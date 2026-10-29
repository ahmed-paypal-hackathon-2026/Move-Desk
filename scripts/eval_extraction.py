#!/usr/bin/env python3
"""Measure intake accuracy on the labeled chat messages in data/chats.jsonl.

Usage:
  python scripts/eval_extraction.py                 # rule-based extractor, no network
  ANTHROPIC_API_KEY=... python scripts/eval_extraction.py --limit 200   # Claude extractor

Reports field-level accuracy and the share of messages where every field is correct."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import intake  # noqa: E402

LABEL_TO_FIELD = {"move_type": "move_type", "rooms": "rooms", "floors_origin": "floors_origin",
                  "floors_dest": "floors_dest", "elevator_origin": "elevator_origin",
                  "elevator_dest": "elevator_dest", "boxes": "boxes", "large_furniture": "large_furniture",
                  "heavy_items": "heavy_items", "distance_km": "distance_km", "packing_service": "packing_service"}


def run(path: Path, limit: int, extractor) -> dict:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()][:limit]
    right, total, perfect = Counter(), Counter(), 0
    by_lang = {"en": [0, 0], "ar": [0, 0]}
    for r in rows:
        ex = extractor(r["text"])
        ok_all = True
        for k, truth in r["label"].items():
            total[k] += 1
            got = intake.normalize(ex.fields).get(k)
            if k == "heavy_items" and got is None:
                got = 0  # unmentioned heavy items default to 0 in the app and are shown as an assumption
            hit = got is not None and float(got) == float(truth) if not isinstance(truth, str) else got == truth
            right[k] += int(hit)
            ok_all &= hit
        perfect += int(ok_all)
        by_lang[r["language"]][0] += int(ok_all)
        by_lang[r["language"]][1] += 1
    n_fields = sum(total.values())
    return {
        "messages": len(rows),
        "field_accuracy": round(sum(right.values()) / n_fields, 4),
        "all_fields_correct": round(perfect / len(rows), 4),
        "per_field": {k: round(right[k] / total[k], 3) for k in total},
        "all_fields_correct_by_language": {k: round(v[0] / v[1], 3) if v[1] else None for k, v in by_lang.items()},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/chats.jsonl")
    ap.add_argument("--limit", type=int, default=100000)
    a = ap.parse_args()
    result = run(Path(a.data), a.limit, lambda text: intake.extract(text))
    print(json.dumps(result, indent=2))
