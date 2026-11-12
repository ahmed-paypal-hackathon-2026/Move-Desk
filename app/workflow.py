"""The quote-to-cash workflow.

  chat -> PENDING_APPROVAL -> (owner approves) DEPOSIT_SENT -> (deposit PAID) BOOKED
       -> (owner completes job) BALANCE_SENT -> (balance PAID) PAID        REJECTED from PENDING_APPROVAL

Rules that never bend:
  * Prices come from pricing.py, never from the language model.
  * A human approves every quote before any invoice exists.
  * A job is marked paid only when PayPal itself says the invoice is paid (we GET it), never because
    a webhook body or a request claimed so."""
import json
import re
import uuid
from datetime import timedelta
from typing import Optional

from . import config, db, intake, ml, pricing
from .paypal import PayPalError, make_client

PAID_STATUSES = {"PAID", "MARKED_AS_PAID"}
_client = None


class WorkflowError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def paypal():
    global _client
    if _client is None:
        _client = make_client()
    return _client


def set_paypal(client) -> None:
    global _client
    _client = client


def card() -> pricing.RateCard:
    return pricing.RateCard.from_env()


def _job(job_id: str) -> dict:
    j = db.get("jobs", job_id)
    if not j:
        raise WorkflowError("job not found", 404)
    return j


# ---------------------------------------------------------------- chat intake

REPLIES = {
    "understood": {"en": "Thanks, I have noted: {facts}.", "ar": "شكراً، سجلت: {facts}."},
    "need": {"en": "To finish your quote I still need:", "ar": "لإكمال عرض السعر أحتاج إلى:"},
    "greet": {"en": "Hi, I am the {co} assistant. Tell me about your move: type of home, rooms, floors, "
                    "elevators and distance.",
              "ar": "أهلاً، أنا مساعد {co}. أخبرني عن النقل: نوع السكن وعدد الغرف والأدوار والمصاعد والمسافة."},
    "done": {"en": "Your estimate is between {lo} and {hi} {cur} (most likely {mid} {cur}). {co} will review it "
                   "and send a deposit invoice to {email} once confirmed. Your reference is {job}.",
             "ar": "التقدير بين {lo} و{hi} {cur} (الأرجح {mid} {cur}). سيراجعه فريق {co} ويرسل فاتورة العربون إلى "
                   "{email} بعد التأكيد. رقم طلبك {job}."},
    "waiting": {"en": "Your request {job} is with the {co} team. You will get the invoice by email once it is approved.",
                "ar": "طلبك {job} لدى فريق {co}. ستصلك الفاتورة بالبريد بعد الموافقة."},
}

def _facts(draft: dict) -> str:
    """A short, readable echo of what we understood, so the customer can correct mistakes."""
    d = intake.normalize(draft)
    out = []
    if d.get("move_type"):
        out.append(d["move_type"] + (f", {d['rooms']} room{'s' if d['rooms'] != 1 else ''}" if d.get("rooms") else ""))
    for side, label in (("origin", "pickup"), ("dest", "new place")):
        fl, el = d.get(f"floors_{side}"), d.get(f"elevator_{side}")
        if fl is not None:
            out.append(f"{label}: " + ("ground floor" if fl == 0 else f"floor {fl}, " + ("elevator" if el else "no elevator")
                                      if el is not None else f"floor {fl}"))
    if d.get("distance_km") is not None:
        out.append(f"{d['distance_km']:g} km")
    return "; ".join(out)


def _t(key: str, lang: str, **kw) -> str:
    lang = lang if lang in ("en", "ar") else "en"
    return REPLIES[key][lang].format(**kw)


