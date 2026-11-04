"""In-memory model runtime for the live loop: telemetry source, forecasters, anomaly detector.

Leakage guard: ``obs_*`` matrices hold only slots that have been replayed; every later slot is
NaN, so live inference cannot see the future even by accident.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cloudopt.anomaly import ResidualAnomalyDetector, residual_features
from cloudopt.forecast.xgb import QUANTILES, XGBForecaster, quantile_index
from cloudopt.rl.env import RLData, apply_action, observe

from app.config import REPO_ROOT

BUNDLE_DIR = REPO_ROOT / "ml" / "artifacts" / "demo"
# Enough history for the longest feature (one-week seasonal lag plus rolling windows).
FEATURE_HISTORY = 7 * 288 + 160


@dataclass
class SlotForecast:
    horizon: int
    target_slot: int
    q: np.ndarray  # (len(QUANTILES), services)


class Runtime:
    def __init__(self, bundle_dir: Path = BUNDLE_DIR):
        self.catalog = json.loads((bundle_dir / "catalog.json").read_text())
        w = np.load(bundle_dir / "workload.npz")
        self.src_mean = w["demand_mean"].astype(np.float64)
        self.src_peak = w["demand_peak"].astype(np.float64)
        self.rps = w["rps"].astype(np.float64)
        self.mem_mb = w["mem_mb"].astype(np.float64)
        self.scale = w["scale"].astype(np.float64)
        self.service_ids = [s["app_id"] for s in self.catalog["services"]]
        self.index = {sid: i for i, sid in enumerate(self.service_ids)}
        self.n, self.T = self.src_peak.shape
        self.horizons = list(self.catalog["live_horizons"])
        self.models = {h: XGBForecaster.load(h, bundle_dir / f"xgb_h{h}.ubj") for h in self.horizons}
        self.detector = ResidualAnomalyDetector.load(bundle_dir / "anomaly.joblib")
        self.obs_mean = np.full_like(self.src_mean, np.nan)
        self.obs_peak = np.full_like(self.src_peak, np.nan)

        # Optional RL agent (PPO policy exported to ONNX; no PyTorch needed at runtime).
        self.rl = None
        rl_path = bundle_dir / "rl_policy.onnx"
        if rl_path.exists():
            import onnxruntime as ort

            self.rl = ort.InferenceSession(str(rl_path), providers=["CPUExecutionProvider"])
        vcpu = np.array([s["replica_vcpu"] for s in self.catalog["services"]])
        base = np.array([s["baseline_replicas"] for s in self.catalog["services"]])
        # Live inputs for the agent: 1-step forecasts and detector-flagged spikes per slot.
        self.rl_data = RLData(self.obs_peak, self.obs_mean, np.zeros((len(QUANTILES), self.n, self.T)),
                              np.zeros((self.n, self.T), dtype=bool), vcpu, base * vcpu)

    # --- telemetry -------------------------------------------------------------------------
    def reset_observations(self, upto: int) -> None:
        """Mark slots [0, upto) as observed with the untouched source values."""
        self.obs_mean[:] = np.nan
        self.obs_peak[:] = np.nan
        self.obs_mean[:, :upto] = self.src_mean[:, :upto]
        self.obs_peak[:, :upto] = self.src_peak[:, :upto]

    def record_live(self, forecast_next: "SlotForecast | None", t: int, spikes: np.ndarray) -> None:
        """Keep what the RL agent observes in step with the live loop."""
        self.rl_data.spikes[:, t] = spikes
        if forecast_next is not None:
            self.rl_data.q[:, :, forecast_next.target_slot] = forecast_next.q

    def rl_action(self, i: int, t_next: int, running: int) -> int | None:
        """Replica count the RL agent chooses for service i in slot t_next, or None if unavailable."""
        if self.rl is None or t_next < 3:
            return None
        obs = observe(self.rl_data, np.array([i]), t_next, np.array([float(running)]))
        logits = self.rl.run(None, {"obs": obs})[0]
        return int(apply_action(np.array([float(running)]), np.array([int(np.argmax(logits[0]))]))[0])

    def observe(self, t: int, multiplier: np.ndarray) -> None:
        self.obs_mean[:, t] = self.src_mean[:, t] * multiplier
        self.obs_peak[:, t] = self.src_peak[:, t] * multiplier

    # --- inference -------------------------------------------------------------------------
    def _window(self, t_last: int, horizon: int) -> tuple[int, slice]:
        """History window ending at observed slot t_last, padded so target t_last+horizon fits."""
        start = max(0, t_last - FEATURE_HISTORY)
        end = min(self.T, t_last + horizon + 1)
        return start, slice(start, end)

    def forecast(self, t_last: int, horizon: int) -> SlotForecast | None:
        """Quantile forecast for slot t_last + horizon using observations up to t_last."""
        target = t_last + horizon
        if target >= self.T:
            return None
        start, sl = self._window(t_last, horizon)
        q = self.models[horizon].predict_at(self.obs_peak[:, sl], self.obs_mean[:, sl], self.scale, [target - start])
        return SlotForecast(horizon, target, q[:, :, 0])

    def anomalies(self, t: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Score slot t against the 5-minute forecast issued at t-1.
        Returns (flag, kind, expected_p50, expected_p95) per service."""
        start, sl = self._window(t - 1, 1)
        peak, mean = self.obs_peak[:, sl], self.obs_mean[:, sl]
        cols = [t - 1 - start, t - start]  # previous slot is needed for the lagged residual feature
        q = self.models[1].predict_at(peak, mean, self.scale, cols)
        y = peak[:, cols]
        F = residual_features(y, q, self.scale)
        flag, kind = self.detector.detect(F[:, 1:], y[:, 1:], q[:, :, 1:])
        return flag[:, 0], kind[:, 0], q[quantile_index(0.5), :, 1], q[quantile_index(0.95), :, 1]


def exceed_probability(q: np.ndarray, threshold: np.ndarray) -> np.ndarray:
    """P(demand > threshold) per service from quantile forecasts q (len(QUANTILES), services)."""
    out = np.empty(q.shape[1])
    for i in range(q.shape[1]):
        vals = q[:, i]
        if threshold[i] <= vals[0]:
            out[i] = 1 - QUANTILES[0] / 2
        elif threshold[i] >= vals[-1]:
            out[i] = (1 - QUANTILES[-1]) / 2
        else:
            out[i] = 1 - np.interp(threshold[i], vals, QUANTILES)
    return np.clip(out, 0.0, 1.0)
