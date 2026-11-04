"""Baseline vs. system comparison on the held-out test days (13-14).

Produces the centrepiece evaluation: cost and SLA violations of every scaling strategy on the
same replayed workload. Later model-driven policies (XGBoost/LSTM forecasts, RL) register
here too, so the chart always compares everything on identical demand.

Usage:
    python -m cloudopt.eval.compare [--delay 0] [--pricing aws_fargate]
Outputs:
    reports/comparison_delay{d}.json
    reports/figures/comparison_delay{d}.png
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from cloudopt import config
from cloudopt.data import load_workload
from cloudopt.forecast import baselines as fb
from cloudopt.sim.policies import Oracle, Predictive, Reactive, StaticObserved, StaticPeak
from cloudopt.sim.pricing import PRESETS
from cloudopt.sim.simulator import SimConfig, simulate, summarize


def build_policies(wl, cfg: SimConfig) -> list:
    h = 1 + cfg.provision_delay  # capacity requested now lands h slots later
    y = wl.demand_peak
    return [
        StaticPeak(),
        StaticObserved(),
        Reactive(),
        Predictive(fb.persistence(y, h), name="naive_persistence"),
        Predictive(fb.moving_max(y, h, window=6), name="naive_moving_max_30m"),
        Oracle(),
    ]


def run(delay: int = 0, pricing: str = "aws_fargate") -> list[dict]:
    wl = load_workload(scaling_study=True)
    cfg = SimConfig(provision_delay=delay, pricing=PRESETS[pricing])
    start, end = config.TEST.start, config.TEST.stop
    results = {p.name: simulate(p, wl, cfg, start, end) for p in build_policies(wl, cfg)}
    base = results["static_peak"]
    rows = [summarize(r, base) for r in results.values()]

    meta = {
        "apps": wl.n_apps,
        "tenants": int(len(np.unique(wl.tenant_ids))),
        "window": "test days 13-14",
        "provision_delay_slots": delay,
        "pricing": PRESETS[pricing].to_dict(),
        "target_util": cfg.target_util,
        "sla_util": cfg.sla_util,
    }
    config.REPORTS.mkdir(exist_ok=True)
    out = config.REPORTS / f"comparison_delay{delay}.json"
    out.write_text(json.dumps({"meta": meta, "results": rows}, indent=2))
    plot(rows, meta, config.FIGURES / f"comparison_delay{delay}.png")
    return rows


def plot(rows: list[dict], meta: dict, path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [r["policy"] for r in rows]
    cost = [r["total_cost_usd"] for r in rows]
    viol = [100 * r["violation_rate"] for r in rows]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.5))
    a1.barh(names, cost, color="#2f6f8f")
    a1.set_xlabel("Cost over test window (USD)")
    a1.invert_yaxis()
    a2.barh(names, viol, color="#c0504d")
    a2.set_xlabel("SLA violations (% of app-slots)")
    a2.invert_yaxis()
    a2.set_yticklabels([])
    fig.suptitle(
        f"{meta['apps']} apps / {meta['tenants']} tenants, {meta['window']}, "
        f"provision delay {meta['provision_delay_slots']} slot(s), {meta['pricing']['label']}"
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=int, default=0)
    ap.add_argument("--pricing", default="aws_fargate", choices=list(PRESETS))
    args = ap.parse_args()
    rows = run(args.delay, args.pricing)
    cols = ["policy", "total_cost_usd", "savings_vs_baseline_pct", "median_app_savings_pct", "violation_rate",
            "apps_with_violation_share", "median_util", "avg_replicas", "scaling_actions_per_app_day"]
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(f"{r[c]:.4f}" if isinstance(r[c], float) else str(r[c]) for c in cols))


if __name__ == "__main__":
    main()
