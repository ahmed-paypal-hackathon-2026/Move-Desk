"""Copilot loop with a scripted fake Claude, offline answers, manual reminders, customer status, safety guards."""
import copy
from types import SimpleNamespace as NS

import pytest

from app import copilot, db
from conftest import FULL_MSG, booked


class FakeClaude:
    """Plays back a script of responses and records what the copilot sent."""
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(copy.deepcopy(kw))
        return self.script.pop(0)


def tool(name, args, id_="t1"):
    return NS(type="tool_use", id=id_, name=name, input=args)


def text(t):
    return NS(type="text", text=t)


@pytest.fixture()
def fake(client):
    def install(script):
        f = FakeClaude(script)
        copilot.set_client(f)
        return f
    yield install
    copilot.set_client(None)


def test_copilot_uses_tools_and_only_proposes(client, fake, mock_pp, job_id):
    f = fake([
        NS(stop_reason="tool_use", content=[tool("list_jobs", {"status": "PENDING_APPROVAL"})]),
        NS(stop_reason="tool_use", content=[tool("propose_action", {"action": "approve", "job_id": job_id,
                                                                     "reason": "Looks complete"}, "t2")]),
        NS(stop_reason="end_turn", content=[text(f"{job_id} is waiting. I proposed approving it.")]),
    ])
    r = client.post("/api/copilot", json={"message": "what needs my approval?"}).json()
    assert r["source"] == "claude" and r["tools_used"] == ["list_jobs", "propose_action"]
    assert r["proposals"][0]["action"] == "approve" and r["proposals"][0]["job_id"] == job_id
    # proposing changed nothing and created no invoice
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "PENDING_APPROVAL" and mock_pp.invoices == {}
    # the tool result really went back to Claude, with the job in it
    tool_results = f.calls[1]["messages"][-1]["content"]
    assert tool_results[0]["type"] == "tool_result" and job_id in tool_results[0]["content"]
    # the model cannot see any tool that changes state
    names = {t["name"] for t in f.calls[0]["tools"]}
    assert names == {"list_jobs", "get_job", "cash_summary", "draft_reminder", "propose_action"}


def test_invalid_proposals_are_rejected(client, fake, job_id):
    fake([
        NS(stop_reason="tool_use", content=[tool("propose_action", {"action": "record_cash", "job_id": job_id, "reason": "x"}),
                                             tool("propose_action", {"action": "approve", "job_id": "NOPE", "reason": "x"}, "t2"),
                                             tool("propose_action", {"action": "refund_everything", "job_id": job_id, "reason": "x"}, "t3")]),
        NS(stop_reason="end_turn", content=[text("done")]),
    ])
    r = client.post("/api/copilot", json={"message": "do things"}).json()
    assert r["proposals"] == []


def test_copilot_loop_is_bounded(client, fake):
    fake([NS(stop_reason="tool_use", content=[tool("cash_summary", {}, f"t{i}")]) for i in range(copilot.MAX_ROUNDS)])
    r = client.post("/api/copilot", json={"message": "loop"}).json()
    assert "could not finish" in r["answer"]


def test_claude_failure_falls_back_to_offline(client, job_id):
    class Broken:
        messages = None
    b = Broken(); b.messages = b
    b.create = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    copilot.set_client(b)
    try:
        r = client.post("/api/copilot", json={"message": "what needs approval"}).json()
    finally:
        copilot.set_client(None)
    assert r["source"] == "offline" and "unavailable" in r["note"] and r["proposals"][0]["job_id"] == job_id


def test_offline_answers(client, mock_pp, job_id):
    a = client.post("/api/copilot", json={"message": "anything waiting for approval?"}).json()
    assert a["source"] == "offline" and job_id in a["answer"] and a["proposals"][0]["action"] == "approve"
    j = booked(client, mock_pp, job_id)
    client.post(f"/api/jobs/{job_id}/complete", json={"actual_hours": 3})
    owe = client.post("/api/copilot", json={"message": "who owes me money?"}).json()
    assert job_id in owe["answer"] and "final invoice" in owe["answer"]
    late = client.post("/api/copilot", json={"message": "who should I chase?"}).json()
    assert late["proposals"][0]["action"] == "send_reminder"
    assert client.post("/api/copilot", json={"message": "summary"}).json()["answer"].startswith("1 jobs")


def test_confirmed_reminder_goes_through_paypal(client, mock_pp, job_id):
    booked(client, mock_pp, job_id)
    client.post(f"/api/jobs/{job_id}/complete", json={"actual_hours": 3})
    r = client.post(f"/api/jobs/{job_id}/remind", json={"tone": "firm"})
    assert r.status_code == 200 and r.json()["reminders"][-1]["tone"] == "firm"
    assert ("remind", r.json()["balance_invoice"]) in mock_pp.calls
    assert client.post(f"/api/jobs/{job_id}/remind", json={"tone": "rude"}).status_code == 422


def test_reminder_refused_without_open_balance(client, job_id):
    assert client.post(f"/api/jobs/{job_id}/remind", json={"tone": "friendly"}).status_code == 409


def test_customer_status_follows_the_job(client, mock_pp):
    r = client.post("/api/chat", json={"message": FULL_MSG}).json()
    sid, jid = r["session_id"], r["job_id"]
    st = client.get(f"/api/chat/{sid}/status").json()
    assert st["status"] == "PENDING_APPROVAL" and st["pay"] is None and st["total"] is None
    client.post(f"/api/jobs/{jid}/approve")
    st = client.get(f"/api/chat/{sid}/status").json()
    assert st["pay"]["label"] == "Pay deposit" and st["pay"]["url"] and st["pay"]["qr"].startswith("data:")
    # nothing private leaks to the customer view
    assert "estimate" not in st and "email" not in st and "events" not in st
    assert client.get("/api/chat/unknown-session/status").status_code == 404


def test_real_paypal_requires_admin_key(client, mock_pp, monkeypatch):
    monkeypatch.setattr(mock_pp, "mode", "sandbox")
    assert client.get("/api/jobs").status_code == 503
    monkeypatch.setenv("ADMIN_KEY", "k")
    assert client.get("/api/jobs", headers={"x-admin-key": "k"}).status_code == 200


def test_seed_covers_every_stage(client):
    ids = client.post("/api/demo/seed").json()["jobs"]
    assert len(ids) == 5
    statuses = {client.get(f"/api/jobs/{i}").json()["status"] for i in ids}
    assert statuses == {"PENDING_APPROVAL", "BOOKED", "BALANCE_SENT", "PAID"}
