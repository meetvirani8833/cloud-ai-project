"""Scaling policies. Each exposes ``decide(ctx) -> desired replicas (apps,)`` and sees only
data before slot ``ctx.t``.

Baselines evaluated against the AI system:
  StaticPeak      provisioned once for the historical peak at target utilisation ("just to be safe")
  StaticObserved  provisioned once so that typical utilisation matches real over-provisioning
                  measured in the Google Borg 2019 trace (median usage / request = 0.217)
  Reactive        Kubernetes-HPA-style threshold autoscaler on last slot's utilisation
  Predictive      provisions for a forecast of the coming slot's peak demand
  Oracle          perfect foresight, a lower bound on cost with no violations (not achievable)
"""

from __future__ import annotations

import numpy as np

from cloudopt import config
from cloudopt.sim.simulator import Context

BORG_MEDIAN_USAGE_TO_REQUEST = 0.217  # docs/data_profile.md


class StaticPeak:
    name = "static_peak"

    def __init__(self, headroom: float = 1.0):
        self.headroom = headroom

    def reset(self, ctx: Context) -> None:
        peak = ctx.wl.demand_peak[:, config.TRAIN].max(axis=1)
        self.replicas = ctx.replicas_for(peak * self.headroom)

    def decide(self, ctx: Context) -> np.ndarray:
        return self.replicas


class StaticObserved:
    name = "static_observed"

    def __init__(self, usage_to_request: float = BORG_MEDIAN_USAGE_TO_REQUEST):
        self.ratio = usage_to_request

    def reset(self, ctx: Context) -> None:
        median = np.median(ctx.wl.demand_mean[:, config.TRAIN], axis=1)
        self.replicas = ctx.replicas_for(median, util=self.ratio)

    def decide(self, ctx: Context) -> np.ndarray:
        return self.replicas


class Reactive:
    """HPA rule: desired = ceil(current * observed_util / target), with a 10% tolerance band and
    a scale-down stabilisation window (keep the max recommendation over the last few slots)."""

    name = "reactive_hpa"

    def __init__(self, tolerance: float = 0.1, downscale_window: int = 3):
        self.tolerance = tolerance
        self.window = downscale_window

    def reset(self, ctx: Context) -> None:
        self.recent: list[np.ndarray] = []

    def decide(self, ctx: Context) -> np.ndarray:
        observed = ctx.wl.demand_mean[:, ctx.t - 1]
        util = observed * ctx.cfg.cpu_per_worker / (ctx.running * ctx.replica_vcpu)
        ratio = util / ctx.cfg.target_util
        rec = np.where(np.abs(ratio - 1) <= self.tolerance, ctx.running, np.ceil(ctx.running * ratio - 1e-9))
        self.recent = (self.recent + [rec])[-self.window:]
        stabilised = np.max(self.recent, axis=0)
        return np.where(rec > ctx.running, rec, stabilised)


def _cover(m: np.ndarray, t: int, delay: int) -> np.ndarray:
    """Max over slots t..t+delay: capacity requested now must hold until a scale-up lands."""
    return np.nanmax(m[:, t: min(t + delay + 1, m.shape[1])], axis=1)


class Predictive:
    """Provision for forecast peak demand from now until newly requested capacity lands.

    ``forecast`` is an (apps, slots) matrix where forecast[:, s] predicts demand_peak[:, s]
    using data up to s - 1 - provision_delay, so every value read at decision time t uses
    data up to t - 1 only. Built by cloudopt.forecast.
    """

    def __init__(self, forecast: np.ndarray, name: str = "predictive", margin: float = 1.0):
        self.forecast = forecast
        self.name = name
        self.margin = margin

    def decide(self, ctx: Context) -> np.ndarray:
        return ctx.replicas_for(_cover(self.forecast, ctx.t, ctx.cfg.provision_delay) * self.margin)


class CloudOpt:
    """The policy the live product runs (backend/app/services/engine.py), reproduced offline.

    * scale-out: provisions for the ``quantile`` forecast of the next slot (x ``margin``), plus an
      anomaly guard: after a detector-flagged spike, assume it persists one more slot;
    * scale-in: only down to what the longer-horizon forecasts (``hold``, e.g. 15 and 30 min) also
      allow, so capacity is not released just before a forecast burst; and only after
      ``patience`` consecutive lower recommendations.

    ``q`` is (quantiles, apps, slots) from XGBForecaster.predict with horizon 1 + delay;
    ``hold`` is a list of (quantile matrix, horizon) pairs; ``spike_flags`` (apps, slots) marks
    detector-flagged spikes on observed slots.
    """

    def __init__(self, q: np.ndarray, quantile_idx: int, spike_flags: np.ndarray | None = None,
                 margin: float = 1.0, patience: int = 3, name: str = "cloudopt",
                 hold: list[tuple[np.ndarray, int]] | None = None):
        self.q, self.qi, self.flags = q, quantile_idx, spike_flags
        self.margin, self.patience, self.name = margin, patience, name
        self.hold = hold or []

    def reset(self, ctx: Context) -> None:
        self.count = np.zeros(ctx.wl.n_apps, dtype=int)
        self.held = np.zeros(ctx.wl.n_apps)

    def decide(self, ctx: Context) -> np.ndarray:
        need = _cover(self.q[self.qi], ctx.t, ctx.cfg.provision_delay) * self.margin
        if self.flags is not None:
            last = ctx.t - 1
            need = np.where(self.flags[:, last], np.maximum(need, ctx.wl.demand_peak[:, last]), need)
        hold_need = need
        for qh, h in self.hold:
            col = ctx.t - 1 + h  # forecast issued with data up to t-1 for slot t-1+h
            if col < qh.shape[-1]:
                hold_need = np.fmax(hold_need, qh[self.qi][:, col] * self.margin)
        up = ctx.replicas_for(need)
        floor = ctx.replicas_for(hold_need)
        scale_out = up > ctx.running
        lower = ~scale_out & (floor < ctx.running)
        self.count = np.where(lower, self.count + 1, 0)
        self.held = np.where(lower, np.maximum(self.held, floor), 0)
        release = lower & (self.count >= self.patience)
        out = np.where(scale_out, up, ctx.running)
        out = np.where(release, self.held, out)
        self.count = np.where(release, 0, self.count)
        self.held = np.where(release, 0, self.held)
        return out


class Oracle:
    name = "oracle"

    def decide(self, ctx: Context) -> np.ndarray:
        return ctx.replicas_for(_cover(ctx.wl.demand_peak, ctx.t, ctx.cfg.provision_delay))
