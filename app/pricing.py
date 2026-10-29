"""Deterministic pricing. The ML model estimates hours; this module turns hours into money.
No AI is involved here. Money uses Decimal and is rounded to cents."""
import math
import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def D(x) -> Decimal:
    return Decimal(str(x))


def money(x: Decimal) -> float:
    return float(x.quantize(CENT, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class RateCard:
    hourly_rate_2_movers: float = 95.0
    extra_mover_hourly: float = 35.0
    min_hours: float = 3.0
    distance_free_km: float = 30.0
    distance_rate_per_km: float = 2.2
    packing_materials_per_box: float = 1.5
    deposit_pct: float = 0.25
    early_pay_discount_pct: int = 5
    early_pay_days: int = 3
    payment_terms_days: int = 7
    currency: str = "USD"

    @staticmethod
    def from_env() -> "RateCard":
        g = os.environ.get
        return RateCard(
            hourly_rate_2_movers=float(g("RATE_HOURLY_2_MOVERS", "95")),
            extra_mover_hourly=float(g("RATE_EXTRA_MOVER", "35")),
            min_hours=float(g("RATE_MIN_HOURS", "3")),
            deposit_pct=float(g("DEPOSIT_PCT", "0.25")),
            early_pay_discount_pct=int(g("EARLY_PAY_DISCOUNT_PCT", "5")),
            currency=g("CURRENCY", "USD"),
        )


def volume_m3(job: dict) -> float:
    return round(job["rooms"] * 1.0 + job["boxes"] * 0.06 + job["large_furniture"] * 0.85
                 + job["appliances"] * 0.55 + job["heavy_items"] * 1.1 + job["fragile_items"] * 0.08, 2)


def recommended_crew(vol: float) -> int:
    return int(min(6, max(2, round(vol / 9))))


def billable_hours(hours: float, card: RateCard) -> float:
    """Half-hour steps with a minimum charge."""
    return max(card.min_hours, math.ceil(hours * 2) / 2)


def hourly_rate(crew: int, card: RateCard) -> Decimal:
    return D(card.hourly_rate_2_movers) + D(card.extra_mover_hourly) * (crew - 2)


def build_quote(job: dict, hours: float, card: RateCard) -> dict:
    """Quote lines and totals for a job. `job` must include crew_size, distance_km, boxes, packing_service."""
    bh = D(billable_hours(hours, card))
    rate = hourly_rate(job["crew_size"], card)
    lines = [{"name": f"Moving labor, {job['crew_size']} movers", "qty": float(bh), "unit": money(rate),
              "amount": money(bh * rate)}]
    over = D(job["distance_km"]) - D(card.distance_free_km)
    if over > 0:
        lines.append({"name": f"Distance beyond {card.distance_free_km:g} km", "qty": float(over),
                      "unit": card.distance_rate_per_km, "amount": money(over * D(card.distance_rate_per_km))})
    if job["packing_service"]:
        lines.append({"name": "Packing materials", "qty": job["boxes"], "unit": card.packing_materials_per_box,
                      "amount": money(D(job["boxes"]) * D(card.packing_materials_per_box))})
    total = sum((D(l["amount"]) for l in lines), D(0))
    deposit = (total * D(card.deposit_pct)).quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "currency": card.currency,
        "billable_hours": float(bh),
        "hourly_rate": money(rate),
        "lines": lines,
        "total": money(total),
        "deposit": money(deposit),
        "balance": money(total - deposit),
        "deposit_pct": card.deposit_pct,
    }


def quote_range(job: dict, hours_lo: float, hours_hi: float, card: RateCard) -> dict:
    lo = build_quote(job, hours_lo, card)["total"]
    hi = build_quote(job, hours_hi, card)["total"]
    return {"low": min(lo, hi), "high": max(lo, hi)}


def overtime(quoted_hours: float, actual_hours: float, crew: int, card: RateCard) -> dict:
    """Extra labor when the job ran longer than the billed time. Returns hours and amount."""
    extra = D(billable_hours(actual_hours, card)) - D(billable_hours(quoted_hours, card))
    if extra <= 0:
        return {"hours": 0.0, "amount": 0.0}
    return {"hours": float(extra), "amount": money(extra * hourly_rate(crew, card))}
