"""Small SQLite layer. JSON columns are encoded and decoded automatically.

The app uses a simulated clock offset (days) stored in the kv table, so a demo can move forward in time
without waiting. In production the offset stays at 0."""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

JSON_COLS = {"job", "estimate", "quote", "assumptions", "reminders"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, draft TEXT NOT NULL, contact TEXT NOT NULL, messages TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'en', job_id TEXT, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, session_id TEXT, customer_name TEXT, customer_email TEXT, language TEXT,
  job TEXT, estimate TEXT, quote TEXT, assumptions TEXT, status TEXT NOT NULL,
  deposit_invoice TEXT, deposit_url TEXT, deposit_qr TEXT,
  balance_invoice TEXT, balance_url TEXT, balance_qr TEXT,
  balance_amount REAL, balance_discount_pct INTEGER DEFAULT 0, discount_deadline TEXT,
  balance_sent_at TEXT, balance_due TEXT, actual_hours REAL, overtime_amount REAL DEFAULT 0,
  p_on_time REAL, exp_days REAL, reminders TEXT DEFAULT '[]',
  deposit_paid_at TEXT, balance_paid_at TEXT, paid_method TEXT, reject_reason TEXT,
  created TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, job_id TEXT, kind TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS pp_events (event_id TEXT PRIMARY KEY, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def connect(path: str) -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()


def _c() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("database not connected")
    return _conn


def query(sql: str, args: tuple = ()) -> list:
    with _lock:
        return [dict(r) for r in _c().execute(sql, args).fetchall()]


def execute(sql: str, args: tuple = ()) -> int:
    with _lock:
        cur = _c().execute(sql, args)
        _c().commit()
        return cur.lastrowid


def reset_all() -> None:
    with _lock:
        for t in ("sessions", "jobs", "events", "pp_events", "kv"):
            _c().execute(f"DELETE FROM {t}")
        _c().commit()


# ---- clock ----

def kv_get(key: str, default: str = "") -> str:
    rows = query("SELECT value FROM kv WHERE key=?", (key,))
    return rows[0]["value"] if rows else default


def kv_set(key: str, value: str) -> None:
    execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=float(kv_get("clock_offset_days", "0")))


def iso(dt: Optional[datetime] = None) -> str:
    return (dt or now()).isoformat(timespec="seconds")


def parse(ts: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(ts) if ts else None


def advance_clock(days: float) -> float:
    total = float(kv_get("clock_offset_days", "0")) + days
    kv_set("clock_offset_days", str(total))
    return total


# ---- row helpers ----

def _dec(row: dict) -> dict:
    for k in JSON_COLS:
        if k in row and isinstance(row[k], str):
            row[k] = json.loads(row[k])
    return row


def _enc(fields: dict) -> dict:
    return {k: (json.dumps(v) if k in JSON_COLS and not isinstance(v, str) else v) for k, v in fields.items()}


def insert(table: str, fields: dict) -> None:
    f = _enc(fields)
    cols = ",".join(f)
    marks = ",".join("?" for _ in f)
    execute(f"INSERT INTO {table}({cols}) VALUES({marks})", tuple(f.values()))


def update(table: str, key: str, fields: dict) -> None:
    f = _enc(fields)
    sets = ",".join(f"{k}=?" for k in f)
    execute(f"UPDATE {table} SET {sets} WHERE id=?", (*f.values(), key))


def get(table: str, key: str) -> Optional[dict]:
    rows = query(f"SELECT * FROM {table} WHERE id=?", (key,))
    return _dec(rows[0]) if rows else None


def list_jobs() -> list:
    return [_dec(r) for r in query("SELECT * FROM jobs ORDER BY created DESC")]


def log(job_id: Optional[str], kind: str, detail: Any = None) -> None:
    execute("INSERT INTO events(ts,job_id,kind,detail) VALUES(?,?,?,?)",
            (iso(), job_id, kind, json.dumps(detail) if detail is not None else None))


def events_for(job_id: str) -> list:
    rows = query("SELECT ts,kind,detail FROM events WHERE job_id=? ORDER BY id", (job_id,))
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return rows


def claim_event(event_id: str) -> bool:
    """True the first time an event id is seen, False for duplicates."""
    try:
        execute("INSERT INTO pp_events(event_id,ts) VALUES(?,?)", (event_id, iso()))
        return True
    except sqlite3.IntegrityError:
        return False