def chat(session_id: Optional[str], message: str) -> dict:
    s = db.get("sessions", session_id) if session_id else None
    co = config.company()["name"]
    if s is None:
        session_id = uuid.uuid4().hex[:16]
        s = {"id": session_id, "draft": {}, "contact": {}, "messages": [], "language": "en",
             "job_id": None, "created": db.iso()}
        db.execute("INSERT INTO sessions(id,draft,contact,messages,language,job_id,created) VALUES(?,?,?,?,?,?,?)",
                   (session_id, "{}", "{}", "[]", "en", None, s["created"]))
    else:
        for k in ("draft", "contact", "messages"):
            s[k] = json.loads(s[k]) if isinstance(s[k], str) else s[k]

    def save(reply: str) -> dict:
        s["messages"] += [{"role": "user", "text": message}, {"role": "assistant", "text": reply}]
        db.execute("UPDATE sessions SET draft=?,contact=?,messages=?,language=?,job_id=? WHERE id=?",
                   (json.dumps(s["draft"]), json.dumps(s["contact"]), json.dumps(s["messages"][-40:]),
                    s["language"], s["job_id"], s["id"]))
        return {"session_id": s["id"], "reply": reply, "complete": bool(s["job_id"]), "job_id": s["job_id"]}

    if s["job_id"]:
        return save(_t("waiting", s["language"], job=s["job_id"], co=co))

    ex = intake.extract(message, s["draft"])
    s["language"] = ex.language or s["language"]
    for k, v in ex.fields.items():
        if v is not None:
            s["draft"][k] = v
    if ex.name:
        s["contact"]["name"] = ex.name
    if ex.email:
        s["contact"]["email"] = ex.email
    lang = s["language"]

    if not intake.is_complete(s["draft"], s["contact"]):
        qs = intake.questions(s["draft"], s["contact"], lang)
        facts = _facts(s["draft"])
        parts = []
        if facts and ex.fields:
            parts.append(_t("understood", lang, facts=facts))
        if not facts:
            parts.append(_t("greet", lang, co=co))
        parts.append(_t("need", lang))
        parts.append(" ".join(f"({i + 1}) {q}" for i, q in enumerate(qs)))
        return save(" ".join(parts))

    job = create_job(s)
    s["job_id"] = job["id"]
    q = job["quote"]
    rng = job["estimate"]["price_range"]
    reply = _t("done", lang, lo=f"{rng['low']:.0f}", hi=f"{rng['high']:.0f}", mid=f"{q['total']:.0f}",
               cur=q["currency"], co=co, email=s["contact"]["email"], job=job["id"])
    return save(reply)


def create_job(session: dict) -> dict:
    now = db.now()
    full, assumptions = intake.with_defaults(session["draft"], now.month)
    est = ml.predict_duration(full)
    c = card()
    quote = pricing.build_quote(full, est["hours"], c)
    est["price_range"] = pricing.quote_range(full, est["p10"], est["p90"], c)
    est["drivers"] = ml.explain(full)
    job_id = "MD-" + uuid.uuid4().hex[:6].upper()
    db.insert("jobs", {
        "id": job_id, "session_id": session["id"], "customer_name": session["contact"]["name"],
        "customer_email": session["contact"]["email"], "language": session["language"], "job": full,
        "estimate": est, "quote": quote, "assumptions": assumptions, "status": "PENDING_APPROVAL",
        "balance_discount_pct": 0, "overtime_amount": 0.0, "reminders": [], "created": db.iso(), "updated": db.iso(),
    })
    db.log(job_id, "quote_created", {"total": quote["total"], "hours": est["hours"], "source": "chat"})
    return db.get("jobs", job_id)


# ---------------------------------------------------------------- owner actions

def _touch(job_id: str, **fields) -> dict:
    db.update("jobs", job_id, {**fields, "updated": db.iso()})
    return db.get("jobs", job_id)


def approve(job_id: str) -> dict:
    j = _job(job_id)
    if j["status"] != "PENDING_APPROVAL":
        raise WorkflowError(f"cannot approve a job that is {j['status']}")
    q, c = j["quote"], card()
    due = (db.now() + timedelta(days=c.early_pay_days)).date()
    spec = {"invoice_number": f"{job_id}-DEP", "recipient_name": j["customer_name"],
            "recipient_email": j["customer_email"], "currency": q["currency"], "due_date": due,
            "note": f"Deposit to reserve your move with {config.company()['name']}. Reference {job_id}.",
            "items": [{"name": f"Moving deposit ({int(q['deposit_pct'] * 100)}%)", "quantity": 1,
                       "unit_amount": q["deposit"]}]}
    try:
        inv = paypal().create_and_send_invoice(spec, request_id=f"{job_id}-deposit")
        qr = paypal().qr_code(inv["id"], inv["url"])
    except PayPalError as e:
        db.log(job_id, "paypal_error", {"step": "deposit", "error": str(e)})
        raise WorkflowError(f"PayPal could not create the deposit invoice: {e}", 502)
    db.log(job_id, "deposit_sent", {"invoice": inv["id"], "amount": q["deposit"]})
    return _touch(job_id, status="DEPOSIT_SENT", deposit_invoice=inv["id"], deposit_url=inv["url"], deposit_qr=qr)


