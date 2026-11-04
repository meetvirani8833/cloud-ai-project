"""Cost vs. SLA-violation trade-off curves: the centrepiece evaluation.

Every strategy has a knob that trades cost for safety (the reactive autoscaler's target
utilisation, a naive forecast's safety margin, CloudOpt's forecast quantile). A single default
setting can make any strategy look good or bad, so each knob is swept and the resulting curve
compared on the same held-out workload (test days 13-14). Headline numbers interpolate the
curves at equal SLA-violation rates.

Also adds the forecast-driven policies at their default settings to comparison_delay0.json.

Usage (after cloudopt.forecast.xgb and cloudopt.anomaly have run):
    python -m cloudopt.eval.frontier
"""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np

from cloudopt import config
from cloudopt.anomaly import ResidualAnomalyDetector, residual_features
from cloudopt.data import load_workload
from cloudopt.eval.compare import plot, run as run_baselines
from cloudopt.forecast import baselines as fb
from cloudopt.forecast import features
from cloudopt.forecast.xgb import quantile_index
from cloudopt.sim.policies import CloudOpt, Oracle, Predictive, Reactive, StaticPeak
from cloudopt.sim.simulator import SimConfig, choose_replica_vcpu, simulate, summarize


def _study_forecasts(study_ids: np.ndarray, horizon: int) -> np.ndarray:
    all_ids = load_workload(scaling_study=False).app_ids
    q = np.load(config.ARTIFACTS / f"xgb_forecast_h{horizon}.npy").astype(np.float64)
    return q[:, np.isin(all_ids, study_ids)]


def _interp_cost(points: list[dict], v: float) -> float | None:
    """Cost of a curve at violation rate v (linear interpolation; None if v is out of range)."""
    pts = sorted(points, key=lambda p: p["violation_rate"])
    xs = np.array([p["violation_rate"] for p in pts])
    ys = np.array([p["cost"] for p in pts])
    if v < xs.min() - 1e-12 or v > xs.max() + 1e-12:
        return None
    return float(np.interp(v, xs, ys))


def compute_headline(curves: list[dict], static_pt: dict) -> dict:
    """Compare curves only where they overlap: interpolating outside a curve's range would be
    inventing data. Reactive never gets below ~2.8% violations, so that is where it is compared."""
    by = {c["policy"]: c["points"] for c in curves}
    ours, reactive = by["cloudopt"], by["reactive_hpa"]
    out: dict = {}
    v_r = min(p["violation_rate"] for p in reactive)
    c_r, c_o = _interp_cost(reactive, v_r), _interp_cost(ours, v_r)
    if c_r and c_o:
        out[f"Cheaper than reactive autoscaling at equal SLA ({v_r:.1%} violations)"] = round(100 * (c_r - c_o) / c_r, 1)
    safe = [p for p in ours if p["violation_rate"] <= 0.01]
    if safe:
        best = min(safe, key=lambda p: p["cost"])
        out["Savings vs static peak provisioning at ≤1% violations"] = round(100 * (static_pt["cost"] - best["cost"]) / static_pt["cost"], 1)
    rl = by.get("rl_ppo")
    if rl:
        diffs = []
        for p in rl:
            c_o = _interp_cost(ours, p["violation_rate"])
            if c_o:
                diffs.append(100 * (p["cost"] - c_o) / c_o)
        if diffs:
            out["RL agent cost vs CloudOpt policy at equal SLA (mean over RL points)"] = round(float(np.mean(diffs)), 1)
    fo = by.get("predictive_xgb")
    if fo:
        v = 0.01
        c_f, c_o1 = _interp_cost(fo, v), _interp_cost(ours, v)
        if c_f and c_o1:
            out["Extra cost of CloudOpt's stability layers vs forecast-only at 1% violations"] = round(100 * (c_o1 - c_f) / c_f, 1)
    return out


def add_rl_curve(rep: dict) -> None:
    """Attach the PPO policies (one point per violation penalty) from reports/rl_eval.json."""
    path = config.REPORTS / "rl_eval.json"
    rep["curves"] = [c for c in rep["curves"] if c["policy"] != "rl_ppo"]
    if not path.exists():
        return
    rl = json.loads(path.read_text())
    pts = [{"knob": p["violation_penalty"], "cost": p["total_cost_usd"], "violation_rate": p["violation_rate"]}
           for p in rl["policies"]]
    lams = [p["knob"] for p in pts]
    rep["curves"].append({"policy": "rl_ppo", "label": f"RL agent, PPO (violation penalty {min(lams):g}–{max(lams):g})", "points": pts})


def headline_only() -> dict:
    """Recompute headline numbers (and the RL curve) from an existing frontier.json (no re-simulation)."""
    path = config.REPORTS / "frontier.json"
    rep = json.loads(path.read_text())
    add_rl_curve(rep)
    rep["headline"] = compute_headline(rep["curves"], rep["meta"]["static_peak"])
    path.write_text(json.dumps(rep, indent=2))
    _plot(rep)
    print(json.dumps(rep["headline"], indent=2))
    return rep


