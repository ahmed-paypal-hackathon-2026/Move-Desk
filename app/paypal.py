"""PayPal Invoicing v2 clients behind one interface.

  LivePayPal  talks to the PayPal REST API (sandbox by default) with httpx
  MockPayPal  in-process simulator used when no credentials are set, for demos and tests

Both return the same shapes, so the workflow code never knows which one it is using.
Every write carries a PayPal-Request-Id so retries cannot create duplicate invoices."""
import base64
import itertools
import time
import uuid
from datetime import date
from typing import Optional

import httpx
import segno

from . import config


class PayPalError(Exception):
    def __init__(self, status: int, body: str, debug_id: str = ""):
        super().__init__(f"PayPal {status} {debug_id}: {body[:300]}")
        self.status = status
        self.body = body
        self.debug_id = debug_id


def _split_name(full: str) -> tuple:
    parts = (full or "Customer").strip().split(" ", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _val(x: float) -> str:
    return f"{x:.2f}"


def build_invoice_payload(spec: dict) -> dict:
    """spec: invoice_number, recipient_name, recipient_email, note, currency, due_date (date),
    items [{name, quantity, unit_amount, discount_pct?}]"""
    given, surname = _split_name(spec["recipient_name"])
    cur = spec["currency"]
    items = []
    for it in spec["items"]:
        row = {"name": it["name"][:200], "quantity": str(it["quantity"]),
               "unit_amount": {"currency_code": cur, "value": _val(it["unit_amount"])}}
        if it.get("discount_pct"):
            row["discount"] = {"percent": str(it["discount_pct"])}
        items.append(row)
    billing = {"email_address": spec["recipient_email"], "name": {"given_name": given, "surname": surname}}
    return {
        "detail": {
            "invoice_number": spec["invoice_number"],
            "invoice_date": date.today().isoformat(),
            "currency_code": cur,
            "note": spec["note"][:4000],
            "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": spec["due_date"].isoformat()},
        },
        "invoicer": {"name": {"business_name": config.company()["name"]}},
        "primary_recipients": [{"billing_info": billing}],
        "items": items,
        "configuration": {"allow_tip": False, "partial_payment": {"allow_partial_payment": False}},
    }


def qr_data_uri(body: bytes) -> str:
    """PayPal documents the QR response both as a Base64-encoded PNG and as a JSON body holding the PNG.
    Accept raw PNG bytes, Base64 text, or JSON with a Base64 string field."""
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "data:image/png;base64," + base64.b64encode(body).decode()
    text = body.decode("ascii", "ignore").strip()
    if text.startswith("{"):
        import json as _json
        obj = _json.loads(text)
        text = next((v for v in obj.values() if isinstance(v, str) and len(v) > 100), "")
    text = text.strip().strip('"').split(",")[-1]  # tolerate a data: prefix
    base64.b64decode(text, validate=False)  # raises if it is not Base64
    return "data:image/png;base64," + text


class LivePayPal:
    mode = "sandbox"

    def __init__(self, client_id: str, secret: str, base_url: str, webhook_id: str = "",
                 transport: Optional[httpx.BaseTransport] = None):
        self.client_id, self.secret, self.webhook_id = client_id, secret, webhook_id
        self.http = httpx.Client(base_url=base_url, timeout=30.0, transport=transport)
        self._token, self._expires = "", 0.0

    # ---- plumbing ----
    def _auth(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        r = self.http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                           auth=(self.client_id, self.secret), headers={"Accept": "application/json"})
        if r.status_code != 200:
            raise PayPalError(r.status_code, r.text, r.headers.get("paypal-debug-id", ""))
        body = r.json()
        self._token, self._expires = body["access_token"], time.time() + int(body.get("expires_in", 3000))
        return self._token

    def _req(self, method: str, path: str, json: Optional[dict] = None, request_id: Optional[str] = None,
             raw: bool = False):
        headers = {"Authorization": f"Bearer {self._auth()}", "Content-Type": "application/json"}
        if method != "GET":
            headers["PayPal-Request-Id"] = request_id or str(uuid.uuid4())
        r = self.http.request(method, path, json=json, headers=headers)
        if r.status_code >= 400:
            raise PayPalError(r.status_code, r.text, r.headers.get("paypal-debug-id", ""))
        if raw:
            return r
        return r.json() if r.content else {}

    # ---- invoices ----
    def create_and_send_invoice(self, spec: dict, request_id: Optional[str] = None) -> dict:
        created = self._req("POST", "/v2/invoicing/invoices", build_invoice_payload(spec), request_id)
        href = created.get("href", "")
        invoice_id = created.get("id") or href.rstrip("/").split("/")[-1]
        self._req("POST", f"/v2/invoicing/invoices/{invoice_id}/send",
                  {"send_to_recipient": True, "send_to_invoicer": False},
                  f"{request_id}-send" if request_id else None)
        info = self._req("GET", f"/v2/invoicing/invoices/{invoice_id}")
        url = (info.get("detail", {}).get("metadata", {}) or {}).get("recipient_view_url", "")
        return {"id": invoice_id, "url": url, "status": info.get("status", "SENT")}

    def get_status(self, invoice_id: str) -> str:
        return self._req("GET", f"/v2/invoicing/invoices/{invoice_id}").get("status", "UNKNOWN")

    def qr_code(self, invoice_id: str, url: str = "") -> str:
        r = self._req("POST", f"/v2/invoicing/invoices/{invoice_id}/generate-qr-code",
                      {"width": 300, "height": 300}, raw=True)
        return qr_data_uri(r.content)

    def record_payment(self, invoice_id: str, amount: float, currency: str, method: str = "CASH") -> dict:
        return self._req("POST", f"/v2/invoicing/invoices/{invoice_id}/payments",
                         {"method": method, "payment_date": date.today().isoformat(),
                          "amount": {"currency_code": currency, "value": _val(amount)}})

    def remind(self, invoice_id: str, subject: str, note: str) -> dict:
        return self._req("POST", f"/v2/invoicing/invoices/{invoice_id}/remind",
                         {"subject": subject[:100], "note": note[:4000], "send_to_invoicer": False})

    def cancel(self, invoice_id: str, subject: str, note: str) -> dict:
        return self._req("POST", f"/v2/invoicing/invoices/{invoice_id}/cancel",
                         {"subject": subject[:100], "note": note[:4000], "send_to_invoicer": False,
                          "send_to_recipient": True, "additional_recipients": []})

    # ---- webhooks ----
    def verify_webhook(self, headers: dict, event: dict) -> bool:
        """Ask PayPal to verify the signature of a webhook delivery."""
        if not self.webhook_id:
            return False
        h = {k.lower(): v for k, v in headers.items()}
        try:
            body = {
                "auth_algo": h["paypal-auth-algo"], "cert_url": h["paypal-cert-url"],
                "transmission_id": h["paypal-transmission-id"], "transmission_sig": h["paypal-transmission-sig"],
                "transmission_time": h["paypal-transmission-time"], "webhook_id": self.webhook_id,
                "webhook_event": event,
            }
        except KeyError:
            return False
        res = self._req("POST", "/v1/notifications/verify-webhook-signature", body)
        return res.get("verification_status") == "SUCCESS"


