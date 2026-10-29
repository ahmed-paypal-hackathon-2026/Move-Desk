#!/usr/bin/env python3
"""
Train and evaluate the MoveDesk models.

Models
  1. Job duration (hours): baseline vs linear regression vs gradient boosting,
     plus 10th and 90th percentile models for a quote range.
  2. Days-to-pay for the balance invoice: baseline vs linear vs gradient boosting,
     plus a classifier for "paid on time".

Two evaluations are reported:
  random split      80/20 over jobs
  unseen companies  all jobs of 20 percent of companies are held out

Usage:  python train.py --data data --out models
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

DUR_NUM = ["rooms", "boxes", "large_furniture", "appliances", "heavy_items", "fragile_items",
           "disassembly_items", "floors_origin", "floors_dest", "elevator_origin", "elevator_dest",
           "flights_origin", "flights_dest", "distance_km", "parking_origin", "parking_dest",
           "carry_origin_m", "carry_dest_m", "packing_service", "volume_m3_est", "crew_size",
           "crew_experience_years", "has_dollies_and_straps", "month", "is_month_end", "start_hour"]
DUR_CAT = ["move_type", "region"]

INV_NUM = ["total_amount", "balance_amount", "deposit_pct", "prior_late_count", "payment_terms_days",
           "early_discount_pct", "sent_dow", "job_rating", "had_delay_event"]
INV_CAT = ["customer_type", "preferred_payment_method", "reminder_policy", "language"]


def pre(num, cat):
    return ColumnTransformer([
        ("num", "passthrough", num),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cat),
    ])


def gbm(**kw):
    return HistGradientBoostingRegressor(max_iter=400, learning_rate=0.06, max_leaf_nodes=31,
                                         l2_regularization=1.0, random_state=0, **kw)


def metrics(y, p):
    return {"MAE": round(float(mean_absolute_error(y, p)), 3),
            "RMSE": round(float(np.sqrt(mean_squared_error(y, p))), 3),
            "R2": round(float(r2_score(y, p)), 3)}


class PerRoomBaseline:
    """Rule of thumb an owner could use: hours = a + b * rooms, fitted on the training set."""
    def fit(self, X, y):
        self.m = LinearRegression().fit(X[["rooms"]], y)
        return self

    def predict(self, X):
        return self.m.predict(X[["rooms"]])


class GroupMedianBaseline:
    """Median days to pay per customer type."""
    def fit(self, X, y):
        self.m = pd.Series(np.asarray(y)).groupby(X["customer_type"].to_numpy()).median().to_dict()
        self.g = float(np.median(y))
        return self

    def predict(self, X):
        return X["customer_type"].map(self.m).fillna(self.g).to_numpy()


def split_random(df, seed=0):
    tr, te = train_test_split(df, test_size=0.2, random_state=seed)
    return tr, te


def split_company(df, seed=0):
    gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=seed)
    tr_idx, te_idx = next(gss.split(df, groups=df["company_id"]))
    return df.iloc[tr_idx], df.iloc[te_idx]


def eval_duration(df, splitter, label, save_dir=None):
    tr, te = splitter(df)
    feats = DUR_NUM + DUR_CAT
    Xtr, ytr, Xte, yte = tr[feats], tr["actual_hours"], te[feats], te["actual_hours"]

    base = PerRoomBaseline().fit(Xtr, ytr)
    lin = Pipeline([("p", pre(DUR_NUM, DUR_CAT)), ("m", LinearRegression())]).fit(Xtr, ytr)
    gb = Pipeline([("p", pre(DUR_NUM, DUR_CAT)), ("m", gbm())]).fit(Xtr, ytr)
    q10 = Pipeline([("p", pre(DUR_NUM, DUR_CAT)), ("m", gbm(loss="quantile", quantile=0.10))]).fit(Xtr, ytr)
    q90 = Pipeline([("p", pre(DUR_NUM, DUR_CAT)), ("m", gbm(loss="quantile", quantile=0.90))]).fit(Xtr, ytr)

    res = {
        "baseline_hours_per_room": metrics(yte, base.predict(Xte)),
        "linear_regression": metrics(yte, lin.predict(Xte)),
        "gradient_boosting": metrics(yte, gb.predict(Xte)),
    }
    lo, hi = q10.predict(Xte), q90.predict(Xte)
    res["quote_range_p10_p90_coverage"] = round(float(np.mean((yte >= lo) & (yte <= hi))), 3)
    res["quote_range_mean_width_hours"] = round(float(np.mean(hi - lo)), 3)
    print(f"\n[duration | {label}]  train={len(tr)} test={len(te)}")
    for k, v in res.items():
        print(f"  {k}: {v}")

    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": gb, "features": feats}, save_dir / "duration_gbm.joblib")
        joblib.dump({"model": q10, "features": feats}, save_dir / "duration_p10.joblib")
        joblib.dump({"model": q90, "features": feats}, save_dir / "duration_p90.joblib")
        joblib.dump({"model": lin, "features": feats}, save_dir / "duration_linear.joblib")

        # Explainable linear coefficients, in hours per unit
        names = DUR_NUM + list(lin.named_steps["p"].named_transformers_["cat"].get_feature_names_out(DUR_CAT))
        coef = pd.DataFrame({"feature": names, "hours_per_unit": lin.named_steps["m"].coef_})
        coef["abs"] = coef["hours_per_unit"].abs()
        coef.sort_values("abs", ascending=False).drop(columns="abs").round(4).to_csv(
            save_dir / "duration_linear_coefficients.csv", index=False)

        # Permutation importance of the boosting model
        sub = te.sample(min(5000, len(te)), random_state=0)
        pi = permutation_importance(gb, sub[feats], sub["actual_hours"], n_repeats=5, random_state=0,
                                    scoring="neg_mean_absolute_error")
        imp = pd.DataFrame({"feature": feats, "mae_increase_hours": pi.importances_mean})
        imp.sort_values("mae_increase_hours", ascending=False).round(4).to_csv(
            save_dir / "duration_feature_importance.csv", index=False)

        # Predicted vs actual picture for the demo video
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            s = te.sample(min(3000, len(te)), random_state=1)
            p = gb.predict(s[feats])
            fig, ax = plt.subplots(figsize=(5.5, 5.5))
            ax.scatter(s["actual_hours"], p, s=6, alpha=0.35)
            m = float(max(s["actual_hours"].max(), p.max()))
            ax.plot([0, m], [0, m], "r--", lw=1)
            ax.set_xlabel("Actual hours (test set)")
            ax.set_ylabel("Predicted hours")
            ax.set_title(f"Job duration model, MAE {res['gradient_boosting']['MAE']} h")
            fig.tight_layout()
            fig.savefig(save_dir / "duration_pred_vs_actual.png", dpi=150)
        except Exception as e:  # plotting is optional
            print("plot skipped:", e)
    return res


def eval_invoice(df, splitter, label, save_dir=None):
    tr, te = splitter(df)
    feats = INV_NUM + INV_CAT
    Xtr, ytr, Xte, yte = tr[feats], tr["days_to_pay"], te[feats], te["days_to_pay"]

    base = GroupMedianBaseline().fit(Xtr, ytr)
    lin = Pipeline([("p", pre(INV_NUM, INV_CAT)), ("m", LinearRegression())]).fit(Xtr, ytr)
    gb = Pipeline([("p", pre(INV_NUM, INV_CAT)), ("m", gbm(loss="absolute_error"))]).fit(Xtr, ytr)
    clf =Pipeline([("p", pre(INV_NUM, INV_CAT)),
                    ("m", HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, random_state=0))
                    ]).fit(Xtr, tr["paid_on_time"])

    res = {
        "baseline_median_by_customer_type": metrics(yte, base.predict(Xte)),
        "linear_regression": metrics(yte, lin.predict(Xte)),
        "gradient_boosting": metrics(yte, gb.predict(Xte)),
        "paid_on_time_AUC": round(float(roc_auc_score(te["paid_on_time"], clf.predict_proba(Xte)[:, 1])), 3),
    }
    print(f"\n[days-to-pay | {label}]  train={len(tr)} test={len(te)}")
    for k, v in res.items():
        print(f"  {k}: {v}")

    if save_dir is not None:
        joblib.dump({"model": gb, "features": feats}, save_dir / "invoice_days_gbm.joblib")
        joblib.dump({"model": clf, "features": feats}, save_dir / "invoice_on_time_clf.joblib")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="models")
    a = ap.parse_args()
    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    jobs = pd.read_csv(data / "jobs.csv")
    inv = pd.read_csv(data / "invoices.csv").merge(jobs[["job_id", "company_id"]], on="job_id")

    report = {
        "rows": {"jobs": len(jobs), "invoices": len(inv)},
        "duration": {
            "random_split": eval_duration(jobs, split_random, "random split", save_dir=out),
            "unseen_companies": eval_duration(jobs, split_company, "unseen companies"),
        },
        "days_to_pay": {
            "random_split": eval_invoice(inv, split_random, "random split", save_dir=out),
            "unseen_companies": eval_invoice(inv, split_company, "unseen companies"),
        },
    }
    (out / "metrics.json").write_text(json.dumps(report, indent=2))
    print("\nsaved models and metrics.json to", out)


if __name__ == "__main__":
    main()