def reject(job_id: str, reason: str = "") -> dict:
    j = _job(job_id)
    if j["status"] != "PENDING_APPROVAL":
        raise WorkflowError(f"cannot reject a job that is {j['status']}")
    db.log(job_id, "rejected", {"reason": reason})
    return _touch(job_id, status="REJECTED", reject_reason=reason)


def _balance_spec(j: dict, amount: float, overtime: float, discount_pct: int, due, suffix: str) -> dict:
    items = [{"name": "Remaining balance for your move", "quantity": 1, "unit_amount": amount}]
    if overtime > 0:
        items.append({"name": "Extra time beyond the quoted hours", "quantity": 1, "unit_amount": overtime})
    for it in items:
        it["discount_pct"] = discount_pct
    note = f"Final invoice for move {j['id']}."
    if discount_pct:
        note += f" Pay within {card().early_pay_days} days to keep your {discount_pct}% early payment discount."
    return {"invoice_number": f"{j['id']}-{suffix}", "recipient_name": j["customer_name"],
            "recipient_email": j["customer_email"], "currency": j["quote"]["currency"], "due_date": due,
            "note": note, "items": items}


def complete(job_id: str, actual_hours: float, waive_overtime: bool = False) -> dict:
    j = _job(job_id)
    if j["status"] != "BOOKED":
        raise WorkflowError(f"cannot complete a job that is {j['status']} (deposit must be paid first)")
    c, q = card(), j["quote"]
    ot = pricing.overtime(q["billable_hours"], actual_hours, j["job"]["crew_size"], c)
    overtime_amount = 0.0 if waive_overtime else ot["amount"]
    balance = q["balance"]
    discount = c.early_pay_discount_pct
    now = db.now()
    due = (now + timedelta(days=c.payment_terms_days)).date()
    spec = _balance_spec(j, balance, overtime_amount, discount, due, "BAL")
    try:
        inv = paypal().create_and_send_invoice(spec, request_id=f"{job_id}-balance")
        qr = paypal().qr_code(inv["id"], inv["url"])
    except PayPalError as e:
        db.log(job_id, "paypal_error", {"step": "balance", "error": str(e)})
        raise WorkflowError(f"PayPal could not create the balance invoice: {e}", 502)
    feats = ml.payment_features(j, q, discount, c.payment_terms_days, balance + overtime_amount,
                                ot["hours"] > 0, now, j["language"])
    pay = ml.predict_payment(feats)
    db.log(job_id, "balance_sent", {"invoice": inv["id"], "amount": balance + overtime_amount, "discount_pct": discount,
                                   "overtime_hours": ot["hours"], "overtime_waived": waive_overtime, **pay})
    return _touch(job_id, status="BALANCE_SENT", balance_invoice=inv["id"], balance_url=inv["url"], balance_qr=qr,
                  balance_amount=balance + overtime_amount, balance_discount_pct=discount,
                  discount_deadline=db.iso(now + timedelta(days=c.early_pay_days)), balance_sent_at=db.iso(now),
                  balance_due=db.iso(now + timedelta(days=c.payment_terms_days)), actual_hours=actual_hours,
                  overtime_amount=overtime_amount, p_on_time=pay["p_on_time"], exp_days=pay["expected_days"],
                  reminders=[])


# ---------------------------------------------------------------- money coming in

def _open_invoice(j: dict) -> Optional[str]:
    return {"DEPOSIT_SENT": j["deposit_invoice"], "BALANCE_SENT": j["balance_invoice"]}.get(j["status"])


def _apply_paid(j: dict, method: str) -> dict:
    """Idempotent transition after PayPal has confirmed an invoice as paid."""
    if j["status"] == "DEPOSIT_SENT":
        db.log(j["id"], "deposit_paid", {"method": method})
        return _touch(j["id"], status="BOOKED", deposit_paid_at=db.iso())
    if j["status"] == "BALANCE_SENT":
        db.log(j["id"], "balance_paid", {"method": method})
        return _touch(j["id"], status="PAID", balance_paid_at=db.iso(), paid_method=method)
    return j


def _find_by_invoice(invoice_id: str) -> Optional[dict]:
    rows = db.query("SELECT id FROM jobs WHERE deposit_invoice=? OR balance_invoice=?", (invoice_id, invoice_id))
    return db.get("jobs", rows[0]["id"]) if rows else None


