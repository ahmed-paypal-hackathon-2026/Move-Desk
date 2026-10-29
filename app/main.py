"""HTTP API and pages.

Public:  POST /api/chat, POST /api/paypal/webhook, GET /api/config, pages
Owner:   everything else under /api (protected by the X-Admin-Key header when ADMIN_KEY is set)
Demo:    /api/demo/* exist only while PayPal runs in simulator mode"""
import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, copilot, db, ml, workflow
from .paypal import PayPalError
from .schemas import AdvanceIn, ChatIn, CompleteIn, CopilotIn, RejectIn, RemindIn

STATIC = Path(__file__).resolve().parent.parent / "static"

SEED_CHATS = [
    ["Hi, I'm Omar Hassan, omar.h@example.com. Moving a studio, 2nd floor no elevator to 1st floor with elevator, 12 km"],
    ["I'm Laila Nasser, laila.n@example.com. 4 room house with 1 flight of stairs to a new apartment on the "
     "5th floor, elevator at the new place, 40 km, we need packing"],
    ["Mona Adel mona.adel@example.com, 2 bedroom apartment, 4th floor no elevator, moving to a ground floor, "
     "18 km, we have a piano"],
    ["Hi, I'm Karim Fathy, karim.f@example.com. 3 bedroom apartment from the 6th floor with elevator to the "
     "2nd floor no elevator, 9 km"],
    ["I'm Nadia Samir, nadia.s@example.com. Office move, 5 rooms, from the ground floor to the 3rd floor with "
     "elevator, 30 km, about 80 boxes"],
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect(config.db_path())
    if config.env("DEMO_SEED") == "1" and workflow.paypal().mode == "mock" and not db.list_jobs():
        _seed()  # a hosted demo opens with sample jobs so judges see something straight away
    task = None
    interval = config.scheduler_interval()
    if interval > 0:
        async def loop():
            while True:
                await asyncio.sleep(interval)
                try:
                    await asyncio.to_thread(workflow.tick)
                except Exception as e:  # keep the scheduler alive
                    db.log(None, "scheduler_error", {"error": str(e)})
        task = asyncio.create_task(loop())
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(title="MoveDesk", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(workflow.WorkflowError)
async def workflow_error(_: Request, e: workflow.WorkflowError):
    return JSONResponse({"error": str(e)}, status_code=e.status)


def admin(x_admin_key: str = Header(default="")) -> None:
    key = config.admin_key()
    if not key:
        if workflow.paypal().mode != "mock":
            # real PayPal credentials and an open dashboard would let anyone send invoices
            raise HTTPException(503, "Set ADMIN_KEY before using real PayPal credentials")
        return
    if not hmac.compare_digest(x_admin_key, key):
        raise HTTPException(401, "admin key required")


def demo_only() -> None:
    if workflow.paypal().mode != "mock":
        raise HTTPException(404, "demo endpoints are only available in simulator mode")


# ---------------- pages ----------------

@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "index.html")


@app.get("/chat", include_in_schema=False)
def chat_page():
    return FileResponse(STATIC / "chat.html")


@app.get("/mock-pay/{invoice_id}", response_class=HTMLResponse, include_in_schema=False)
def mock_pay_page(invoice_id: str):
    demo_only()
    return FileResponse(STATIC / "mockpay.html")


# ---------------- public API ----------------

@app.get("/api/config")
def get_config():
    return {"company": config.company()["name"], "currency": config.company()["currency"],
            "paypal_mode": workflow.paypal().mode, "claude": bool(config.anthropic_key()),
            "copilot": "claude" if copilot.enabled() else "offline",
            "admin_key_required": bool(config.admin_key())}


@app.post("/api/chat")
def chat(body: ChatIn):
    return workflow.chat(body.session_id, body.message)


@app.get("/api/chat/{session_id}/status")
def chat_status(session_id: str):
    """The customer's own job, keyed by their chat session id."""
    if len(session_id) > 64:
        raise HTTPException(404, "not found")
    return workflow.customer_status(session_id)


@app.post("/api/paypal/webhook")
async def paypal_webhook(request: Request):
    event = await request.json()
    try:
        ok = workflow.paypal().verify_webhook(dict(request.headers), event)
    except PayPalError as e:
        raise HTTPException(502, f"signature check failed: {e}")
    if not ok:
        raise HTTPException(401, "webhook signature not verified")
    return workflow.handle_paypal_event(event)


# ---------------- owner API ----------------

@app.get("/api/jobs", dependencies=[Depends(admin)])
def jobs():
    return workflow.db.list_jobs()


@app.get("/api/jobs/{job_id}", dependencies=[Depends(admin)])
def job(job_id: str):
    j = workflow._job(job_id)
    j["events"] = db.events_for(job_id)
    return j


@app.post("/api/jobs/{job_id}/approve", dependencies=[Depends(admin)])
def approve(job_id: str):
    return workflow.approve(job_id)


@app.post("/api/jobs/{job_id}/reject", dependencies=[Depends(admin)])
def reject(job_id: str, body: RejectIn):
    return workflow.reject(job_id, body.reason)


@app.post("/api/jobs/{job_id}/complete", dependencies=[Depends(admin)])
def complete(job_id: str, body: CompleteIn):
    return workflow.complete(job_id, body.actual_hours, body.waive_overtime)


@app.post("/api/jobs/{job_id}/record-cash", dependencies=[Depends(admin)])
def record_cash(job_id: str):
    return workflow.record_cash(job_id)


@app.post("/api/jobs/{job_id}/remind", dependencies=[Depends(admin)])
def remind(job_id: str, body: RemindIn):
    return workflow.send_reminder(job_id, body.tone)


@app.post("/api/copilot", dependencies=[Depends(admin)])
def ask_copilot(body: CopilotIn):
    try:
        return copilot.ask(body.message, [t.model_dump() for t in body.history])
    except workflow.WorkflowError:
        raise
    except Exception as e:  # Claude unavailable: answer offline instead of failing the owner
        out = copilot.offline(body.message)
        out["note"] = f"Claude was unavailable ({type(e).__name__}), so this is an offline answer."
        return out


@app.get("/api/metrics", dependencies=[Depends(admin)])
def metrics():
    return workflow.metrics()


@app.post("/api/admin/run-reminders", dependencies=[Depends(admin)])
def run_now():
    return workflow.tick()


# ---------------- simulator-only helpers ----------------

@app.get("/api/demo/invoice/{invoice_id}")
def demo_invoice(invoice_id: str):
    demo_only()
    try:
        inv = workflow.paypal()._inv(invoice_id)
    except PayPalError:
        raise HTTPException(404, "invoice not found")
    s = inv["spec"]
    return {"id": inv["id"], "status": inv["status"], "total": inv["total"], "currency": s["currency"],
            "business": config.company()["name"], "recipient": s["recipient_name"], "note": s["note"],
            "items": s["items"], "due_date": s["due_date"].isoformat(),
            "qr": workflow.paypal().qr_code(invoice_id)}


@app.post("/api/demo/pay/{invoice_id}")
def demo_pay(invoice_id: str):
    """The customer clicks Pay in the simulated PayPal page. Same path as a real webhook, minus the network."""
    demo_only()
    try:
        event = workflow.paypal().pay(invoice_id)
    except PayPalError as e:
        raise HTTPException(e.status, e.body)
    return workflow.handle_paypal_event(event)


@app.post("/api/demo/advance", dependencies=[Depends(admin)])
def demo_advance(body: AdvanceIn):
    demo_only()
    db.advance_clock(body.days)
    return {"clock_offset_days": db.kv_get("clock_offset_days", "0"), "tick": workflow.tick()}


@app.post("/api/demo/reset", dependencies=[Depends(admin)])
def demo_reset():
    demo_only()
    db.reset_all()
    workflow.set_paypal(None)
    return {"ok": True}


@app.post("/api/demo/seed", dependencies=[Depends(admin)])
def demo_seed():
    demo_only()
    return {"jobs": _seed()}


def _seed() -> list:
    """Sample requests driven through the real workflow, so the board shows every stage."""
    ids = []
    for msgs in SEED_CHATS:
        sid, r = None, {}
        for msg in msgs:
            r = workflow.chat(sid, msg)
            sid = r["session_id"]
        if r.get("job_id"):
            ids.append(r["job_id"])
    pp = workflow.paypal()

    def pay(job_id: str, field: str) -> None:
        workflow.handle_paypal_event(pp.pay(db.get("jobs", job_id)[field]))

    if len(ids) >= 5:
        for jid in ids[2:5]:
            workflow.approve(jid)
            pay(jid, "deposit_invoice")
        for jid, extra in ((ids[3], 1.0), (ids[4], 0.0)):
            hours = db.get("jobs", jid)["quote"]["billable_hours"] + extra
            workflow.complete(jid, hours)
        pay(ids[4], "balance_invoice")
    return ids
