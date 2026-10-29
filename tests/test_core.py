"""Pricing math, ML sanity, extraction and input validation."""
import pytest

from app import intake, ml, pricing
from app.schemas import PartialJob

CARD = pricing.RateCard()
BASE = dict(move_type="apartment", rooms=3, boxes=40, large_furniture=6, appliances=1, heavy_items=0,
            fragile_items=3, disassembly_items=2, floors_origin=0, floors_dest=0, elevator_origin=0,
            elevator_dest=0, distance_km=20, packing_service=0, parking_origin=1, parking_dest=1,
            carry_origin_m=10, carry_dest_m=10, is_month_end=0, start_hour=9, month=5)


def full(**kw):
    j = dict(BASE, **kw)
    j["volume_m3_est"] = pricing.volume_m3(j)
    j["crew_size"] = pricing.recommended_crew(j["volume_m3_est"])
    return j


def test_quote_math_and_rounding():
    j = full(distance_km=50, packing_service=1, boxes=40)
    q = pricing.build_quote(j, 4.2, CARD)
    assert q["billable_hours"] == 4.5
    labor = 4.5 * float(pricing.hourly_rate(j["crew_size"], CARD))
    assert q["lines"][0]["amount"] == pytest.approx(float(labor))
    assert q["lines"][1]["amount"] == pytest.approx(20 * 2.2)
    assert q["lines"][2]["amount"] == pytest.approx(40 * 1.5)
    assert q["total"] == pytest.approx(sum(l["amount"] for l in q["lines"]))
    assert q["deposit"] + q["balance"] == pytest.approx(q["total"], abs=0.005)


def test_minimum_hours_and_overtime():
    assert pricing.billable_hours(1.0, CARD) == 3.0
    assert pricing.overtime(4.0, 3.5, 2, CARD)["amount"] == 0.0
    ot = pricing.overtime(4.0, 5.2, 3, CARD)
    assert ot["hours"] == 1.5 and ot["amount"] == pytest.approx(1.5 * 130.0)


def test_ml_stairs_cost_time_and_range_is_ordered():
    flat = ml.predict_duration(full(floors_origin=0, elevator_origin=0))
    stairs = ml.predict_duration(full(floors_origin=5, elevator_origin=0))
    lift = ml.predict_duration(full(floors_origin=5, elevator_origin=1))
    assert stairs["hours"] > lift["hours"] and stairs["hours"] > flat["hours"]
    assert flat["p10"] <= flat["hours"] <= flat["p90"]


def test_ml_bigger_job_takes_longer():
    small = ml.predict_duration(full(rooms=1, boxes=15, large_furniture=2))
    big = ml.predict_duration(full(rooms=6, boxes=120, large_furniture=14))
    assert big["hours"] > small["hours"]


def test_payment_model_outputs_probability():
    q = pricing.build_quote(full(), 5, CARD)
    out = ml.predict_payment(ml.payment_features({}, q, 5, 7, q["balance"], False, __import__("datetime").datetime(2026, 5, 4), "en"))
    assert 0 <= out["p_on_time"] <= 1 and out["expected_days"] >= 0


def test_rule_extraction_english_and_arabic_script():
    ex = intake.rule_extract("I'm Sara Ali, sara@example.com. 3 room apartment, 4th floor no elevator, "
                             "moving to a ground floor place 20 km away.")
    assert ex.fields["rooms"] == 3 and ex.fields["floors_origin"] == 4 and ex.fields["distance_km"] == 20
    assert ex.email == "sara@example.com" and ex.name == "Sara Ali"
    ar = intake.rule_extract("شقة 3 غرف في الدور الرابع بدون مصعد والمسافة 15 كم")
    assert ar.language == "ar"


def test_invalid_values_are_dropped_not_trusted():
    fields, dropped = intake.validate_fields({"rooms": 99, "distance_km": -5, "boxes": 20, "move_type": "castle"})
    assert fields == {"boxes": 20} and set(dropped) == {"rooms", "distance_km", "move_type"}
    with pytest.raises(Exception):
        PartialJob(rooms=0)


def test_missing_and_followup_questions():
    d = {"rooms": 3}
    assert "distance_km" in intake.missing(d)
    qs = intake.questions(d, {}, "en")
    assert len(qs) <= 3 and all(q.endswith("?") for q in qs)
    assert not intake.is_complete(d, {})


def test_ground_floor_implies_no_elevator_question():
    d = {"move_type": "apartment", "rooms": 2, "floors_origin": 0, "floors_dest": 0, "distance_km": 5}
    assert intake.missing(d) == []


def test_destination_marker_without_moving_to():
    ex = intake.rule_extract("Studio, 2nd floor no elevator to 1st floor with elevator, 12 km")
    assert (ex.fields["floors_origin"], ex.fields["elevator_origin"]) == (2, 0)
    assert (ex.fields["floors_dest"], ex.fields["elevator_dest"]) == (1, 1)


def test_pickup_only_phrasing():
    ex = intake.rule_extract("moving a 2 bedroom apartment from the 3rd floor no elevator, 25 km")
    assert (ex.fields["floors_origin"], ex.fields["elevator_origin"]) == (3, 0)
    assert "floors_dest" not in ex.fields  # destination unknown, so the agent must still ask
