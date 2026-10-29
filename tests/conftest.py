import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SCHEDULER_INTERVAL_SECONDS"] = "0"
for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "ANTHROPIC_API_KEY", "ADMIN_KEY", "PAYPAL_MODE"):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402

from app import db, workflow  # noqa: E402
from app.paypal import MockPayPal  # noqa: E402

FULL_MSG = ("Hi, I'm Sara Ali, sara@example.com. 3 room apartment, 4th floor no elevator, "
            "moving to a ground floor place 20 km away.")


@pytest.fixture()
def mock_pp(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    mp = MockPayPal()
    workflow.set_paypal(mp)
    db.connect(str(tmp_path / "t.db"))
    yield mp
    workflow.set_paypal(None)


@pytest.fixture()
def client(mock_pp):
    from app.main import app
    with TestClient(app) as c:
        workflow.set_paypal(mock_pp)
        yield c


@pytest.fixture()
def job_id(client):
    r = client.post("/api/chat", json={"message": FULL_MSG}).json()
    assert r["job_id"], r
    return r["job_id"]


def pay(client, mock_pp, invoice_id, event_id="WH-1"):
    ev = mock_pp.pay(invoice_id)
    ev["id"] = event_id
    return client.post("/api/paypal/webhook", json=ev, headers={"x-mock-secret": "movedesk-mock"})


def booked(client, mock_pp, job_id):
    client.post(f"/api/jobs/{job_id}/approve")
    j = client.get(f"/api/jobs/{job_id}").json()
    assert pay(client, mock_pp, j["deposit_invoice"], f"dep-{job_id}").json()["result"] == "processed"
    return client.get(f"/api/jobs/{job_id}").json()
