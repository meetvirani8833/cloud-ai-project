"""Leak-free feature tensors for pooled multi-app forecasting.

For target slot t and horizon h the forecast is issued at the end of slot t - h, so every
feature uses data at or before slot ``t - h``. Features are built for all (app, t) pairs at
once with array shifts, which keeps both training and dashboard-time inference vectorised.

Demand is normalised per app by its training-period mean and log-transformed, so one pooled
model can serve apps whose load differs by four orders of magnitude.
"""

from __future__ import annotations

import numpy as np

from cloudopt import config

DAY = config.SLOTS_PER_DAY
WEEK = 7 * DAY


def app_scale(demand_mean: np.ndarray) -> np.ndarray:
    return np.maximum(demand_mean[:, config.TRAIN].mean(axis=1), 1e-3)


def to_z(y: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return np.log1p(y / scale[:, None])


def from_z(z: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return np.expm1(z) * scale[:, None]


def _shift(m: np.ndarray, k: int) -> np.ndarray:
    """out[:, t] = m[:, t - k]; NaN where t - k < 0."""
    out = np.full_like(m, np.nan)
    if k < m.shape[1]:
        out[:, k:] = m[:, : m.shape[1] - k]
    return out


def _rolling(m: np.ndarray, window: int, fn) -> np.ndarray:
    """out[:, t] = fn(m[:, t - window + 1 : t + 1]) (trailing, inclusive of t)."""
    from numpy.lib.stride_tricks import sliding_window_view

    out = np.full_like(m, np.nan)
    out[:, window - 1:] = fn(sliding_window_view(m, window, axis=1), axis=-1)
    return out


def build(peak: np.ndarray, mean: np.ndarray, scale: np.ndarray, horizon: int) -> tuple[np.ndarray, list[str]]:
    """Return features (apps, slots, F) for predicting z_peak[:, t] from data up to t - horizon."""
    zp, zm = to_z(peak, scale), to_z(mean, scale)
    n, T = zp.shape
    feats: dict[str, np.ndarray] = {}

    # Most recent observations relative to the issue time (t - h).
    for k in range(12):
        feats[f"peak_lag_{k}"] = _shift(zp, horizon + k)
    feats["mean_lag_0"] = _shift(zm, horizon)
    for w in (6, 12, 36, 144):
        feats[f"peak_rmean_{w}"] = _shift(_rolling(zp, w, np.mean), horizon)
        feats[f"peak_rmax_{w}"] = _shift(_rolling(zp, w, np.max), horizon)
    feats["peak_rstd_12"] = _shift(_rolling(zp, 12, np.std), horizon)

    # Seasonal references aligned to the *target* slot (only if already observed at issue time).
    for name, lag in (("day", DAY), ("week", WEEK)):
        if lag >= horizon:
            feats[f"peak_same_slot_prev_{name}"] = _shift(zp, lag)
            feats[f"peak_prev_{name}_rmax_3"] = _shift(_rolling(zp, 3, np.max), lag - 1) if lag - 1 >= horizon else _shift(zp, lag)

    # Calendar of the target slot. The trace has no dates; day index mod 7 stands in for weekday.
    t = np.arange(T)
    slot_of_day = t % DAY
    feats["tod_sin"] = np.broadcast_to(np.sin(2 * np.pi * slot_of_day / DAY), (n, T))
    feats["tod_cos"] = np.broadcast_to(np.cos(2 * np.pi * slot_of_day / DAY), (n, T))
    feats["day_mod_7"] = np.broadcast_to((t // DAY) % 7, (n, T)).astype(float)

    # Static app descriptors.
    feats["log_scale"] = np.broadcast_to(np.log(scale)[:, None], (n, T))

    names = list(feats)
    X = np.stack([np.asarray(feats[k], dtype=np.float32) for k in names], axis=-1)
    return X, names
