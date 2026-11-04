"""Forecast-residual anomaly detection with an Isolation Forest.

Raw-value detectors flag every busy period. Here each slot is described by how far the
observed peak deviates from what the 5-minute forecaster expected (in units of its own
prediction interval), so a predictable rise, like a morning ramp, stays inside the interval
and is treated as expected high demand. An alert needs both: the Isolation Forest score is
above a tuned threshold, AND the value falls outside the forecast's p05-p95 band.

Evaluation injects labelled synthetic events (the trace has no ground-truth anomaly labels):
short spikes and drops are positives, gradual sustained surges are negatives that a good
detector must not alarm on. Thresholds are tuned on validation days (injection seed 1) and
results reported on held-out test days (seed 2). Scoring is event-level, as an on-call
engineer experiences alerts.

Usage:
    python -m cloudopt.anomaly
"""

from __future__ import annotations

import json

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from cloudopt import config
from cloudopt.data import load_workload
from cloudopt.forecast import features
from cloudopt.forecast.xgb import XGBForecaster, quantile_index

FEATURES = ["resid", "resid_prev", "log_ratio_p95", "log_ratio_p05"]
EPS = 1e-6


def residual_features(y: np.ndarray, q: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """(apps, slots, F) scale-free features from actual peak demand ``y`` and 1-step quantile
    forecasts ``q``: the residual in units of the forecast's own p10-p90 width (now and one slot
    earlier) and the log-distance outside the p05/p95 band."""
    zq = {a: features.to_z(np.maximum(q[quantile_index(a)], 0), scale) for a in (0.1, 0.5, 0.9)}
    z = features.to_z(y, scale)
    width = np.maximum(zq[0.9] - zq[0.1], 0.05)
    resid = (z - zq[0.5]) / width
    resid_prev = np.concatenate([np.full((y.shape[0], 1), np.nan), resid[:, :-1]], axis=1)
    f = np.stack([
        resid,
        resid_prev,
        np.log((y + EPS) / (np.maximum(q[quantile_index(0.95)], 0) + EPS)),
        np.log((y + EPS) / (np.maximum(q[quantile_index(0.05)], 0) + EPS)),
    ], axis=-1)
    return np.nan_to_num(f, nan=0.0, posinf=10.0, neginf=-10.0)


class ResidualAnomalyDetector:
    """Isolation Forest on residual features plus a score threshold tuned on validation data."""

    def __init__(self, seed: int = 0, threshold: float = 0.6):
        self.model = IsolationForest(n_estimators=200, random_state=seed, n_jobs=-1)
        self.threshold = threshold

    def fit(self, F: np.ndarray, stride: int = 3) -> "ResidualAnomalyDetector":
        self.model.fit(F[:, ::stride].reshape(-1, F.shape[-1]))
        return self

    def score(self, F: np.ndarray) -> np.ndarray:
        """Higher = more anomalous, shape (apps, slots)."""
        return -self.model.score_samples(F.reshape(-1, F.shape[-1])).reshape(F.shape[:2])

    def detect(self, F: np.ndarray, y: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Returns (is_anomaly, kind) where kind is +1 spike, -1 drop, 0 none."""
        above = y > q[quantile_index(0.95)]
        below = y < q[quantile_index(0.05)]
        flag = (self.score(F) > self.threshold) & (above | below)
        return flag, np.where(flag & above, 1, np.where(flag & below, -1, 0))

    def save(self, path) -> None:
        joblib.dump({"model": self.model, "threshold": self.threshold}, path)

    @classmethod
    def load(cls, path) -> "ResidualAnomalyDetector":
        d = cls()
        obj = joblib.load(path)
        d.model, d.threshold = obj["model"], obj["threshold"]
        return d


def inject(y: np.ndarray, rng: np.random.Generator, window: slice, n_events: int = 3):
    """Inject events per app into ``window``. Returns (y_injected, events)."""
    y = y.copy()
    events = []
    for a in range(y.shape[0]):
        starts = rng.choice(np.arange(window.start + 24, window.stop - 40), size=n_events, replace=False)
        for s in starts:
            kind = rng.choice(["spike", "drop", "surge"])
            if kind == "spike":
                d, mag = rng.integers(1, 4), rng.uniform(2.5, 6.0)
                y[a, s:s + d] *= mag
            elif kind == "drop":
                d, mag = rng.integers(1, 4), rng.uniform(0.05, 0.3)
                y[a, s:s + d] *= mag
            else:  # gradual, sustained rise: expected high demand, not an anomaly
                d, mag = rng.integers(24, 37), rng.uniform(1.5, 2.5)
                y[a, s:s + d] *= np.concatenate([np.linspace(1, mag, d // 2), np.full(d - d // 2, mag)])
            events.append({"app": a, "start": int(s), "dur": int(d), "kind": str(kind)})
    return y, events


def event_metrics(flag: np.ndarray, events: list[dict], offset: int) -> dict:
    """Event-level scoring. ``flag`` covers slots [offset, offset + flag.shape[1]).

    recall: share of injected spike/drop events with at least one flagged slot inside them.
    precision: share of alert runs (contiguous flagged slots per app) overlapping an injected
      spike/drop. Real traffic also has genuine unlabelled anomalies, so this is a lower bound.
    surge_false_alarm: share of benign gradual surges that triggered any alert.
    """
    hit = np.zeros_like(flag, dtype=bool)
    detected = surges_alarmed = n_anom = n_surge = 0
    for e in events:
        a, s0 = e["app"], e["start"] - offset
        s1 = s0 + e["dur"]
        if s0 < 0 or s1 > flag.shape[1]:
            continue
        if e["kind"] == "surge":
            n_surge += 1
            surges_alarmed += bool(flag[a, s0:s1].any())
        else:
            n_anom += 1
            detected += bool(flag[a, s0:s1].any())
            hit[a, s0:s1] = True
    runs = tp_runs = 0
    for a in range(flag.shape[0]):
        f = flag[a].astype(np.int8)
        starts = np.flatnonzero(np.diff(np.concatenate([[0], f])) == 1)
        ends = np.flatnonzero(np.diff(np.concatenate([f, [0]])) == -1) + 1
        for r0, r1 in zip(starts, ends):
            runs += 1
            tp_runs += bool(hit[a, r0:r1].any())
    precision = tp_runs / max(runs, 1)
    recall = detected / max(n_anom, 1)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 4),
        "surge_false_alarm": round(surges_alarmed / max(n_surge, 1), 4),
        "alerts": runs,
    }


def _raw_threshold(y: np.ndarray, sl: slice, k: float) -> np.ndarray:
    """Classic detector: value above k x trailing-hour mean, or below 1/k of it."""
    trail = np.stack([np.nanmean(y[:, t - 12:t], axis=1) for t in range(sl.start, sl.stop)], axis=1)
    raw = y[:, sl]
    return (raw > k * trail) | (raw < trail / k)


def evaluate() -> dict:
    wl = load_workload(scaling_study=True)
    scale = features.app_scale(wl.demand_mean)
    model = XGBForecaster.load(1)

    q = model.predict(wl.demand_peak, wl.demand_mean, scale)
    det = ResidualAnomalyDetector().fit(residual_features(wl.demand_peak, q, scale)[:, config.TRAIN][:, 24:])

    def injected(window: slice, seed: int):
        y, events = inject(wl.demand_peak, np.random.default_rng(seed), window)
        ratio = np.divide(y, wl.demand_peak, out=np.ones_like(y), where=wl.demand_peak > 0)
        qq = model.predict(y, wl.demand_mean * ratio, scale)  # re-forecast from the corrupted history
        score = det.score(residual_features(y, qq, scale)[:, window])
        outside = (y[:, window] > qq[quantile_index(0.95), :, window]) | (y[:, window] < qq[quantile_index(0.05), :, window])
        return y, events, score, outside

    # Tune on validation days.
    yv, ev, sv, ov = injected(config.VAL, seed=1)
    candidates = [(thr, event_metrics((sv > thr) & ov, ev, config.VAL.start))
                  for thr in np.quantile(sv, np.linspace(0.80, 0.999, 60))]
    det.threshold = float(max(candidates, key=lambda c: c[1]["f1"])[0])
    k_best = max(((k, event_metrics(_raw_threshold(yv, config.VAL, k), ev, config.VAL.start))
                  for k in (1.5, 2, 2.5, 3, 4, 5)), key=lambda c: c[1]["f1"])[0]

    # Report on test days.
    sl = config.TEST
    yt, et, st, ot = injected(sl, seed=2)
    report = {
        "method": "event-level; thresholds tuned on validation days (seed 1), reported on test days (seed 2)",
        "test_events": {k: sum(e["kind"] == k for e in et) for k in ("spike", "drop", "surge")},
        "residual_iforest": {**event_metrics((st > det.threshold) & ot, et, sl.start), "threshold": round(det.threshold, 4)},
        "ablation_iforest_without_band_check": event_metrics(st > det.threshold, et, sl.start),
        "ablation_forecast_band_only": event_metrics(ot, et, sl.start),
        "raw_threshold_baseline": {**event_metrics(_raw_threshold(yt, sl, k_best), et, sl.start), "k": k_best},
    }
    config.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    det.save(config.ARTIFACTS / "anomaly.joblib")
    config.REPORTS.mkdir(exist_ok=True)
    (config.REPORTS / "anomaly_eval.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    evaluate()
