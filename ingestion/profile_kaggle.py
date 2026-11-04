"""Profile the parsed Kaggle Borg sample and test whether it supports time-series work.

Reads the output of parse_kaggle.py and prints the statistics recorded in
docs/data_profile.md. Re-run this whenever the source file changes.

Usage:
    python ingestion/profile_kaggle.py [--src data/processed/borg_parsed.parquet]
"""

from __future__ import annotations

import argparse

import pandas as pd

SLOT_US = 300_000_000  # 5 minutes in microseconds
HOUR_US = 3_600_000_000
TRACE_HOURS = 744  # 31 days


def section(title: str) -> None:
    print(f"\n== {title} ==")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/processed/borg_parsed.parquet")
    args = ap.parse_args()
    df = pd.read_parquet(args.src)

    section("Shape and cardinality")
    print(f"rows={len(df):,} cols={df.shape[1]}")
    for c in ["collection_id", "machine_id", "user", "cluster"]:
        print(f"  unique {c}: {df[c].nunique():,}")
    span_days = (df["end_time"].max() - df["start_time"].min()) / 1e6 / 86400
    print(f"  usage-window span: {span_days:.2f} days")

    section("Time sentinels (event time)")
    print(f"  time==0 (before trace): {int(df['time_before_trace'].sum()):,}")
    print(f"  time==INT64_MAX (after trace): {int(df['time_after_trace'].sum()):,}")

    section("Events")
    print(df["event"].value_counts().to_string())

    section("Missingness")
    na = df.isna().sum()
    print(na[na > 0].to_string())
    cpi_missing = df.groupby("event")["cycles_per_instruction"].apply(lambda s: s.isna().mean())
    print("  CPI missing rate by event:\n" + cpi_missing.round(3).to_string())

    section("Over-provisioning signal (average CPU usage / CPU request)")
    u = df["cpu_util_of_request"].dropna()
    print(u.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(3).to_string())
    print(f"  share of rows using <50% of request: {(u < 0.5).mean():.1%}")

    section("Tenant concentration")
    vc = df["user"].value_counts()
    print(f"  top-10 tenants hold {vc.head(10).sum() / len(df):.1%} of rows")
    for k in [1, 5, 10, 20]:
        print(f"  tenant #{k}: {vc.iloc[k - 1]:,} rows (~{vc.iloc[k - 1] / TRACE_HOURS:.1f}/hour)")

    section("Temporal structure test")
    df["hour"] = df["start_time"] // HOUR_US
    df["slot"] = df["start_time"] // SLOT_US
    hourly = df.groupby("hour").agg(n=("slot", "size"), cpu=("average_usage_cpus", "sum"))
    hourly = hourly.reindex(range(TRACE_HOURS), fill_value=0)
    print(f"  rows/hour min={hourly.n.min()} median={hourly.n.median():.0f} max={hourly.n.max()}")
    for col in ["n", "cpu"]:
        s = hourly[col]
        print(f"  aggregate {col}: acf(1h)={s.autocorr(1):.3f} acf(24h)={s.autocorr(24):.3f} acf(168h)={s.autocorr(168):.3f}")
    for k in [1, 10]:
        user = vc.index[k - 1]
        s = df[df["user"] == user].groupby("hour")["average_usage_cpus"].sum().reindex(range(TRACE_HOURS), fill_value=0)
        print(f"  tenant #{k}: empty hours={int((s == 0).sum())}/{TRACE_HOURS} acf(24h)={s.autocorr(24):.3f}")

    section("Per-entity continuity (distinct 5-min windows)")
    jobs = df.groupby("collection_id")["slot"].nunique()
    inst = df.groupby(["collection_id", "instance_index"]).agg(rows=("slot", "size"), slots=("slot", "nunique"), machines=("machine_id", "nunique"))
    print(f"  max distinct windows for any job: {jobs.max()} (of {TRACE_HOURS * 12} possible)")
    print(f"  max distinct windows for any instance: {inst.slots.max()}")
    top = inst.sort_values("rows", ascending=False).head(1)
    print(f"  most-repeated instance: {int(top.rows.iloc[0]):,} rows over {int(top.slots.iloc[0])} windows on {int(top.machines.iloc[0]):,} machines")


if __name__ == "__main__":
    main()
