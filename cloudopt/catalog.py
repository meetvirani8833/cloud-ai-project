"""Demo catalogue: which trace tenants/apps the product shows, and their display names.

The trace is anonymised (hashed owners and apps). For the product UI each selected owner gets
a clearly fictional company name and each app a service name derived from its trigger type.
The hashes are kept alongside so every number can be traced back to the source data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cloudopt import config
from cloudopt.data import Workload

COMPANY_NAMES = [
    "Northvale Analytics", "Bluefin Health", "Larkspur Logistics", "Quillstack", "Driftwood Retail",
    "Helio Learning", "Copperleaf Finance", "Mosaic Media", "Sable Systems", "Tidewater Travel",
    "Juniper Labs", "Fernhill Games", "Arcadia Insurance", "Brightpath HR", "Cobalt Ticketing",
    "Evergreen Foods",
]

SERVICE_NAMES = {
    "http": ["web-api", "checkout-api", "public-api", "auth-api", "search-api", "mobile-backend"],
    "queue": ["order-worker", "email-worker", "billing-worker", "sync-worker"],
    "event": ["event-ingest", "stream-processor", "telemetry-ingest"],
    "timer": ["scheduler", "report-cron", "cleanup-cron"],
    "orchestration": ["workflow-engine", "saga-orchestrator"],
    "storage": ["file-processor", "image-resizer"],
    "others": ["service"],
}


def dominant_trigger(apps: pd.DataFrame) -> pd.Series:
    shares = apps.filter(like="trigger_share_")
    return shares.idxmax(axis=1).str.replace("trigger_share_", "", regex=False)


def daily_autocorr(m: np.ndarray) -> np.ndarray:
    x = m[:, config.TRAIN]
    a, b = x[:, config.SLOTS_PER_DAY:], x[:, : -config.SLOTS_PER_DAY]
    a = a - a.mean(axis=1, keepdims=True)
    b = b - b.mean(axis=1, keepdims=True)
    den = np.sqrt((a**2).sum(axis=1) * (b**2).sum(axis=1))
    return np.where(den > 0, (a * b).sum(axis=1) / np.where(den > 0, den, 1), 0.0)


def select_demo(wl: Workload, n_tenants: int = 8) -> pd.DataFrame:
    """Pick tenants whose services show realistic, varied traffic for the live demo.

    Preference: multi-service tenants, request-driven triggers (http/queue/event), visible daily
    seasonality. Returns one row per selected app with display names.
    """
    apps = pd.read_parquet(config.DATA_PROCESSED / "azure_apps.parquet").set_index("app_id").loc[wl.app_ids]
    apps["trigger"] = dominant_trigger(apps).to_numpy()
    apps["acf_1d"] = daily_autocorr(wl.demand_mean)
    apps["request_driven"] = apps["trigger"].isin(["http", "queue", "event"])
    t = apps.groupby("tenant_id").agg(
        n=("trigger", "size"), acf=("acf_1d", "max"), req=("request_driven", "mean"),
    )
    t["score"] = 0.5 * np.minimum(t["n"], 4) / 4 + 0.3 * t["acf"].clip(0, 1) + 0.2 * t["req"]
    chosen = t.sort_values("score", ascending=False).head(n_tenants).index
    sel = apps[apps["tenant_id"].isin(chosen)].copy()
    order = {tid: i for i, tid in enumerate(chosen)}
    sel["tenant_rank"] = sel["tenant_id"].map(order)
    sel = sel.sort_values(["tenant_rank", "total_invocations"], ascending=[True, False])
    sel["tenant_name"] = sel["tenant_rank"].map(lambda i: COMPANY_NAMES[i % len(COMPANY_NAMES)])

    names = []
    for _, grp in sel.groupby("tenant_rank", sort=False):
        used: dict[str, int] = {}
        for trig in grp["trigger"]:
            pool = SERVICE_NAMES.get(trig, SERVICE_NAMES["others"])
            k = used.get(trig, 0)
            used[trig] = k + 1
            names.append(pool[k] if k < len(pool) else f"{pool[k % len(pool)]}-{k // len(pool) + 1}")
    sel["service_name"] = names
    return sel.reset_index()[[
        "app_id", "tenant_id", "tenant_name", "service_name", "trigger", "HashOwner", "HashApp", "acf_1d",
    ]]
