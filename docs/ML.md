# MoveDesk ML: data and models

The app loads these models from `models/` at runtime (`app/ml.py`). Retrain with the command below.

## Quick start

```bash
pip install -r requirements.txt matplotlib
python train.py --data data --out models                 # about 40 seconds
```

## What is in `data/`

| File | Rows | Purpose |
|---|---|---|
| `companies.csv` | 40 | Moving company profiles: experience, equipment, region, hourly rate |
| `jobs.csv` | 60,000 | One row per completed move. **Target: `actual_hours`** |
| `invoices.csv` | 60,000 | One balance invoice per job. **Target: `days_to_pay`** (capped at 60) |
| `chats.jsonl` | 1,500 | Customer messages (English and Egyptian Arabic in Latin letters) with exact ground-truth fields |

### `jobs.csv` columns

| Column | Meaning |
|---|---|
| `move_type` | studio, apartment, house, office |
| `rooms`, `boxes`, `large_furniture`, `appliances`, `heavy_items` (piano, safe, treadmill), `fragile_items`, `disassembly_items` | Inventory counts |
| `floors_origin`, `floors_dest` | Floor number (for houses: flights of internal stairs) |
| `elevator_origin`, `elevator_dest` | 1 if an elevator is available |
| `flights_origin`, `flights_dest` | Flights actually climbed (0 when an elevator exists) |
| `distance_km` | Distance between addresses |
| `parking_origin`, `parking_dest` | 1 easy to 3 hard |
| `carry_origin_m`, `carry_dest_m` | Distance from truck to door |
| `packing_service` | 1 if the crew packs |
| `volume_m3_est` | Estimated volume from the counts |
| `crew_size` | 2 to 6 movers |
| `crew_experience_years`, `has_dollies_and_straps`, `region` | Company traits (observable) |
| `month`, `day_of_week`, `is_month_end`, `start_hour` | Timing |
| `had_delay_event` | Random breakdown or access delay (about 5 percent of jobs) |
| `actual_hours` | Start to finish clock time including the drive |

### `invoices.csv` columns

`total_amount`, `deposit_pct`, `balance_amount`, `customer_type` (residential, corporate, realtor),
`preferred_payment_method` (card, paypal, bank_transfer, cash), `prior_late_count`, `payment_terms_days`,
`early_discount_pct`, `reminder_policy`, `sent_dow`, `job_rating`, `had_delay_event`, `language`,
`days_to_pay`, `paid_on_time`.

Invoice totals use a 3-hour minimum, half-hour rounding, crew-size rate uplift, a distance fee beyond
30 km and packing materials.

### `chats.jsonl`

Each line has `text` and a `label` object. Use it to measure Claude extraction: send `text`, compare
the returned JSON to `label`, and report field-level accuracy in the README.

## Results (held-out test sets)

Job duration, hours:

| Model | MAE | RMSE | R2 |
|---|---|---|---|
| Rule of thumb (hours = a + b x rooms) | 2.61 | 3.55 | 0.33 |
| Linear regression (explainable) | 1.06 | 1.48 | 0.88 |
| **Gradient boosting** | **0.81** | **1.17** | **0.93** |

On 20 percent of companies never seen in training: MAE 0.83, R2 0.93.
P10 to P90 quote range: about 2.4 hours wide, and it contains the real value 75 percent of the time
(nominal 80 percent, so it is slightly overconfident; say so).

Days to pay:

| Model | MAE (days) | R2 |
|---|---|---|
| Median by customer type | 4.94 | 0.15 |
| Linear regression | 5.80 | 0.32 |
| **Gradient boosting (absolute-error loss)** | **3.56** | **0.34** |

Paid-on-time classifier AUC is 0.95. Treat that as optimistic: payment terms and cash payments make
on-time payment easy to separate. Late-tail payers are hard to predict, so exact day prediction stays hard.
Use the probability to decide reminder timing, not the day count.

Files written to `models/`: model files, `metrics.json`, `duration_linear_coefficients.csv`
(hours per unit, good for the video), `duration_feature_importance.csv`, `duration_pred_vs_actual.png`.

## How it plugs into MoveDesk

1. Claude turns the customer chat into validated job fields (`app/intake.py`).
2. `ml.predict_duration` returns hours and a P10 to P90 range (`app/ml.py`).
3. The rate card turns hours into money (`app/pricing.py`). AI never invents prices.
4. After the move, `ml.predict_payment` returns the chance the balance is paid on time, which sets reminder timing and tone (`app/workflow.py`).
