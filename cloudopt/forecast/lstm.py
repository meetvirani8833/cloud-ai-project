"""LSTM sequence forecasters (PyTorch) for the longer horizons, with quantile outputs.

Two direct multi-step models, both trained on the pooled, per-app-normalised series of all
728 apps (training days 1-10, early stopping on days 11-12, metrics on test days 13-14):

  lstm_1h   input: last 72 five-minute slots (6 h)    -> output: next 12 slots (the next hour)
  lstm_24h  input: last 72 hourly values (3 days)     -> output: next 24 hours

Targets are z = log1p(peak / app_scale) as in the XGBoost model; outputs are p10/p50/p90
trained with the pinball loss. Each is compared at its far horizon with XGBoost and naive
baselines (the 24 h comparison is on hourly means of 5-minute peaks, so the XGBoost 24 h
5-minute forecasts are averaged to hours).

Usage (CPU is fine; Colab GPU is faster):
    python -m cloudopt.forecast.lstm [--epochs 6]
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import torch
from torch import nn

from cloudopt import config
from cloudopt.data import load_workload
from cloudopt.forecast import features
from cloudopt.forecast.metrics import forecast_metrics

QS = torch.tensor([0.1, 0.5, 0.9])
DAY = config.SLOTS_PER_DAY


class SeqQuantile(nn.Module):
    def __init__(self, n_in: int, horizon: int, hidden: int = 64, layers: int = 2):
        super().__init__()
        self.lstm = nn.LSTM(n_in, hidden, num_layers=layers, batch_first=True, dropout=0.1)
        self.head = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(), nn.Linear(hidden, horizon * len(QS)))
        self.h = horizon

    def forward(self, x):
        out, _ = self.lstm(x)
        q = self.head(out[:, -1]).view(-1, self.h, len(QS))
        # Monotone quantiles: p10 <= p50 <= p90 by construction.
        return torch.cat([q[..., 1:2] - nn.functional.softplus(q[..., :1]), q[..., 1:2],
                          q[..., 1:2] + nn.functional.softplus(q[..., 2:])], dim=-1)


def pinball(pred, y):
    d = y.unsqueeze(-1) - pred
    return torch.maximum(QS * d, (QS - 1) * d).mean()


def _inputs(z: np.ndarray, period: int) -> np.ndarray:
    """Per-step input channels: value plus time-of-day sin/cos (aligned to each step)."""
    n, T = z.shape
    tod = 2 * np.pi * (np.arange(T) % period) / period
    return np.stack([z, np.broadcast_to(np.sin(tod), (n, T)), np.broadcast_to(np.cos(tod), (n, T))], axis=-1).astype(np.float32)


def _windows(X: np.ndarray, z: np.ndarray, lookback: int, horizon: int, apps: np.ndarray, issue: np.ndarray):
    """Windows for (app, issue) pairs, issue = last observed index:
    x = X[a, i-lookback+1 : i+1], y = z[a, i+1 : i+1+horizon]."""
    off = np.arange(-lookback + 1, 1)
    xs = X[apps[:, None], issue[:, None] + off[None, :]]
    ys = z[apps[:, None], issue[:, None] + 1 + np.arange(horizon)[None, :]]
    return xs.astype(np.float32), ys.astype(np.float32)


def _pairs(n_apps: int, issues: np.ndarray, k: int | None, rng) -> tuple[np.ndarray, np.ndarray]:
    a, i = np.meshgrid(np.arange(n_apps), issues, indexing="ij")
    a, i = a.ravel(), i.ravel()
    if k is not None and len(a) > k:
        sel = rng.choice(len(a), size=k, replace=False)
        a, i = a[sel], i[sel]
    return a, i


def fit(X, z, lookback, horizon, train_issue, val_issue, epochs, seed=0, max_train=80_000, max_val=40_000):
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    xtr, ytr = (torch.from_numpy(t) for t in _windows(X, z, lookback, horizon, *_pairs(X.shape[0], train_issue, max_train, rng)))
    xva, yva = (torch.from_numpy(t) for t in _windows(X, z, lookback, horizon, *_pairs(X.shape[0], val_issue, max_val, rng)))
    model = SeqQuantile(X.shape[-1], horizon)
    opt = torch.optim.Adam(model.parameters(), lr=2e-3)
    best, best_state, history = float("inf"), None, []
    for ep in range(epochs):
        model.train()
        perm = torch.randperm(len(xtr))
        for b in range(0, len(xtr), 512):
            idx = perm[b: b + 512]
            loss = pinball(model(xtr[idx]), ytr[idx])
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        model.eval()
        with torch.no_grad():
            v = float(np.mean([pinball(model(xva[i:i + 4096]), yva[i:i + 4096]).item() for i in range(0, len(xva), 4096)]))
        history.append(round(v, 5))
        print(f"  epoch {ep + 1}/{epochs} val pinball {v:.5f}", flush=True)
        if v < best:
            best, best_state = v, {k: t.clone() for k, t in model.state_dict().items()}
    model.load_state_dict(best_state)
    return model, history


@torch.no_grad()
def predict(model, X, lookback, issue) -> np.ndarray:
    """(apps, len(issue), horizon, 3) in z units, every app predicted at every issue time."""
    model.eval()
    out = [model(torch.from_numpy(X[:, i - lookback + 1: i + 1].astype(np.float32))).numpy() for i in issue]
    return np.stack(out, axis=1)


def _hourly(m: np.ndarray) -> np.ndarray:
    return m.reshape(m.shape[0], -1, 12).mean(axis=2)


def evaluate(epochs: int) -> dict:
    torch.set_num_threads(6)
    wl = load_workload(scaling_study=False)
    study = np.isin(wl.app_ids, load_workload(scaling_study=True).app_ids)
    scale = features.app_scale(wl.demand_mean)
    report: dict = {}

    # ---------- 1 hour ahead, 5-minute resolution
    t0 = time.time()
    z = features.to_z(wl.demand_peak, scale)
    X = _inputs(z, DAY)
    L, H = 72, 12
    tr_issue = np.arange(L, config.TRAIN.stop - H, 3)
    va_issue = np.arange(config.VAL.start, config.VAL.stop - H, 6)
    model, hist = fit(X, z, L, H, tr_issue, va_issue, epochs)
    # Test: issue at every slot so slot s is predicted from s-12 (same as XGBoost h12).
    te_issue = np.arange(config.TEST.start - H, config.TEST.stop - H)
    q = predict(model, X, L, te_issue)[:, :, H - 1, :]  # 12-step-ahead
    f = features.from_z(q[..., 1], scale)
    y = wl.demand_peak[:, config.TEST]
    xgb12 = np.load(config.ARTIFACTS / "xgb_forecast_h12.npy")[2][:, config.TEST]  # p50
    persist = wl.demand_peak[:, config.TEST.start - H: config.TEST.stop - H]
    lo, hi = features.from_z(q[..., 0], scale), features.from_z(q[..., 2], scale)
    report["h12 (1 hour)"] = {
        subset: {
            "lstm_p50": forecast_metrics(y[m], f[m], scale[m]),
            "xgboost_p50": forecast_metrics(y[m], xgb12[m], scale[m]),
            "persistence": forecast_metrics(y[m], persist[m], scale[m]),
            "lstm_p10_p90_coverage": float(np.mean((y[m] >= lo[m]) & (y[m] <= hi[m]))),
        }
        for subset, m in (("all_apps", np.ones(len(study), bool)), ("scaling_study_apps", study))
    }
    report["h12 (1 hour)"]["val_loss_history"] = hist
    report["h12 (1 hour)"]["train_seconds"] = round(time.time() - t0, 1)
    torch.save(model.state_dict(), config.ARTIFACTS / "lstm_1h.pt")
    print(json.dumps({"h12": report["h12 (1 hour)"]["scaling_study_apps"]}, indent=1))

    # ---------- 24 hours ahead, hourly resolution (hourly mean of 5-minute peaks)
    t0 = time.time()
    yh = _hourly(wl.demand_peak)
    zh = features.to_z(yh, scale)
    Xh = _inputs(zh, 24)
    L, H = 72, 24
    tr_issue = np.arange(L, config.TRAIN.stop // 12 - H)
    va_issue = np.arange(config.VAL.start // 12, config.VAL.stop // 12 - H)
    model, hist = fit(Xh, zh, L, H, tr_issue, va_issue, epochs)
    te_issue = np.arange(config.TEST.start // 12 - H, config.TEST.stop // 12 - H)
    q = predict(model, Xh, L, te_issue)[:, :, H - 1, :]  # 24-hour-ahead
    f = features.from_z(q[..., 1], scale)
    ytest = yh[:, config.TEST.start // 12: config.TEST.stop // 12]
    xgb288 = _hourly(np.load(config.ARTIFACTS / "xgb_forecast_h288.npy")[2][:, config.TEST])
    seasonal = yh[:, config.TEST.start // 12 - 24: config.TEST.stop // 12 - 24]
    lo, hi = features.from_z(q[..., 0], scale), features.from_z(q[..., 2], scale)
    report["h24h (24 hours, hourly)"] = {
        subset: {
            "lstm_p50": forecast_metrics(ytest[m], f[m], scale[m]),
            "xgboost_p50_hourly_mean": forecast_metrics(ytest[m], xgb288[m], scale[m]),
            "seasonal_naive_1d": forecast_metrics(ytest[m], seasonal[m], scale[m]),
            "lstm_p10_p90_coverage": float(np.mean((ytest[m] >= lo[m]) & (ytest[m] <= hi[m]))),
        }
        for subset, m in (("all_apps", np.ones(len(study), bool)), ("scaling_study_apps", study))
    }
    report["h24h (24 hours, hourly)"]["val_loss_history"] = hist
    report["h24h (24 hours, hourly)"]["train_seconds"] = round(time.time() - t0, 1)
    torch.save(model.state_dict(), config.ARTIFACTS / "lstm_24h.pt")
    print(json.dumps({"h24": report["h24h (24 hours, hourly)"]["scaling_study_apps"]}, indent=1))

    (config.REPORTS / "forecast_lstm.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=4)
    evaluate(ap.parse_args().epochs)
