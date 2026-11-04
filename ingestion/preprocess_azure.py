"""Build the modelling dataset from the Azure Functions Trace 2019.

Inputs (data/raw/azure 2019 pubkic dataset/azure-functions-dataset-2019/):
  invocations_per_function_md.feather   per-function invocation counts, 1-minute bins, 14 days
  function_durations_percentiles.feather per-function daily execution-time stats (ms)
  app_memory_percentiles.feather         per-app daily allocated-memory stats (MB), days 1-12 only

Outputs (data/processed/):
  azure_apps.parquet        catalog of selected apps: ids, tenant, trigger mix, volume, selection stats
  azure_app_minute.parquet  app_id, t (minute 0..20159), invocations
  azure_app_daily.parquet   app_id, day, duration and memory stats
  azure_app_5min.parquet    app_id, slot (0..4031), invocations, rps, concurrency, mem_mb  <- main table

Modelling conventions (documented in docs/data_profile_azure.md):
  * Tenant = HashOwner (Azure subscription); service = HashApp.
  * Functions are summed to app level: in Azure Functions the app is the unit of resource allocation.
  * Compute demand ("concurrency") = invocations/min * mean duration (s) / 60, the average number of
    busy workers by Little's law. CPU utilisation is later derived as concurrency / provisioned capacity.
  * Memory for days 13-14 is missing in the public trace; filled with the app's median and flagged.

Usage:
    python ingestion/preprocess_azure.py [--min-rate 10] [--max-empty 0.05]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path("data/raw/azure 2019 pubkic dataset/azure-functions-dataset-2019")
OUT = Path("data/processed")
DAYS = 14
MIN_PER_DAY = 1440
MINUTES = [str(i) for i in range(1, MIN_PER_DAY + 1)]


def load_app_minutes() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (app x 20160 invocation matrix, per-app trigger mix)."""
    fn = pd.read_feather(RAW / "invocations_per_function_md.feather")
    triggers = (
        fn.groupby(["HashApp", "Trigger"])[MINUTES].sum().sum(axis=1).unstack(fill_value=0)
    )
    triggers = triggers.div(triggers.sum(axis=1), axis=0).add_prefix("trigger_share_")

    by_day = fn.groupby(["HashOwner", "HashApp", "day"])[MINUTES].sum().astype(np.int64)
    del fn
    # Wide day blocks -> one 20,160-minute row per app; missing days are zero traffic.
    full = by_day.unstack("day").reindex(columns=range(1, DAYS + 1), level=1)
    full = full.reorder_levels([1, 0], axis=1).sort_index(axis=1, level=[0, 1], sort_remaining=False)
    ordered = [(d, m) for d in range(1, DAYS + 1) for m in MINUTES]
    full = full.reindex(columns=pd.MultiIndex.from_tuples(ordered)).fillna(0).astype(np.int64)
    full.columns = range(DAYS * MIN_PER_DAY)
    present_days = by_day.groupby(level=[0, 1]).size().rename("present_days")
    return full.join(present_days), triggers


