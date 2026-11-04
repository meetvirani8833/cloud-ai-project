"""Gymnasium environment for learning a replica-scaling policy.

One episode = one app over a contiguous window of 5-minute slots. At slot t the agent sees
only information available at the end of slot t-1 (recent demand, the XGBoost quantile
forecast for slot t, current capacity) and chooses a multiplicative change to its replica
count, which takes effect for slot t (provision delay 0, matching the simulator).

Reward per slot = -(capacity / static-peak capacity)      # spend, normalised per app
                  - violation_penalty * [peak util > SLA]  # performance
                  - churn_penalty * [replicas changed]     # stability

``observe`` is shared with RLPolicy so offline evaluation and training see identical inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

import gymnasium as gym
import numpy as np

from cloudopt import config
from cloudopt.forecast.xgb import quantile_index

ACTIONS = np.array([0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0])
OBS_DIM = 13


@dataclass
class RLData:
    """Arrays for a set of apps on a common slot axis (all (apps, slots) unless noted)."""

    peak: np.ndarray
    mean: np.ndarray
    q: np.ndarray  # (quantiles, apps, slots) 1-step forecasts
    spikes: np.ndarray  # bool, detector-flagged spikes
    vcpu: np.ndarray  # (apps,)
    static_cap: np.ndarray  # (apps,) static-peak capacity in workers, the cost normaliser


def observe(d: RLData, idx: np.ndarray, t: int, running: np.ndarray) -> np.ndarray:
    """Observation for apps ``idx`` deciding slot t with ``running`` replicas. Shape (len(idx), OBS_DIM)."""
    cap = np.maximum(running * d.vcpu[idx], 1e-6)
    r = lambda x: np.clip(np.nan_to_num(x / cap, nan=0.0), 0, 5)  # noqa: E731
    p50, p90, p99 = (d.q[quantile_index(a), idx, t] for a in (0.5, 0.9, 0.99))
    tod = 2 * np.pi * (t % config.SLOTS_PER_DAY) / config.SLOTS_PER_DAY
    obs = np.stack([
        r(p50), r(p90), r(p99),
        r(d.peak[idx, t - 1]), r(d.mean[idx, t - 1]), r(d.peak[idx, t - 2]), r(d.peak[idx, t - 3]),
        np.clip(np.nan_to_num(p90 / np.maximum(d.peak[idx, t - 1], 1e-6), nan=1.0), 0, 5),
        np.full(len(idx), np.sin(tod)), np.full(len(idx), np.cos(tod)),
        np.log1p(running) / 5,
        np.clip(cap / np.maximum(d.static_cap[idx], 1e-6), 0, 3),
        d.spikes[idx, t - 1].astype(float),
    ], axis=1)
    return obs.astype(np.float32)


def apply_action(running: np.ndarray, action: np.ndarray, lo: int = 1, hi: int = 10_000) -> np.ndarray:
    f = ACTIONS[action]
    new = np.round(running * f)
    # Any non-neutral action moves at least one replica.
    new = np.where((f > 1) & (new <= running), running + 1, new)
    new = np.where((f < 1) & (new >= running), running - 1, new)
    return np.clip(new, lo, hi)


class ScalingEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, data: RLData, window: slice, episode_len: int = 144, violation_penalty: float = 2.0,
                 churn_penalty: float = 0.01, sla_util: float = 0.9, seed: int | None = None):
        super().__init__()
        self.d, self.window, self.len = data, window, episode_len
        self.vp, self.cp, self.sla = violation_penalty, churn_penalty, sla_util
        self.observation_space = gym.spaces.Box(-5.0, 5.0, shape=(OBS_DIM,), dtype=np.float32)
        self.action_space = gym.spaces.Discrete(len(ACTIONS))
        self.rng = np.random.default_rng(seed)

    def _obs(self):
        return observe(self.d, np.array([self.app]), self.t, np.array([self.running]))[0]

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self.app = int(self.rng.integers(self.d.peak.shape[0]))
        lo = max(self.window.start, 3)
        self.t = int(self.rng.integers(lo, self.window.stop - self.len))
        self.end = self.t + self.len
        # Start from a sensible capacity: the last observed peak at 70% utilisation.
        self.running = float(max(1, np.ceil(self.d.peak[self.app, self.t - 1] / (self.d.vcpu[self.app] * 0.7))))
        return self._obs(), {}

    def step(self, action):
        prev = self.running
        self.running = float(apply_action(np.array([prev]), np.array([action]))[0])
        cap = self.running * self.d.vcpu[self.app]
        util = self.d.peak[self.app, self.t] / cap
        violation = float(util > self.sla)
        reward = -cap / self.d.static_cap[self.app] - self.vp * violation - self.cp * float(self.running != prev)
        self.t += 1
        done = self.t >= self.end
        return self._obs() if not done else np.zeros(OBS_DIM, np.float32), reward, done, False, {"violation": violation, "cost": cap}