class MockPayPal:
    """Simulator with the same interface. Invoices live in memory; `pay` plays the part of the customer."""
    mode = "mock"

    def __init__(self):
        self.invoices: dict = {}
        self._ids = itertools.count(1)
        self.calls: list = []

    def _inv(self, invoice_id: str) -> dict:
        if invoice_id not in self.invoices:
            raise PayPalError(404, "RESOURCE_NOT_FOUND")
        return self.invoices[invoice_id]

    def create_and_send_invoice(self, spec: dict, request_id: Optional[str] = None) -> dict:
        if request_id:  # idempotency, like PayPal-Request-Id
            for inv in self.invoices.values():
                if inv.get("request_id") == request_id:
                    return {"id": inv["id"], "url": inv["url"], "status": inv["status"]}
        invoice_id = f"INV2-MOCK-{next(self._ids):04d}"
        total = 0.0
        for it in spec["items"]:
            line = float(it["quantity"]) * it["unit_amount"]
            if it.get("discount_pct"):
                line *= 1 - it["discount_pct"] / 100
            total += line
        inv = {"id": invoice_id, "status": "SENT", "spec": spec, "total": round(total, 2), "request_id": request_id,
               "url": f"{config.public_base_url()}/mock-pay/{invoice_id}", "payments": [], "reminders": []}
        self.invoices[invoice_id] = inv
        self.calls.append(("create", invoice_id))
        return {"id": invoice_id, "url": inv["url"], "status": "SENT"}

    def get_status(self, invoice_id: str) -> str:
        return self._inv(invoice_id)["status"]

    def qr_code(self, invoice_id: str, url: str = "") -> str:
        target = url or self._inv(invoice_id)["url"]
        return segno.make(target).svg_data_uri(scale=6, border=2)

    def record_payment(self, invoice_id: str, amount: float, currency: str, method: str = "CASH") -> dict:
        inv = self._inv(invoice_id)
        inv["payments"].append({"method": method, "amount": amount})
        inv["status"] = "MARKED_AS_PAID"
        self.calls.append(("record_payment", invoice_id))
        return {"payment_id": f"PAY-MOCK-{len(inv['payments'])}"}

    def remind(self, invoice_id: str, subject: str, note: str) -> dict:
        inv = self._inv(invoice_id)
        inv["reminders"].append({"subject": subject, "note": note})
        self.calls.append(("remind", invoice_id))
        return {}

    def cancel(self, invoice_id: str, subject: str, note: str) -> dict:
        self._inv(invoice_id)["status"] = "CANCELLED"
        self.calls.append(("cancel", invoice_id))
        return {}

    def verify_webhook(self, headers: dict, event: dict) -> bool:
        h = {k.lower(): v for k, v in headers.items()}
        return h.get("x-mock-secret") == config.env("MOCK_WEBHOOK_SECRET", "movedesk-mock")

    # ---- simulator-only: the customer pays ----
    def pay(self, invoice_id: str) -> dict:
        inv = self._inv(invoice_id)
        if inv["status"] not in ("SENT", "UNPAID"):
            raise PayPalError(409, f"invoice is {inv['status']}")
        inv["status"] = "PAID"
        self.calls.append(("pay", invoice_id))
        return {"id": f"WH-MOCK-{uuid.uuid4().hex[:12]}", "event_type": "INVOICING.INVOICE.PAID",
                "resource": {"id": invoice_id, "status": "PAID"}}  # documented shape: invoice id at resource.id


def make_client():
    if config.paypal_mode() == "sandbox":
        return LivePayPal(config.env("PAYPAL_CLIENT_ID"), config.env("PAYPAL_CLIENT_SECRET"),
                          config.paypal_base_url(), config.env("PAYPAL_WEBHOOK_ID"))
    return MockPayPal()