def app_daily_stats() -> pd.DataFrame:
    dur = pd.read_feather(RAW / "function_durations_percentiles.feather")
    dur = dur.drop_duplicates(["HashOwner", "HashApp", "HashFunction", "day"])
    dur["weighted"] = dur["Average"] * dur["Count"]
    d = dur.groupby(["HashApp", "day"]).agg(
        dur_weighted=("weighted", "sum"),
        dur_count=("Count", "sum"),
        duration_p99_ms=("percentile_Average_99", "max"),
    )
    d["duration_ms"] = d["dur_weighted"] / d["dur_count"].where(d["dur_count"] > 0)
    d = d[["duration_ms", "duration_p99_ms"]]

    mem = pd.read_feather(RAW / "app_memory_percentiles.feather")
    mem = mem.drop_duplicates(["HashOwner", "HashApp", "day"])
    m = mem.set_index(["HashApp", "day"])[["AverageAllocatedMb", "AverageAllocatedMb_pct99"]]
    m.columns = ["mem_mb", "mem_p99_mb"]
    return d.join(m, how="outer")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-rate", type=float, default=10.0, help="Min mean invocations/minute")
    ap.add_argument("--max-empty", type=float, default=0.05, help="Max share of zero-invocation minutes")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    print("loading invocations ...")
    apps, triggers = load_app_minutes()
    series = apps.drop(columns="present_days").to_numpy()
    stats = pd.DataFrame(
        {
            "present_days": apps["present_days"].to_numpy(),
            "total_invocations": series.sum(axis=1),
            "mean_per_min": series.mean(axis=1),
            "empty_share": (series == 0).mean(axis=1),
        },
        index=apps.index,
    )

    daily = app_daily_stats()
    dur_days = daily["duration_ms"].notna().groupby(level=0).sum()
    mem_days = daily["mem_mb"].notna().groupby(level=0).sum()
    stats["duration_days"] = stats.index.get_level_values("HashApp").map(dur_days).fillna(0).astype(int)
    stats["memory_days"] = stats.index.get_level_values("HashApp").map(mem_days).fillna(0).astype(int)

    keep = (
        (stats["present_days"] == DAYS)
        & (stats["empty_share"] <= args.max_empty)
        & (stats["mean_per_min"] >= args.min_rate)
        & (stats["duration_days"] == DAYS)
        & (stats["memory_days"] >= 10)
    )
    print(f"apps total={len(stats):,} selected={int(keep.sum()):,}")
    sel = stats[keep].sort_values("total_invocations", ascending=False).reset_index()

    # Stable human-readable ids, ranked by volume; hashes kept for traceability.
    owner_rank = sel.groupby("HashOwner")["total_invocations"].sum().rank(ascending=False, method="first")
    sel["tenant_id"] = sel["HashOwner"].map(owner_rank).astype(int).map(lambda r: f"tenant_{r:04d}")
    sel["app_id"] = [f"app_{i + 1:04d}" for i in range(len(sel))]
    sel = sel.join(triggers, on="HashApp").fillna({c: 0.0 for c in triggers.columns})
    sel["apps_in_tenant"] = sel.groupby("tenant_id")["app_id"].transform("size")
    sel.to_parquet(OUT / "azure_apps.parquet", index=False)

    # Minute table.
    mat = apps.loc[list(zip(sel["HashOwner"], sel["HashApp"]))].drop(columns="present_days").to_numpy()
    n_apps, n_min = mat.shape
    minute = pd.DataFrame(
        {
            "app_id": np.repeat(sel["app_id"].to_numpy(), n_min),
            "t": np.tile(np.arange(n_min, dtype=np.int32), n_apps),
            "invocations": mat.ravel().astype(np.int32),
        }
    )
    minute.to_parquet(OUT / "azure_app_minute.parquet", index=False)

    # Daily stats for selected apps, memory gap-filled with the app median.
    d = daily.loc[daily.index.get_level_values(0).isin(sel["HashApp"])].reset_index()
    d = d.merge(sel[["HashApp", "app_id"]], on="HashApp").drop(columns="HashApp")
    full_days = pd.MultiIndex.from_product([sel["app_id"], range(1, DAYS + 1)], names=["app_id", "day"])
    d = d.set_index(["app_id", "day"]).reindex(full_days).reset_index()
    d["mem_filled"] = d["mem_mb"].isna()
    for c in ["mem_mb", "mem_p99_mb"]:
        d[c] = d[c].fillna(d.groupby("app_id")[c].transform("median"))
    d.to_parquet(OUT / "azure_app_daily.parquet", index=False)

    # 5-minute modelling table with derived compute demand.
    minute["day"] = minute["t"] // MIN_PER_DAY + 1
    minute = minute.merge(d[["app_id", "day", "duration_ms", "mem_mb"]], on=["app_id", "day"])
    minute["busy_s"] = minute["invocations"] * minute["duration_ms"] / 1000.0
    minute["slot"] = minute["t"] // 5
    five = minute.groupby(["app_id", "slot"], sort=True).agg(
        invocations=("invocations", "sum"),
        busy_s=("busy_s", "sum"),
        busy_s_peak_min=("busy_s", "max"),
        mem_mb=("mem_mb", "first"),
    ).reset_index()
    five["rps"] = five["invocations"] / 300.0
    five["concurrency"] = five["busy_s"] / 300.0  # mean busy workers in the window
    # Busiest single minute in the window: SLA is judged on this, not the 5-min mean.
    five["concurrency_peak"] = five.pop("busy_s_peak_min") / 60.0
    five["slot"] = five["slot"].astype(np.int32)
    five.drop(columns="busy_s").to_parquet(OUT / "azure_app_5min.parquet", index=False)

    print(
        f"wrote {len(sel):,} apps across {sel['tenant_id'].nunique():,} tenants; "
        f"{len(minute):,} minute rows; {len(five):,} 5-min rows"
    )


if __name__ == "__main__":
    main()