def handle_paypal_event(event: dict) -> dict:
    event_id = event.get("id") or ""
    if event_id and db.query("SELECT 1 FROM pp_events WHERE event_id=?", (event_id,)):
        return {"result": "duplicate"}
    etype = event.get("event_type", "")
    if etype != "INVOICING.INVOICE.PAID":
        if event_id:
            db.claim_event(event_id)
        return {"result": "ignored", "event_type": etype}
    res = event.get("resource", {})
    # PayPal documents the invoice id at resource.id; older samples nest it under resource.invoice.id
    invoice_id = res.get("id") if str(res.get("id", "")).startswith("INV") else (res.get("invoice") or {}).get("id") or res.get("id") or ""
    j = _find_by_invoice(invoice_id)
    if not j:
        if event_id:
            db.claim_event(event_id)
        old = db.query("SELECT job_id FROM events WHERE kind='discount_expired' AND detail LIKE ?",
                       (f'%"{invoice_id}"%',))
        if old:  # a customer paid the invoice we replaced: the owner must look (refund or credit)
            db.log(old[0]["job_id"], "superseded_invoice_paid", {"invoice": invoice_id})
            return {"result": "superseded", "job_id": old[0]["job_id"]}
        db.log(None, "unknown_invoice_event", {"invoice": invoice_id})
        return {"result": "unknown_invoice"}
    if invoice_id != _open_invoice(j):
        # Already handled, or a superseded (cancelled and reissued) invoice. Nothing to move.
        if event_id:
            db.claim_event(event_id)
        return {"result": "no_change", "status": j["status"]}
    try:
        status = paypal().get_status(invoice_id)
    except PayPalError as e:
        # Do not claim the event: PayPal will retry and we try again.
        raise WorkflowError(f"could not confirm payment with PayPal: {e}", 502)
    if status not in PAID_STATUSES:
        db.log(j["id"], "payment_not_confirmed", {"invoice": invoice_id, "paypal_status": status})
        if event_id:
            db.claim_event(event_id)
        return {"result": "not_confirmed", "paypal_status": status}
    j = _apply_paid(j, "paypal")
    if event_id:
        db.claim_event(event_id)
    return {"result": "processed", "status": j["status"], "job_id": j["id"]}


def record_cash(job_id: str) -> dict:
    """The customer paid the crew directly. Tell PayPal so the invoice closes, then move the job on."""
    j = _job(job_id)
    invoice_id = _open_invoice(j)
    if not invoice_id:
        raise WorkflowError(f"nothing to collect while the job is {j['status']}")
    amount = j["quote"]["deposit"] if j["status"] == "DEPOSIT_SENT" else _amount_due(j)
    try:
        paypal().record_payment(invoice_id, amount, j["quote"]["currency"], "CASH")
        status = paypal().get_status(invoice_id)
    except PayPalError as e:
        raise WorkflowError(f"PayPal could not record the payment: {e}", 502)
    if status not in PAID_STATUSES:
        raise WorkflowError(f"PayPal reports the invoice as {status}, so the job was not changed", 502)
    return _apply_paid(j, "cash")


def _amount_due(j: dict) -> float:
    pct = j["balance_discount_pct"] or 0
    return round(j["balance_amount"] * (1 - pct / 100), 2)


# ---------------------------------------------------------------- time-based automation

def expire_discounts() -> list:
    """After the early-pay deadline, replace the discounted balance invoice with a full-price one."""
    changed = []
    now = db.now()
    for j in db.list_jobs():
        if j["status"] != "BALANCE_SENT" or not j["balance_discount_pct"]:
            continue
        if now <= db.parse(j["discount_deadline"]):
            continue
        old = j["balance_invoice"]
        try:
            if paypal().get_status(old) in PAID_STATUSES:  # paid at the last minute, let the webhook path win
                _apply_paid(j, "paypal")
                continue
            paypal().cancel(old, "Early payment discount ended",
                            "The early payment discount has ended. A new invoice at the full amount follows.")
            ot = j["overtime_amount"] or 0.0
            spec = _balance_spec(j, j["quote"]["balance"], ot, 0, db.parse(j["balance_due"]).date(), "BAL2")
            inv = paypal().create_and_send_invoice(spec, request_id=f"{j['id']}-balance-full")
            qr = paypal().qr_code(inv["id"], inv["url"])
        except PayPalError as e:
            db.log(j["id"], "paypal_error", {"step": "discount_expiry", "error": str(e)})
            continue
        db.log(j["id"], "discount_expired", {"old_invoice": old, "new_invoice": inv["id"]})
        _touch(j["id"], balance_invoice=inv["id"], balance_url=inv["url"], balance_qr=qr, balance_discount_pct=0)
        changed.append(j["id"])
    return changed


