"""Pooled XGBoost quantile forecaster, one model per horizon.

A single multi-quantile model (``reg:quantileerror``) is trained per horizon on all apps'
training days. Its quantiles give the confidence interval shown on the dashboard, and an upper
quantile is what the predictive scaling policy provisions for.

Usage:
    python -m cloudopt.forecast.xgb            # train + evaluate horizons 1, 12, 288
    python -m cloudopt.forecast.xgb --horizons 1 2
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import xgboost as xgb

from cloudopt import config
from cloudopt.data import load_workload
from cloudopt.forecast import baselines as fb
from cloudopt.forecast import features
from cloudopt.forecast.metrics import forecast_metrics

QUANTILES = np.array([0.05, 0.1, 0.5, 0.9, 0.95, 0.99])
HORIZONS = {1: "5 min", 3: "15 min", 6: "30 min", 12: "1 hour", 288: "24 hours"}

PARAMS = dict(
    objective="reg:quantileerror",
    quantile_alpha=QUANTILES,
    tree_method="hist",
    max_depth=6,  # depth 8 was ~2% more accurate but 4x larger; the live demo must fit a free-tier host
    learning_rate=0.08,
    min_child_weight=20,
    subsample=0.8,
    colsample_bytree=0.8,
    max_bin=128,
    nthread=-1,
)


def _rows(X: np.ndarray, z: np.ndarray, sl: slice, horizon: int, stride: int = 1):
    """Flatten (apps, slots, F) to rows for slots in ``sl`` whose features are mostly present."""
    start = max(sl.start, horizon + 12)
    Xs, ys = X[:, start: sl.stop: stride], z[:, start: sl.stop: stride]
    Xf, yf = Xs.reshape(-1, X.shape[-1]), ys.reshape(-1)
    keep = np.isfinite(yf)
    return Xf[keep], yf[keep]


class XGBForecaster:
    def __init__(self, horizon: int, num_boost_round: int = 400):
        self.horizon = horizon
        self.rounds = num_boost_round
        self.booster: xgb.Booster | None = None
        self.feature_names: list[str] = []

    def fit(self, peak: np.ndarray, mean: np.ndarray, scale: np.ndarray, stride: int = 2) -> "XGBForecaster":
        X, self.feature_names = features.build(peak, mean, scale, self.horizon)
        z = features.to_z(peak, scale)
        Xtr, ytr = _rows(X, z, config.TRAIN, self.horizon, stride)
        Xva, yva = _rows(X, z, config.VAL, self.horizon, stride * 2)
        dtr = xgb.QuantileDMatrix(Xtr, ytr, feature_names=self.feature_names, max_bin=PARAMS["max_bin"])
        dva = xgb.QuantileDMatrix(Xva, yva, ref=dtr, feature_names=self.feature_names, max_bin=PARAMS["max_bin"])
        self.booster = xgb.train(PARAMS, dtr, self.rounds, evals=[(dva, "val")],
                                 early_stopping_rounds=30, verbose_eval=False)
        return self

    def predict(self, peak: np.ndarray, mean: np.ndarray, scale: np.ndarray) -> np.ndarray:
        """Quantile forecasts in original units, shape (len(QUANTILES), apps, slots).
        Slot t holds the forecast issued at t - horizon (NaN where history is too short)."""
        X, _ = features.build(peak, mean, scale, self.horizon)
        n, T, F = X.shape
        d = xgb.DMatrix(X.reshape(-1, F), feature_names=self.feature_names)
        q = self.booster.predict(d, iteration_range=(0, self.n_iterations))
        q = np.sort(q.reshape(n, T, len(QUANTILES)), axis=-1)  # repair any quantile crossing
        q[:, : self.horizon + 12] = np.nan
        return np.stack([features.from_z(q[..., i], scale) for i in range(len(QUANTILES))])

    def predict_at(self, peak: np.ndarray, mean: np.ndarray, scale: np.ndarray, cols) -> np.ndarray:
        """Like predict() but only for slot columns ``cols``: shape (len(QUANTILES), apps, len(cols)).
        Used by the live loop, where only the target slot is needed."""
        X, _ = features.build(peak, mean, scale, self.horizon)
        Xc = X[:, cols]
        n, k, F = Xc.shape
        q = self.booster.predict(xgb.DMatrix(Xc.reshape(-1, F), feature_names=self.feature_names),
                                 iteration_range=(0, self.n_iterations))
        q = np.sort(q.reshape(n, k, len(QUANTILES)), axis=-1)
        return np.stack([features.from_z(q[..., i], scale) for i in range(len(QUANTILES))])

    @property
    def n_iterations(self) -> int:
        try:
            return self.booster.best_iteration + 1
        except AttributeError:  # trained without early stopping
            return self.booster.num_boosted_rounds()

    def save(self, path) -> None:
        self.booster.save_model(path)

    @classmethod
    def load(cls, horizon: int, path=None) -> "XGBForecaster":
        m = cls(horizon)
        m.booster = xgb.Booster()
        m.booster.load_model(str(path or config.ARTIFACTS / f"xgb_h{horizon}.ubj"))
        m.feature_names = list(m.booster.feature_names)
        return m


def quantile_index(q: float) -> int:
    return int(np.argmin(np.abs(QUANTILES - q)))


def evaluate(horizons: list[int]) -> dict:
    wl_all = load_workload(scaling_study=False)
    scale = features.app_scale(wl_all.demand_mean)
    study = np.isin(wl_all.app_ids, load_workload(scaling_study=True).app_ids)
    y = wl_all.demand_peak
    report = {}
    config.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for h in horizons:
        t0 = time.time()
        model = XGBForecaster(h).fit(wl_all.demand_peak, wl_all.demand_mean, scale)
        q = model.predict(wl_all.demand_peak, wl_all.demand_mean, scale)
        np.save(config.ARTIFACTS / f"xgb_forecast_h{h}.npy", q.astype(np.float32))
        model.save(str(config.ARTIFACTS / f"xgb_h{h}.ubj"))
        preds = {
            "xgboost_p50": q[quantile_index(0.5)],
            "persistence": fb.persistence(y, h),
            "seasonal_naive_1d": fb.seasonal_naive(y, h),
        }
        res = {}
        for subset, mask in (("all_apps", np.ones(len(study), bool)), ("scaling_study_apps", study)):
            res[subset] = {
                name: forecast_metrics(y[mask][:, config.TEST], p[mask][:, config.TEST], scale[mask])
                for name, p in preds.items()
            }
            lo, hi = q[quantile_index(0.1)][mask][:, config.TEST], q[quantile_index(0.9)][mask][:, config.TEST]
            yt = y[mask][:, config.TEST]
            res[subset]["xgboost_p10_p90_coverage"] = float(np.mean((yt >= lo) & (yt <= hi)))
        res["best_iteration"] = int(model.booster.best_iteration)
        res["train_seconds"] = round(time.time() - t0, 1)
        report[f"h{h} ({HORIZONS.get(h, f'{h} slots')})"] = res
        print(json.dumps({f"h{h}": res}, indent=1))
    config.REPORTS.mkdir(exist_ok=True)
    path = config.REPORTS / "forecast_xgb.json"
    merged = json.loads(path.read_text()) if path.exists() else {}
    merged.update(report)  # keep results for horizons not re-run this time
    path.write_text(json.dumps(merged, indent=2))
    return merged


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS))
    evaluate(ap.parse_args().horizons)
