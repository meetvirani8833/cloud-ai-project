"""Forecast accuracy metrics computed over all (app, slot) pairs.

MAPE is reported as the project brief asks, but it explodes on near-zero demand. The primary
comparisons are WAPE (sum |error| / sum actual) and nMAE (absolute error divided by each app's
mean demand, averaged), which weights every app equally instead of letting the largest dominate.
"""

from __future__ import annotations

import numpy as np


def forecast_metrics(y: np.ndarray, f: np.ndarray, scale: np.ndarray, mape_floor: float = 0.1) -> dict:
    """y, f: (apps, slots); scale: (apps,) typical demand used for the scale-free nMAE."""
    ok = np.isfinite(f) & np.isfinite(y)
    err = f - y
    e = err[ok]
    yt = y[ok]
    nz = yt > mape_floor
    nmae = np.abs(err) / scale[:, None]
    return {
        "nmae": float(np.mean(nmae[ok])),
        "mae": float(np.mean(np.abs(e))),
        "rmse": float(np.sqrt(np.mean(e**2))),
        "mape_pct": float(100 * np.mean(np.abs(e[nz]) / yt[nz])),
        "wape_pct": float(100 * np.abs(e).sum() / yt.sum()),
        "n": int(ok.sum()),
    }
