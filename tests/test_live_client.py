"""LivePayPal request shapes, checked against a fake PayPal served by httpx.MockTransport.
These tests prove what we send; they do not prove PayPal accepts it (see README, sandbox checklist)."""
import json
from datetime import date

import httpx
import pytest

from app.paypal import LivePayPal, PayPalError, build_invoice_payload

SPEC = dict(invoice_number="MD-1-BAL", recipient_name="Sara Ali", recipient_email="sara@example.com", currency="USD",
            due_date=date(2026, 11, 1), note="Final invoice",
            items=[dict(name="Balance", quantity=1, unit_amount=463.13, discount_pct=5)])


def fake(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content and req.headers.get("content-type", "").startswith("application/json") else None
        calls.append((req.method, req.url.path, dict(req.headers), body))
        p = req.url.path
        if p == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3000})
        if p == "/v2/invoicing/invoices" and req.method == "POST":
            return httpx.Response(201, json={"rel": "self", "href": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-REAL-1", "method": "GET"})
        if p.endswith("/send"):
            return httpx.Response(202, json={})
        if p.endswith("/generate-qr-code"):
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        if p.endswith("/payments"):
            return httpx.Response(200, json={"payment_id": "PAY-1"})
        if p.endswith("/verify-webhook-signature"):
            return httpx.Response(200, json={"verification_status": "SUCCESS" if body["transmission_id"] == "good" else "FAILURE"})
        if p == "/v2/invoicing/invoices/BAD":
            return httpx.Response(422, json={"name": "UNPROCESSABLE"}, headers={"paypal-debug-id": "dbg123"})
        if req.method == "GET":
            return httpx.Response(200, json={"status": "SENT", "detail": {"metadata": {"recipient_view_url": "https://sandbox.paypal.com/invoice/p/#X"}}})
        return httpx.Response(200, json={})
    return handler


@pytest.fixture()
def live():
    calls = []
    c = LivePayPal("id", "secret", "https://api-m.sandbox.paypal.com", "WH-ID", transport=httpx.MockTransport(fake(calls)))
    return c, calls


def test_create_and_send(live):
    c, calls = live
    out = c.create_and_send_invoice(SPEC, request_id="job-1")
    assert out == {"id": "INV2-REAL-1", "url": "https://sandbox.paypal.com/invoice/p/#X", "status": "SENT"}
    create = [x for x in calls if x[1] == "/v2/invoicing/invoices" and x[0] == "POST"][0]
    assert create[2]["authorization"] == "Bearer tok" and create[2]["paypal-request-id"] == "job-1"
    send = [x for x in calls if x[1].endswith("/send")][0]
    assert send[3] == {"send_to_recipient": True, "send_to_invoicer": False}
    assert send[2]["paypal-request-id"] == "job-1-send"


def test_payload_shape():
    p = build_invoice_payload(SPEC)
    assert p["detail"]["payment_term"] == {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": "2026-11-01"}
    assert p["detail"]["currency_code"] == "USD"
    assert p["primary_recipients"][0]["billing_info"]["email_address"] == "sara@example.com"
    assert p["primary_recipients"][0]["billing_info"]["name"] == {"given_name": "Sara", "surname": "Ali"}
    item = p["items"][0]
    assert item["unit_amount"] == {"currency_code": "USD", "value": "463.13"} and item["discount"] == {"percent": "5"}
    assert "discount" not in build_invoice_payload(dict(SPEC, items=[dict(name="x", quantity=1, unit_amount=1)]))["items"][0]


def test_token_is_cached(live):
    c, calls = live
    c.get_status("A"); c.get_status("B")
    assert sum(1 for x in calls if x[1] == "/v1/oauth2/token") == 1


def test_qr_cash_payment_and_remind(live):
    c, calls = live
    assert c.qr_code("INV").startswith("data:image/png;base64,")
    c.record_payment("INV", 100.0, "USD")
    pay = [x for x in calls if x[1].endswith("/payments")][0]
    assert pay[3]["method"] == "CASH" and pay[3]["amount"] == {"currency_code": "USD", "value": "100.00"}
    c.remind("INV", "Reminder", "Please pay")
    assert [x for x in calls if x[1].endswith("/remind")][0][3]["subject"] == "Reminder"


def test_errors_carry_debug_id(live):
    c, _ = live
    with pytest.raises(PayPalError) as e:
        c.get_status("BAD")
    assert e.value.status == 422 and e.value.debug_id == "dbg123"


def test_webhook_verification(live):
    c, calls = live
    hdr = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "u", "PAYPAL-TRANSMISSION-ID": "good",
           "PAYPAL-TRANSMISSION-SIG": "s", "PAYPAL-TRANSMISSION-TIME": "t"}
    assert c.verify_webhook(hdr, {"id": "e"}) is True
    assert c.verify_webhook(dict(hdr, **{"PAYPAL-TRANSMISSION-ID": "bad"}), {"id": "e"}) is False
    assert c.verify_webhook({}, {"id": "e"}) is False
    assert LivePayPal("i", "s", "https://x", "").verify_webhook(hdr, {}) is False
