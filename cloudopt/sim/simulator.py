"""Discrete-time capacity simulator shared by baselines, forecast-driven policies and RL.

Model (one decision per 5-minute slot, vectorised over apps):

* Each app runs N replicas of a fixed size (vCPU, GB). Capacity = N * vCPU busy workers,
  assuming one busy execution uses one vCPU (``cpu_per_worker``).
* At the start of slot t a policy chooses a desired replica count using only data observed
  up to slot t-1. Scale-downs apply immediately; scale-ups requested at t take effect at
  t + ``provision_delay`` (replica start-up time).
* Utilisation is judged on the busiest minute of the slot. An SLA violation is a slot where
  that peak utilisation exceeds ``sla_util`` (latency degrades sharply as a server approaches
  saturation).
* Cost = running replicas * replica hourly price * slot length.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cloudopt import config
from cloudopt.data import Workload
from cloudopt.sim.pricing import DEFAULT_PRICING, PricingModel

VCPU_SIZES = np.array([0.25, 0.5, 1.0, 2.0, 4.0])


@dataclass
class SimConfig:
    target_util: float = 0.7  # utilisation autoscalers aim for
    sla_util: float = 0.9  # peak-minute utilisation above this counts as an SLA violation
    provision_delay: int = 0  # slots before a scale-up becomes effective
    min_replicas: int = 1
    max_replicas: int = 10_000
    cpu_per_worker: float = 1.0
    mem_gb_per_vcpu: float = 2.0
    min_replicas_at_median: int = 3  # sizing rule, see choose_replica_vcpu
    pricing: PricingModel = field(default_factory=lambda: DEFAULT_PRICING)

    @property
    def slot_hours(self) -> float:
        return config.SLOT_MINUTES / 60.0


def choose_replica_vcpu(train_median_demand: np.ndarray, cfg: SimConfig) -> np.ndarray:
    """Pick each app's replica size: the largest size that still needs at least
    ``min_replicas_at_median`` replicas at median demand, so there is room to scale both ways."""
    need = train_median_demand[:, None] * cfg.cpu_per_worker / (VCPU_SIZES[None, :] * cfg.target_util)
    ok = need >= cfg.min_replicas_at_median
    # Index of the largest qualifying size; fall back to the smallest size.
    idx = np.where(ok.any(axis=1), ok.shape[1] - 1 - np.argmax(ok[:, ::-1], axis=1), 0)
    return VCPU_SIZES[idx]


@dataclass
class Context:
    """What a policy may see when deciding for slot t: history strictly before t."""

    t: int
    wl: Workload
    cfg: SimConfig
    replica_vcpu: np.ndarray  # (apps,)
    running: np.ndarray  # (apps,) replicas running in slot t-1
    history_replicas: np.ndarray  # (apps, slots) filled up to t-1

    def capacity_workers(self, replicas: np.ndarray) -> np.ndarray:
        return replicas * self.replica_vcpu / self.cfg.cpu_per_worker

    def replicas_for(self, demand_workers: np.ndarray, util: float | None = None) -> np.ndarray:
        """Replicas needed to serve ``demand_workers`` at ``util`` (default: target)."""
        u = self.cfg.target_util if util is None else util
        return np.ceil(demand_workers * self.cfg.cpu_per_worker / (self.replica_vcpu * u) - 1e-9)


@dataclass
class SimResult:
    name: str
    start: int
    replicas: np.ndarray  # (apps, T) running replicas
    util_mean: np.ndarray  # (apps, T)
    util_peak: np.ndarray  # (apps, T)
    violation: np.ndarray  # (apps, T) bool
    cost: np.ndarray  # (apps, T) USD
    replica_vcpu: np.ndarray  # (apps,)


def simulate(policy, wl: Workload, cfg: SimConfig, start: int, end: int,
             replica_vcpu: np.ndarray | None = None, warmup: int = config.SLOTS_PER_DAY) -> SimResult:
    """Run ``policy`` over [start, end). The policy also runs for ``warmup`` slots before
    ``start`` (unscored) so every strategy enters the window in its own steady state."""
    if replica_vcpu is None:
        replica_vcpu = choose_replica_vcpu(np.median(wl.demand_mean[:, config.TRAIN], axis=1), cfg)
    n = wl.n_apps
    first = max(start - warmup, 1)
    hist = np.zeros((n, wl.n_slots))
    desired_hist = np.zeros((n, wl.n_slots))
    ctx = Context(first, wl, cfg, replica_vcpu, np.zeros(n), hist)
    if hasattr(policy, "reset"):
        policy.reset(ctx)

    # Cold start at the beginning of the warm-up: sized for the last observed peak.
    running = np.clip(ctx.replicas_for(wl.demand_peak[:, first - 1]), cfg.min_replicas, cfg.max_replicas)
    hist[:, first - 1] = running
    desired_hist[:, :first] = running[:, None]

    for t in range(first, end):
        ctx.t, ctx.running = t, running
        desired = np.clip(np.ceil(policy.decide(ctx) - 1e-9), cfg.min_replicas, cfg.max_replicas)
        desired_hist[:, t] = desired
        arrived = desired_hist[:, t - cfg.provision_delay] if t - cfg.provision_delay >= 0 else desired
        # Scale-down is immediate; scale-up is capped by what was requested `delay` slots ago.
        running = np.minimum(desired, np.maximum(running, arrived))
        hist[:, t] = running

    reps = hist[:, start:end]
    cap = reps * replica_vcpu[:, None] / cfg.cpu_per_worker
    util_mean = wl.demand_mean[:, start:end] / cap
    util_peak = wl.demand_peak[:, start:end] / cap
    price = np.array([cfg.pricing.replica_hour(v, v * cfg.mem_gb_per_vcpu) for v in replica_vcpu])
    cost = reps * price[:, None] * cfg.slot_hours
    return SimResult(getattr(policy, "name", type(policy).__name__), start, reps, util_mean, util_peak,
                     util_peak > cfg.sla_util, cost, replica_vcpu)


def summarize(res: SimResult, baseline: SimResult | None = None) -> dict:
    total = float(res.cost.sum())
    out = {
        "policy": res.name,
        "total_cost_usd": round(total, 2),
        "violation_rate": float(res.violation.mean()),
        "apps_with_violation_share": float(res.violation.any(axis=1).mean()),
        "mean_util": float(np.mean(np.minimum(res.util_mean, 5.0))),
        "median_util": float(np.median(res.util_mean)),
        "avg_replicas": float(res.replicas.mean()),
        "scaling_actions_per_app_day": float(
            (np.diff(res.replicas, axis=1) != 0).sum() / res.replicas.shape[0]
            / (res.replicas.shape[1] / config.SLOTS_PER_DAY)
        ),
    }
    if baseline is not None:
        base = float(baseline.cost.sum())
        out["savings_vs_baseline_pct"] = round(100 * (base - total) / base, 2)
        # Totals are dominated by a few very large apps; the per-app median is the typical tenant view.
        app_base = baseline.cost.sum(axis=1)
        out["median_app_savings_pct"] = round(float(np.median(100 * (app_base - res.cost.sum(axis=1)) / app_base)), 2)
    return out
