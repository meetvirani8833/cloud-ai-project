"""Naive forecasters on (apps, slots) matrices.

Every function returns a matrix ``f`` where ``f[:, t]`` predicts ``y[:, t]`` using only
``y[:, : t - horizon + 1]`` (i.e. the forecast is issued ``horizon`` slots ahead).
Slots without enough history are NaN.
"""

from __future__ import annotations

import numpy as np

from cloudopt import config


def persistence(y: np.ndarray, horizon: int = 1) -> np.ndarray:
    f = np.full_like(y, np.nan)
    f[:, horizon:] = y[:, :-horizon]
    return f


def seasonal_naive(y: np.ndarray, horizon: int = 1, season: int = config.SLOTS_PER_DAY) -> np.ndarray:
    """Same slot one season earlier (needs season >= horizon)."""
    f = np.full_like(y, np.nan)
    f[:, season:] = y[:, :-season]
    return f


def moving_max(y: np.ndarray, horizon: int = 1, window: int = 6) -> np.ndarray:
    """Max over the last ``window`` observed slots: a conservative short-term forecast."""
    f = np.full_like(y, np.nan)
    for t in range(window + horizon - 1, y.shape[1]):
        last = t - horizon + 1
        f[:, t] = y[:, last - window: last].max(axis=1)
    return f
