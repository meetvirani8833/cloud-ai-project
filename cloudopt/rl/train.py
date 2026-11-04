"""Train PPO scaling policies and evaluate them against rule-based baselines.

Training uses the validation days (11-12): XGBoost forecasts on the training days (1-10) are
in-sample and far too accurate, which would teach the agent to over-trust them. Evaluation runs
on the held-out test days (13-14) across every scaling-study app, through the same simulator as
all other strategies, and also reports cumulative reward under the RL objective.

Usage:
    python -m cloudopt.rl.train [--timesteps 1000000] [--penalties 1 2 4 8] [--churn 0.05]
Outputs:
    ml/artifacts/rl_ppo_vp{penalty}.zip, ml/artifacts/rl_policy.onnx (default penalty)
    reports/rl_eval.json, reports/figures/frontier.png (RL points added by frontier)
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env

from cloudopt import config
from cloudopt.anomaly import ResidualAnomalyDetector, residual_features
from cloudopt.data import load_workload
from cloudopt.eval.frontier import _study_forecasts
from cloudopt.forecast import features
from cloudopt.forecast.xgb import quantile_index
from cloudopt.rl.env import RLData, ScalingEnv, apply_action, observe
from cloudopt.sim.policies import CloudOpt, Reactive, StaticPeak
from cloudopt.sim.simulator import Context, SimConfig, choose_replica_vcpu, simulate, summarize


class RLPolicy:
    """Simulator adapter: batch inference of a trained PPO policy across all apps."""

    def __init__(self, model: PPO, data: RLData, name: str):
        self.model, self.d, self.name = model, data, name

    def decide(self, ctx: Context) -> np.ndarray:
        obs = observe(self.d, np.arange(ctx.wl.n_apps), ctx.t, ctx.running)
        action, _ = self.model.predict(obs, deterministic=True)
        return apply_action(ctx.running, np.asarray(action), ctx.cfg.min_replicas, ctx.cfg.max_replicas)


def build_data() -> tuple:
    wl = load_workload(scaling_study=True)
    cfg = SimConfig()
    vcpu = choose_replica_vcpu(np.median(wl.demand_mean[:, config.TRAIN], axis=1), cfg)
    q1 = np.nan_to_num(_study_forecasts(wl.app_ids, 1))
    det = ResidualAnomalyDetector.load(config.ARTIFACTS / "anomaly.joblib")
    F = residual_features(wl.demand_peak, q1, features.app_scale(wl.demand_mean))
    flags, kind = det.detect(F, wl.demand_peak, q1)
    static = StaticPeak()
    static.reset(Context(0, wl, cfg, vcpu, np.zeros(wl.n_apps), np.zeros((wl.n_apps, wl.n_slots))))
    data = RLData(wl.demand_peak, wl.demand_mean, q1, flags & (kind > 0), vcpu, static.replicas * vcpu)
    return wl, cfg, vcpu, data


def episode_reward(res, data: RLData, vp: float, cp: float = 0.01) -> float:
    """Mean per-app cumulative reward of a simulated run under the RL objective."""
    cap = res.replicas * data.vcpu[:, None]
    churn = np.concatenate([np.zeros((cap.shape[0], 1)), (np.diff(res.replicas, axis=1) != 0)], axis=1)
    r = -cap / data.static_cap[:, None] - vp * res.violation - cp * churn
    return float(r.sum(axis=1).mean())


def train(timesteps: int, penalties: list[float], churn: float = 0.05, seed: int = 0) -> dict:
    torch.set_num_threads(4)
    wl, cfg, vcpu, data = build_data()
    start, end = config.TEST.start, config.TEST.stop
    results = {"timesteps": timesteps, "churn_penalty": churn, "train_window": "validation days 11-12",
               "eval_window": "test days 13-14", "apps": wl.n_apps, "policies": []}

    baselines = {
        "reactive_hpa": Reactive(),
        "cloudopt_rules": CloudOpt(data.q, quantile_index(0.9), data.spikes, name="cloudopt_rules",
                                   hold=[(np.nan_to_num(_study_forecasts(wl.app_ids, h)), h) for h in (3, 6)]),
    }
    base_runs = {k: simulate(p, wl, cfg, start, end, replica_vcpu=vcpu) for k, p in baselines.items()}
    static_run = simulate(StaticPeak(), wl, cfg, start, end, replica_vcpu=vcpu)

    for vp in penalties:
        t0 = time.time()
        env = make_vec_env(lambda: ScalingEnv(data, config.VAL, violation_penalty=vp, churn_penalty=churn), n_envs=8, seed=seed)
        model = PPO("MlpPolicy", env, n_steps=512, batch_size=512, learning_rate=3e-4, gamma=0.97, gae_lambda=0.95,
                    ent_coef=0.01, clip_range=0.2, policy_kwargs={"net_arch": [64, 64]}, seed=seed, verbose=0)
        model.learn(total_timesteps=timesteps, progress_bar=False)
        model.save(config.ARTIFACTS / f"rl_ppo_vp{vp:g}.zip")
        res = simulate(RLPolicy(model, data, f"rl_ppo_vp{vp:g}"), wl, cfg, start, end, replica_vcpu=vcpu)
        row = summarize(res, static_run)
        row["violation_penalty"] = vp
        row["train_seconds"] = round(time.time() - t0, 1)
        row["cumulative_reward"] = episode_reward(res, data, vp, churn)
        row["baseline_reward"] = {k: episode_reward(r, data, vp, churn) for k, r in base_runs.items()}
        results["policies"].append(row)
        print(json.dumps(row, indent=1), flush=True)

    results["baselines"] = {k: summarize(r, static_run) for k, r in base_runs.items()}
    (config.REPORTS / "rl_eval.json").write_text(json.dumps(results, indent=2))
    return results


def export_onnx(vp: float) -> None:
    """Export the policy network (obs -> action logits) for torch-free inference in the backend."""
    model = PPO.load(config.ARTIFACTS / f"rl_ppo_vp{vp:g}.zip", device="cpu")

    class Logits(torch.nn.Module):
        def __init__(self, policy):
            super().__init__()
            self.p = policy

        def forward(self, obs):
            latent = self.p.mlp_extractor.forward_actor(self.p.extract_features(obs, self.p.pi_features_extractor))
            return self.p.action_net(latent)

    dummy = torch.zeros(1, model.observation_space.shape[0])
    torch.onnx.export(Logits(model.policy).eval(), dummy, str(config.ARTIFACTS / "rl_policy.onnx"),
                      input_names=["obs"], output_names=["logits"], dynamic_axes={"obs": {0: "batch"}, "logits": {0: "batch"}},
                      dynamo=False)  # classic exporter: enough for an MLP, no onnxscript dependency


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--timesteps", type=int, default=1_000_000)
    ap.add_argument("--penalties", type=float, nargs="+", default=[1.0, 2.0, 4.0, 8.0])
    ap.add_argument("--churn", type=float, default=0.05, help="Reward penalty per replica change")
    ap.add_argument("--export-onnx", type=float, default=None, metavar="PENALTY",
                    help="Skip training; export the saved policy with this penalty to ml/artifacts/rl_policy.onnx")
    args = ap.parse_args()
    if args.export_onnx is not None:
        export_onnx(args.export_onnx)
    else:
        train(args.timesteps, args.penalties, args.churn)
