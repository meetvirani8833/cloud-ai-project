"""Parse the Kaggle Borg 2019 sample export into a typed, flat Parquet file.

The export stores nested fields as stringified Python literals
(e.g. "{'cpus': 0.0011, 'memory': 0.00098}") and the CPU histograms as
numpy-printed arrays ("[0.00074 0.00091 ...]"). This script flattens them so
downstream code never has to call ast.literal_eval.

Usage:
    python ingestion/parse_kaggle.py [--src data/raw/borg_traces_data.csv]
                                     [--out data/processed/borg_parsed.parquet]
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

import numpy as np
import pandas as pd

# Borg encodes "before trace start" as 0 and "after trace end" as INT64_MAX.
TIME_BEFORE_TRACE = 0
TIME_AFTER_TRACE = np.iinfo(np.int64).max

USAGE_DICT_COLS = ["resource_request", "average_usage", "maximum_usage", "random_sample_usage"]
LIST_COLS = ["constraint", "start_after_collection_ids"]
HIST_COLS = ["cpu_usage_distribution", "tail_cpu_usage_distribution"]


def _literal(s):
    if not isinstance(s, str):
        return None
    return ast.literal_eval(s)


def _hist(s):
    if not isinstance(s, str):
        return None
    return np.array(s.strip("[]").split(), dtype=np.float64)


def parse(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop(columns=["Unnamed: 0"])

    for col in USAGE_DICT_COLS:
        parsed = df[col].map(_literal)
        df[f"{col}_cpus"] = parsed.map(lambda d: d.get("cpus") if d else None).astype("float64")
        df[f"{col}_memory"] = parsed.map(lambda d: d.get("memory") if d else None).astype("float64")
        df = df.drop(columns=col)

    # These are numpy-printed lists (elements separated by whitespace/newlines, not
    # commas), so literal_eval fails on multi-element values. Only the count is used.
    df["constraint_count"] = df["constraint"].map(lambda s: s.count("{") if isinstance(s, str) else 0)
    df["start_after_collection_ids_count"] = df["start_after_collection_ids"].map(
        lambda s: len(s.strip("[]").split()) if isinstance(s, str) else 0
    )
    df = df.drop(columns=LIST_COLS)

    for col in HIST_COLS:
        hists = df[col].map(_hist)
        # Percentile bins are the most useful summary: median and max of the histogram.
        df[f"{col}_p50"] = hists.map(lambda a: float(np.median(a)) if a is not None and a.size else None)
        df[f"{col}_max"] = hists.map(lambda a: float(a.max()) if a is not None and a.size else None)
        df = df.drop(columns=col)

    df["time_before_trace"] = df["time"] == TIME_BEFORE_TRACE
    df["time_after_trace"] = df["time"] == TIME_AFTER_TRACE
    df.loc[df["time_before_trace"] | df["time_after_trace"], "time"] = pd.NA
    df["time"] = df["time"].astype("Int64")

    # Usage window length; 300 s is a full Borg sampling window.
    df["window_s"] = (df["end_time"] - df["start_time"]) / 1e6
    # Utilisation relative to what the job asked for: the over-provisioning signal.
    df["cpu_util_of_request"] = df["average_usage_cpus"] / df["resource_request_cpus"]
    df["mem_util_of_request"] = df["average_usage_memory"] / df["resource_request_memory"]
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/raw/borg_traces_data.csv")
    ap.add_argument("--out", default="data/processed/borg_parsed.parquet")
    args = ap.parse_args()

    df = parse(pd.read_csv(args.src))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    print(f"wrote {args.out}: {df.shape[0]:,} rows x {df.shape[1]} cols")


if __name__ == "__main__":
    main()
