"""Wrapper around the trained models. Loaded once on first use."""
import threading
from datetime import datetime
from typing import Optional

import joblib
import pandas as pd

from . import config

_lock = threading.Lock()
_cache: dict = {}


def _load() -> dict:
    with _lock:
        if not _cache:
            d = config.model_dir()
            _cache["dur"] = joblib.load(d / "duration_gbm.joblib")
            _cache["p10"] = joblib.load(d / "duration_p10.joblib")
            _cache["p90"] = joblib.load(d / "duration_p90.joblib")
            _cache["days"] = joblib.load(d / "invoice_days_gbm.joblib")
            _cache["clf"] = joblib.load(d / "invoice_on_time_clf.joblib")
        return _cache


def _duration_row(job: dict) -> pd.DataFrame:
    m = _load()
    r = dict(job)
    r.update(config.company_profile())
    r["flights_origin"] = 0 if r["elevator_origin"] else r["floors_origin"]
    r["flights_dest"] = 0 if r["elevator_dest"] else r["floors_dest"]
    return pd.DataFrame([r])[m["dur"]["features"]]


def predict_duration(job: dict) -> dict:
    """Hours estimate with a P10 to P90 range. `job` is a fully specified job dict."""
    m = _load()
    X = _duration_row(job)
    h = float(m["dur"]["model"].predict(X)[0])
    lo = float(m["p10"]["model"].predict(X)[0])
    hi = float(m["p90"]["model"].predict(X)[0])
    h = max(h, 1.0)
    return {"hours": round(h, 2), "p10": round(max(min(lo, h), 0.5), 2), "p90": round(max(hi, h), 2)}


def explain(job: dict) -> list:
    """What drives the estimate: hours the model attributes to each factor, by switching it off."""
    base = predict_duration(job)["hours"]
    out = []

    def delta(label: str, changed: dict, cond: bool):
        if not cond:
            return
        alt = dict(job, **changed)
        d = base - predict_duration(alt)["hours"]
        if abs(d) >= 0.1:
            out.append({"factor": label, "hours": round(d, 1)})

    stairs = (0 if job["elevator_origin"] else job["floors_origin"]) + (0 if job["elevator_dest"] else job["floors_dest"])
    delta("Stairs (no elevator)", {"elevator_origin": 1, "elevator_dest": 1}, stairs > 0)
    delta("Heavy items", {"heavy_items": 0}, job["heavy_items"] > 0)
    delta("Packing service", {"packing_service": 0}, bool(job["packing_service"]))
    delta("Long carry from the truck", {"carry_origin_m": 10, "carry_dest_m": 10},
          job["carry_origin_m"] + job["carry_dest_m"] > 60)
    delta("Distance", {"distance_km": 10}, job["distance_km"] > 40)
    return sorted(out, key=lambda x: -abs(x["hours"]))


def predict_payment(inv: dict) -> dict:
    """Expected days to pay and the probability the invoice is paid within its terms."""
    m = _load()
    X = pd.DataFrame([inv])[m["days"]["features"]]
    days = float(m["days"]["model"].predict(X)[0])
    p = float(m["clf"]["model"].predict_proba(X)[0, 1])
    return {"expected_days": round(max(days, 0.0), 1), "p_on_time": round(p, 3)}


def payment_features(job_row: dict, quote: dict, discount_pct: int, terms: int, balance: float,
                     had_delay: bool, now: datetime, language: str) -> dict:
    return {
        "total_amount": quote["total"], "balance_amount": balance, "deposit_pct": quote["deposit_pct"],
        "prior_late_count": 0, "payment_terms_days": terms, "early_discount_pct": discount_pct,
        "sent_dow": now.weekday(), "job_rating": 4, "had_delay_event": int(had_delay),
        "customer_type": "residential", "preferred_payment_method": "paypal",
        "reminder_policy": "standard", "language": language if language in ("en", "ar") else "en",
    }