def reminder_schedule(p_on_time: float) -> list:
    """Days after sending the balance invoice, with the tone to use. Risky invoices get more, earlier nudges."""
    if p_on_time < 0.5:
        return [(1, "friendly"), (3, "firm"), (6, "final")]
    if p_on_time < 0.8:
        return [(3, "friendly"), (6, "firm")]
    return [(5, "friendly")]


TEMPLATES = {
    "friendly": {"en": "Hi {name}, a friendly reminder that your invoice for move {job} ({amount} {cur}) is open. "
                       "You can pay securely with PayPal using the link on the invoice. Thank you!",
                 "ar": "مرحباً {name}، تذكير لطيف بأن فاتورة النقل {job} ({amount} {cur}) ما زالت مفتوحة. "
                       "يمكنك الدفع بأمان عبر PayPal من رابط الفاتورة. شكراً لك!"},
    "firm": {"en": "Hi {name}, your invoice for move {job} ({amount} {cur}) is still unpaid and is due on {due}. "
                   "Please pay through the PayPal link on the invoice.",
             "ar": "مرحباً {name}، فاتورة النقل {job} ({amount} {cur}) لم تسدد بعد وموعد استحقاقها {due}. "
                   "يرجى الدفع عبر رابط PayPal في الفاتورة."},
    "final": {"en": "Hi {name}, this is the final reminder for invoice {job} ({amount} {cur}), due on {due}. "
                    "Please pay today or contact us if there is a problem.",
              "ar": "مرحباً {name}، هذا آخر تذكير بفاتورة {job} ({amount} {cur}) المستحقة في {due}. "
                    "يرجى الدفع اليوم أو التواصل معنا إذا كانت هناك مشكلة."},
}


def _polish(text: str, tone: str, lang: str) -> str:
    """Optional AI rewrite (AI_POLISH_REMINDERS=1). Falls back to the template if numbers or dates change.
    Off by default so free-tier AI quotas are spent on intake and the copilot."""
    if not config.ai_provider() or config.env("AI_POLISH_REMINDERS") != "1":
        return text
    try:
        from . import llm
        msg = llm.client().messages.create(
            model=config.ai_model(), max_tokens=300,
            system="Rewrite this payment reminder in the same language with a natural, polite tone. Keep every "
                   "number, date and reference exactly as written. Output only the message.",
            messages=[{"role": "user", "content": f"Tone: {tone}\n\n{text}"}])
        out = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        if out and set(re.findall(r"\d+", text)) <= set(re.findall(r"\d+", out)):
            return out
    except Exception:
        pass
    return text


def run_reminders() -> list:
    sent = []
    now = db.now()
    for j in db.list_jobs():
        if j["status"] != "BALANCE_SENT" or not j["balance_sent_at"]:
            continue
        age = (now - db.parse(j["balance_sent_at"])).total_seconds() / 86400
        done = {r["day"] for r in j["reminders"]}
        for day, tone in reminder_schedule(j["p_on_time"] if j["p_on_time"] is not None else 0.8):
            if age < day or day in done:
                continue
            text = reminder_text(j, tone)
            try:
                paypal().remind(j["balance_invoice"], f"Reminder: invoice {j['id']}", text)
            except PayPalError as e:
                db.log(j["id"], "paypal_error", {"step": "reminder", "error": str(e)})
                break
            j["reminders"].append({"day": day, "tone": tone, "sent_at": db.iso(), "text": text})
            _touch(j["id"], reminders=j["reminders"])
            db.log(j["id"], "reminder_sent", {"day": day, "tone": tone})
            sent.append({"job_id": j["id"], "tone": tone})
            break  # at most one reminder per job per run
    return sent


def reminder_text(j: dict, tone: str, polish: bool = True) -> str:
    lang = j["language"] if j["language"] in ("en", "ar") else "en"
    text = TEMPLATES[tone][lang].format(
        name=j["customer_name"].split(" ")[0], job=j["id"], amount=f"{_amount_due(j):.2f}",
        cur=j["quote"]["currency"], due=db.parse(j["balance_due"]).date().isoformat())
    return _polish(text, tone, lang) if polish else text


