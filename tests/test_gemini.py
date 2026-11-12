"""Gemini adapter (app/llm.py) against a fake Gemini server served by httpx.MockTransport.
These prove what we send and how we read replies; they do not prove Google accepts it (run once with a real key)."""
import json

import httpx
import pytest

from app import config, copilot, intake, llm
from conftest import FULL_MSG


class FakeGemini:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append((str(req.url), dict(req.headers), json.loads(req.content)))
        return self.replies.pop(0)


def call(name, args, sig=None):
    part = {"functionCall": {"name": name, "args": args}}
    if sig:
        part["thoughtSignature"] = sig
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [part]}}]})


def text(t):
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": t}]}}]})


def gem(replies):
    fake = FakeGemini(replies)
    return llm.GeminiClient("test-key", transport=httpx.MockTransport(fake)), fake


def test_provider_selection(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config.ai_provider() == ""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert config.ai_provider() == "claude"
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert config.ai_provider() == "gemini" and config.ai_label() == "Gemini"
    assert config.ai_model() == "gemini-flash-lite-latest"


def test_forced_tool_call_request_shape():
    c, fake = gem([call("record_job_details", {"rooms": 3})])
    r = c.messages.create(model="gemini-x", max_tokens=600, system="SYS", tools=[intake._tool_schema()],
                          tool_choice={"type": "tool", "name": "record_job_details"},
                          messages=[{"role": "user", "content": "hello"}])
    url, headers, body = fake.requests[0]
    assert url.endswith("/v1beta/models/gemini-x:generateContent") and headers["x-goog-api-key"] == "test-key"
    assert body["systemInstruction"] == {"parts": [{"text": "SYS"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["record_job_details"]}}
    decl = body["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == "record_job_details"
    flat = json.dumps(decl)
    assert '"title"' not in flat and '"anyOf"' not in flat
    assert r.stop_reason == "tool_use" and r.content[0].name == "record_job_details" and r.content[0].input == {"rooms": 3}


def test_no_argument_tool_omits_parameters():
    decl = llm._declaration({"name": "cash_summary", "description": "x", "input_schema": {"type": "object", "properties": {}}})
    assert "parameters" not in decl


def test_tool_result_round_trip_keeps_thought_signature():
    c, fake = gem([call("cash_summary", {}, sig="SIG123"), text("All good.")])
    first = c.messages.create(model="m", max_tokens=100, messages=[{"role": "user", "content": "q"}],
                              tools=[{"name": "cash_summary", "description": "", "input_schema": {"type": "object", "properties": {}}}])
    tid = first.content[0].id
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": "cash_summary", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": json.dumps({"jobs": 2})}]}]
    second = c.messages.create(model="m", max_tokens=100, messages=msgs)
    sent = fake.requests[1][2]["contents"]
    assert sent[1] == {"role": "model", "parts": [{"functionCall": {"name": "cash_summary", "args": {}}, "thoughtSignature": "SIG123"}]}
    assert sent[2] == {"role": "user", "parts": [{"functionResponse": {"name": "cash_summary", "response": {"result": {"jobs": 2}}}}]}
    assert second.stop_reason == "end_turn" and second.content[0].text == "All good."


def test_errors_raise():
    c, _ = gem([httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})])
    with pytest.raises(llm.LLMError):
        c.messages.create(model="m", max_tokens=10, messages=[{"role": "user", "content": "q"}])


def test_intake_uses_gemini_and_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    c, _ = gem([call("record_job_details", {"move_type": "apartment", "rooms": 3, "floors_origin": 4,
                                            "elevator_origin": 0, "floors_dest": 0, "distance_km": 20,
                                            "customer_name": "Sara Ali", "customer_email": "sara@example.com"})])
    monkeypatch.setattr(llm, "client", lambda: c)
    ex = intake.extract(FULL_MSG)
    assert ex.source == "gemini" and ex.fields["rooms"] == 3 and ex.name == "Sara Ali"
    broken, _ = gem([httpx.Response(500, text="down")])
    monkeypatch.setattr(llm, "client", lambda: broken)
    ex2 = intake.extract(FULL_MSG)
    assert ex2.source.startswith("rules") and ex2.fields["rooms"] == 3


def test_copilot_runs_on_gemini(client, mock_pp, job_id, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    c, fake = gem([call("list_jobs", {"status": "PENDING_APPROVAL"}),
                   call("propose_action", {"action": "approve", "job_id": job_id, "reason": "complete"}),
                   text(f"{job_id} is waiting; I proposed approving it.")])
    copilot.set_client(c)
    try:
        r = client.post("/api/copilot", json={"message": "what needs approval?"}).json()
    finally:
        copilot.set_client(None)
    assert r["source"] == "ai" and r["provider"] == "Gemini"
    assert r["proposals"][0]["job_id"] == job_id and mock_pp.invoices == {}
    assert job_id in json.dumps(fake.requests[1][2]["contents"][-1])  # tool result went back to Gemini