def run() -> dict:
    wl = load_workload(scaling_study=True)
    base_cfg = SimConfig()
    vcpu = choose_replica_vcpu(np.median(wl.demand_mean[:, config.TRAIN], axis=1), base_cfg)
    start, end = config.TEST.start, config.TEST.stop
    q1 = _study_forecasts(wl.app_ids, 1)
    hold = [(_study_forecasts(wl.app_ids, h), h) for h in (3, 6)]

    det = ResidualAnomalyDetector.load(config.ARTIFACTS / "anomaly.joblib")
    scale = features.app_scale(wl.demand_mean)
    F = residual_features(wl.demand_peak, np.nan_to_num(q1), scale)
    flags, kind = det.detect(F, wl.demand_peak, np.nan_to_num(q1))
    spikes = flags & (kind > 0)

    def point(policy, cfg=base_cfg, knob=None):
        r = simulate(policy, wl, cfg, start, end, replica_vcpu=vcpu)
        return {"knob": knob, "cost": round(float(r.cost.sum()), 2), "violation_rate": float(r.violation.mean())}, r

    curves = []
    reactive_pts = [point(Reactive(), replace(base_cfg, target_util=u), u)[0] for u in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)]
    curves.append({"policy": "reactive_hpa", "label": "Reactive autoscaler (target util. 30–80%)", "points": reactive_pts})
    mm = fb.moving_max(wl.demand_peak, 1, window=6)
    curves.append({"policy": "naive_moving_max_30m", "label": "Naive 30-min max (margin 1.0–1.5×)",
                   "points": [point(Predictive(mm, margin=m), knob=m)[0] for m in (1.0, 1.1, 1.25, 1.5)]})
    knobs = [(0.5, 1.0), (0.9, 1.0), (0.95, 1.0), (0.99, 1.0), (0.99, 1.15), (0.99, 1.3)]
    curves.append({"policy": "predictive_xgb", "label": "XGBoost forecast only (p50–p99)",
                   "points": [point(Predictive(q1[quantile_index(qq)], margin=m), knob=f"p{int(qq * 100)}x{m}")[0] for qq, m in knobs]})
    curves.append({"policy": "cloudopt_no_lookahead", "label": "Ablation: CloudOpt without look-ahead scale-in",
                   "points": [point(CloudOpt(q1, quantile_index(qq), spikes, margin=m), knob=f"p{int(qq * 100)}x{m}")[0] for qq, m in knobs]})
    curves.append({"policy": "cloudopt", "label": "CloudOpt (forecast + anomaly guard + look-ahead scale-in)",
                   "points": [point(CloudOpt(q1, quantile_index(qq), spikes, margin=m, hold=hold), knob=f"p{int(qq * 100)}x{m}")[0] for qq, m in knobs]})
    static_res = simulate(StaticPeak(), wl, base_cfg, start, end, replica_vcpu=vcpu)

    static_pt, oracle_pt = point(StaticPeak())[0], point(Oracle())[0]
    headline = compute_headline(curves, static_pt)

    report = {
        "meta": {"apps": wl.n_apps, "window": "test days 13-14", "provision_delay_slots": 0,
                 "static_peak": static_pt, "oracle": oracle_pt},
        "curves": curves,
        "headline": headline,
    }
    add_rl_curve(report)
    report["headline"] = compute_headline(report["curves"], static_pt)
    (config.REPORTS / "frontier.json").write_text(json.dumps(report, indent=2))
    _plot(report)

    # Default-setting rows for the comparison table.
    run_baselines(0)
    path = config.REPORTS / "comparison_delay0.json"
    comp = json.loads(path.read_text())
    for pol in (Predictive(q1[quantile_index(0.9)], name="predictive_xgb"),
                CloudOpt(q1, quantile_index(0.9), spikes, name="predictive_xgb_anomaly", hold=hold)):
        r = simulate(pol, wl, base_cfg, start, end, replica_vcpu=vcpu)
        comp["results"].insert(-1, summarize(r, static_res))
    path.write_text(json.dumps(comp, indent=2))
    plot(comp["results"], comp["meta"], config.FIGURES / "comparison_delay0.png")
    print(json.dumps({"headline": headline}, indent=2))
    for c in curves:
        print(c["policy"], [(p["knob"], round(100 * p["violation_rate"], 2), round(p["cost"])) for p in c["points"]])
    print("static", static_pt, "oracle", oracle_pt)
    return report


def _plot(report: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    for c in report["curves"]:
        xs = [100 * p["violation_rate"] for p in c["points"]]
        ys = [p["cost"] for p in c["points"]]
        ax.plot(xs, ys, marker="s" if c["policy"] == "rl_ppo" else "o", ls="--" if c["policy"] == "rl_ppo" else "-",
                lw=2.4 if c["policy"] == "cloudopt" else 1.5, label=c["label"])
    s, o = report["meta"]["static_peak"], report["meta"]["oracle"]
    ax.scatter([100 * o["violation_rate"]], [o["cost"]], marker="*", s=160, c="k", label="Oracle (perfect foresight)", zorder=5)
    ax.set_xlabel("SLA violations (% of app-slots)")
    ax.set_ylabel("Cost over test window (USD)")
    ax.set_title(f"Cost vs. SLA trade-off ({report['meta']['apps']} apps; static peak = ${s['cost']:,.0f})")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(config.FIGURES / "frontier.png", dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    import sys

    headline_only() if "--headline-only" in sys.argv else run()