def send_reminder(job_id: str, tone: str) -> dict:
    """Owner-triggered reminder (for example after the copilot proposed one and the owner confirmed)."""
    if tone not in TEMPLATES:
        raise WorkflowError("tone must be friendly, firm or final", 422)
    j = _job(job_id)
    if j["status"] != "BALANCE_SENT":
        raise WorkflowError(f"reminders are only sent for an open balance invoice, this job is {j['status']}")
    text = reminder_text(j, tone)
    try:
        paypal().remind(j["balance_invoice"], f"Reminder: invoice {j['id']}", text)
    except PayPalError as e:
        raise WorkflowError(f"PayPal could not send the reminder: {e}", 502)
    j["reminders"].append({"day": None, "tone": tone, "sent_at": db.iso(), "text": text, "manual": True})
    db.log(job_id, "reminder_sent", {"tone": tone, "manual": True})
    return _touch(job_id, reminders=j["reminders"])


CUSTOMER_STAGE = {
    "PENDING_APPROVAL": "Your quote is with the team for a final check.",
    "DEPOSIT_SENT": "Your quote is confirmed. Pay the deposit to reserve your date.",
    "BOOKED": "Deposit received. Your move is booked.",
    "BALANCE_SENT": "Thanks for moving with us. Your final invoice is ready.",
    "PAID": "All paid. Thank you!",
    "REJECTED": "Sorry, we cannot take this move. The team will contact you.",
}


def customer_status(session_id: str) -> dict:
    """What the customer may see about their own job. The session id is the only key, so keep this minimal."""
    s = db.get("sessions", session_id)
    if not s or not s.get("job_id"):
        raise WorkflowError("no quote for this chat yet", 404)
    j = _job(s["job_id"])
    q = j["quote"]
    out = {"job_id": j["id"], "status": j["status"], "message": CUSTOMER_STAGE.get(j["status"], ""),
           "currency": q["currency"], "range": j["estimate"]["price_range"], "total": None, "pay": None}
    if j["status"] != "PENDING_APPROVAL" and j["status"] != "REJECTED":
        out["total"] = q["total"]
    if j["status"] == "DEPOSIT_SENT":
        out["pay"] = {"label": "Pay deposit", "amount": q["deposit"], "url": j["deposit_url"], "qr": j["deposit_qr"]}
    elif j["status"] == "BALANCE_SENT":
        pct = j["balance_discount_pct"] or 0
        out["pay"] = {"label": "Pay final invoice", "amount": _amount_due(j), "url": j["balance_url"],
                      "qr": j["balance_qr"], "discount_pct": pct,
                      "discount_until": j["discount_deadline"] if pct else None}
    return out


def tick() -> dict:
    """What the background scheduler runs."""
    return {"discounts_expired": expire_discounts(), "reminders": run_reminders()}


# ---------------------------------------------------------------- numbers for the dashboard

def metrics() -> dict:
    jobs = db.list_jobs()
    by = {}
    for j in jobs:
        by[j["status"]] = by.get(j["status"], 0) + 1
    quoted = sum(j["quote"]["total"] for j in jobs)
    collected = sum((j["quote"]["deposit"] if j["deposit_paid_at"] else 0)
                    + (_amount_due(j) if j["balance_paid_at"] else 0) for j in jobs)
    outstanding = sum(_amount_due(j) for j in jobs if j["status"] == "BALANCE_SENT") \
        + sum(j["quote"]["deposit"] for j in jobs if j["status"] == "DEPOSIT_SENT")
    paid = [j for j in jobs if j["balance_paid_at"] and j["balance_sent_at"]]
    days = [(db.parse(j["balance_paid_at"]) - db.parse(j["balance_sent_at"])).total_seconds() / 86400 for j in paid]
    return {"jobs": len(jobs), "by_status": by, "quoted_total": round(quoted, 2), "collected": round(collected, 2),
            "outstanding": round(outstanding, 2),
            "avg_days_to_pay": round(sum(days) / len(days), 1) if days else None,
            "at_risk": [j["id"] for j in jobs if j["status"] == "BALANCE_SENT" and (j["p_on_time"] or 1) < 0.5],
            "currency": config.company()["currency"], "paypal_mode": paypal().mode}
