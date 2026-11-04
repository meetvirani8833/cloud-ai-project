"""Package everything the live product needs into ml/artifacts/demo/.

The hosted backend should not need the 90 MB processed dataset or any training code paths,
so this writes a compact bundle:
  demo/workload.npz       demand matrices for the demo services (36 x 4032)
  demo/catalog.json       tenant/service display names, replica sizes, baseline provisioning
  demo/xgb_h{h}.ubj       XGBoost quantile boosters for the live forecast horizons
  demo/anomaly.joblib     Isolation Forest + tuned threshold (written by cloudopt.anomaly)
  demo/rl_policy.onnx     optional PPO policy (written by cloudopt.rl.train --export-onnx)

Usage:
    python -m cloudopt.demo_bundle [--tenants 8]
"""

from __future__ import annotations

import argparse
import json
import shutil

import numpy as np

from cloudopt import config
from cloudopt.catalog import select_demo
from cloudopt.data import load_workload
from cloudopt.forecast import features
from cloudopt.sim.policies import StaticPeak
from cloudopt.sim.simulator import Context, SimConfig, choose_replica_vcpu

DEMO_DIR = config.ARTIFACTS / "demo"
LIVE_HORIZONS = (1, 3, 6, 12)


def build(n_tenants: int = 8) -> None:
    study = load_workload(scaling_study=True)
    demo = select_demo(study, n_tenants)
    # Reorder rows to the catalogue's order (tenant by tenant).
    pos = np.array([np.where(study.app_ids == a)[0][0] for a in demo["app_id"]])
    wl = study.subset(pos)

    cfg = SimConfig()
    vcpu = choose_replica_vcpu(np.median(wl.demand_mean[:, config.TRAIN], axis=1), cfg)
    static = StaticPeak()
    static.reset(Context(0, wl, cfg, vcpu, np.zeros(wl.n_apps), np.zeros((wl.n_apps, wl.n_slots))))

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        DEMO_DIR / "workload.npz",
        demand_mean=wl.demand_mean.astype(np.float32),
        demand_peak=wl.demand_peak.astype(np.float32),
        rps=wl.rps.astype(np.float32),
        mem_mb=wl.mem_mb.astype(np.float32),
        # Same per-app normalisation the forecasters were trained with.
        scale=features.app_scale(wl.demand_mean),
    )
    catalog = {
        "slots_per_day": config.SLOTS_PER_DAY,
        "n_slots": config.N_SLOTS,
        "live_horizons": list(LIVE_HORIZONS),
        "services": [
            {
                "app_id": r.app_id,
                "tenant_id": r.tenant_id,
                "tenant_name": r.tenant_name,
                "service_name": r.service_name,
                "trigger": r.trigger,
                "source_owner_hash": r.HashOwner,
                "source_app_hash": r.HashApp,
                "replica_vcpu": float(vcpu[i]),
                "replica_mem_gb": float(vcpu[i] * cfg.mem_gb_per_vcpu),
                "baseline_replicas": int(max(static.replicas[i], 1)),
            }
            for i, r in enumerate(demo.itertuples())
        ],
    }
    (DEMO_DIR / "catalog.json").write_text(json.dumps(catalog, indent=2))

    needed = [(f"xgb_h{h}.ubj", f"python -m cloudopt.forecast.xgb --horizons {h}") for h in LIVE_HORIZONS]
    needed.append(("anomaly.joblib", "python -m cloudopt.anomaly"))
    for name, how in needed:
        src = config.ARTIFACTS / name
        if not src.exists():
            raise SystemExit(f"Missing {src}; create it first: {how}")
        shutil.copy(src, DEMO_DIR / name)
    if (config.ARTIFACTS / "rl_policy.onnx").exists():  # optional: enables the RL decision engine
        shutil.copy(config.ARTIFACTS / "rl_policy.onnx", DEMO_DIR / "rl_policy.onnx")
    print(f"bundle written to {DEMO_DIR}: {wl.n_apps} services, {demo['tenant_id'].nunique()} tenants")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenants", type=int, default=8)
    build(ap.parse_args().tenants)
