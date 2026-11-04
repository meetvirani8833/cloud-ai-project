# Evaluation Results

All numbers are on held-out test days 13–14 of the Azure Functions 2019 trace and are reproducible with the commands in the README. Raw outputs are in `reports/*.json`; figures are in `reports/figures/`. The dashboard's Evaluation page renders the same files.

## 1. Headline (cost vs. SLA)

Scaling study: 264 apps from 199 tenants. Every strategy is replayed on identical demand. Each strategy's tuning knob is swept to trace a cost-vs-violation curve, and strategies are compared only where their curves overlap (`reports/frontier.json`, `reports/figures/frontier.png`).

| Claim | Value |
|---|---|
| RL agent (PPO) vs. CloudOpt rule policy at equal SLA (mean over RL operating points) | **7% cheaper** |
| CloudOpt is cheaper than reactive (HPA-style) autoscaling at equal SLA (2.8% violations, reactive's best achievable) | **39.8%** |
| CloudOpt saves vs. static peak provisioning while keeping violations ≤ 1% | **80.6%** |
| Extra cost of CloudOpt's stability layers vs. forecast-only at 1% violations | 24.3% (buys 3× fewer scaling actions) |

Default settings (`reports/comparison_delay0.json`):

| Strategy | Cost (2 days) | SLA violations | Replica changes / app / day |
|---|---|---|---|
| Static, sized for peak | $165,940 | 1.82% | 0 |
| Static, Borg-calibrated over-provisioning | $51,792 | 4.67% | 0 |
| Reactive autoscaler (HPA-style) | $18,465 | 14.95% | 27.0 |
| Naive forecast: 30-min max | $33,010 | 1.47% | 32.9 |
| Forecast only (XGBoost p90) | $23,475 | 1.25% | 79.4 |
| **CloudOpt production policy** | **$32,126** | **0.64%** | **25.6** |
| Oracle (perfect foresight) | $20,532 | 0.00% | 117.4 |

Reading:
- CloudOpt dominates the naive forecaster: cheaper, fewer violations and fewer changes.
- Against reactive autoscaling, it has 23× fewer violations at a similar change rate.
- The pure forecast policy has the most efficient cost-vs-SLA curve in this simulator, because the simulator charges nothing for scaling actions. CloudOpt's anomaly guard, look-ahead scale-in and hysteresis trade about 24% extra cost for about 3× fewer replica changes. Real platforms pay for every change (cold starts, connection draining, rescheduling), so this is a deliberate operational choice and is stated as such.

## 2. Forecasting (`reports/forecast_xgb.json`, `reports/forecast_lstm.json`)

Peak demand per 5-minute slot, scaling-study apps. WAPE = Σ|error| / Σactual. nMAE = mean absolute error ÷ each app's mean demand, averaged over apps.

| Horizon | XGBoost WAPE / nMAE | Persistence | Same slot yesterday | XGBoost 80% band coverage |
|---|---|---|---|---|
| 5 min | **17.7% / 0.313** | 30.1% / 0.375 | 29.1% / 0.707 | 80.4% |
| 15 min | **17.4% / 0.334** | 32.3% / 0.400 | 29.1% / 0.707 | 80.6% |
| 30 min | **13.7% / 0.344** | 16.5% / 0.386 | 29.1% / 0.707 | 81.4% |
| 1 hour | **14.5% / 0.365** | 16.1% / 0.391 | 29.1% / 0.707 | 81.7% |
| 24 hours | **24.7% / 0.578** | 29.1% / 0.707 | 29.1% / 0.707 | 78.9% |

The quantile intervals are well calibrated: the nominal 80% band covers 79–82% of outcomes.

LSTM (PyTorch, pinball loss):

| Horizon | LSTM | XGBoost | Naive |
|---|---|---|---|
| 1 hour (5-min resolution) | 24.2% / 0.398 | **14.5% / 0.365** | 16.1% / 0.391 |
| 24 hours (hourly) | 22.8% / **0.516** | **22.1%** / 0.525 | 25.3% / 0.608 |

The LSTM is competitive only at the long horizon. There it ties XGBoost: better nMAE, slightly worse WAPE. At 1 hour it does not beat persistence. With 14 days of data, the tree model's explicit lag and seasonal features win at short horizons. This matches the original design: XGBoost for short horizons, with the LSTM as a long-horizon option.

## 3. Anomaly detection (`reports/anomaly_eval.json`)

Synthetic events injected into the test days: 274 spikes, 259 drops (positives) and 259 gradual surges (benign, must not alert). Thresholds were tuned on validation days with a different random seed. Scoring is per event.

| Detector | Precision | Recall | F1 | False alarms on surges | Alerts |
|---|---|---|---|---|---|
| **Forecast residual + Isolation Forest** | **40%** | **81%** | **0.54** | **17%** | 1,090 |
| Ablation: Isolation Forest without band check | 36% | 82% | 0.50 | 18% | 1,228 |
| Ablation: forecast band only | 5% | 96% | 0.10 | 97% | 10,191 |
| Raw threshold baseline (tuned) | 16% | 70% | 0.26 | 20% | 2,356 |

The residual detector more than doubles F1 over the raw threshold baseline. The Isolation Forest is what keeps it from flagging every surge, as the band-only ablation shows. Precision is a lower bound because the real traffic contains genuine anomalies that are not labelled.

## 4. Reinforcement learning (`reports/rl_eval.json`)

Custom Gymnasium environment (`cloudopt/rl/env.py`) and Stable-Baselines3 PPO, 1M steps per policy (about 5 minutes on CPU each).

- **Observation:** current capacity relative to the XGBoost p50/p90/p99 forecasts, recent peaks, time of day, and the anomaly flag.
- **Actions:** ×{0.5, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 2} replicas.
- **Reward:** −(capacity ÷ static-peak capacity) − λ·violation − 0.05·change.
- **Data split:** trained on validation days, because forecasts on the training days are in-sample and too accurate. Evaluated on test days through the same simulator as every other strategy.

| λ (violation penalty) | Cost | SLA violations | Changes / app / day | Reward: PPO | CloudOpt rules | Reactive |
|---|---|---|---|---|---|---|
| 1 | $19,071 | 5.74% | 14.2 | **−222.7** | −275.8 | −276.4 |
| 2 | $23,276 | 2.74% | 13.2 | **−252.1** | −279.5 | −362.5 |
| 4 | $25,610 | 2.30% | 23.0 | **−280.5** | −286.9 | −534.8 |
| 8 | $26,087 | 2.17% | 22.4 | −332.1 | **−301.8** | −879.3 |

- The PPO agent beats the reactive autoscaler at every λ, and beats the hand-built CloudOpt rules at λ ≤ 4.
- On the cost-vs-SLA curves it is on average **7% cheaper than the CloudOpt policy at equal violation rates**, with the fewest replica changes of any adaptive policy (13–23 per app per day).
- **Limitation:** it cannot push violations below about 2.2% even at λ = 8, while the rule policy reaches 0.6% and below. So the product uses a hybrid: the forecast rules are the default, and the RL agent is a per-service option (λ = 2 policy, exported to ONNX, 23 KB, 100% action agreement with PyTorch) for cost-first services. Both engines go through the same autopilot guardrails and approval flow.
- An earlier run with a 5× smaller churn penalty and 300k steps changed replicas about 227 times per app per day. Its results are kept in `reports/archive/` as an ablation showing why the change penalty matters.

## 5. Explainability (`reports/explainability_eval.json`)

596 recommendations were generated by the live engine. Each explanation is checked automatically against the structured facts it was generated from (`backend/app/services/explain_rubric.py`). The LLM is gpt-4.1-mini, prompt-only, scored on 40 sampled recommendations.

| Check | Template | LLM first draft | LLM + fact-check (shown to users) |
|---|---|---|---|
| All brief fields present | 100.0% | 100.0% | 100.0% |
| Correct replica counts | 100.0% | 100.0% | 100.0% |
| Correct direction | 100.0% | 100.0% | 100.0% |
| Cites the triggering forecast | 100.0% | 100.0% | 100.0% |
| Mentions the anomaly if and only if one triggered it | 99.8% | 100.0% | 100.0% |
| Correct cost direction | 100.0% | 100.0% | 100.0% |
| Cites risk before and after | 100.0% | 100.0% | 100.0% |
| Threshold claims are true | 99.5% | 100.0% | 100.0% |
| Correct confidence label | 100.0% | 95.0% | 100.0% |
| No internal field names | 100.0% | 100.0% | 100.0% |
| No invented numbers | 100.0% | 100.0% | 100.0% |
| **All checks passed** | **99.3%** | **95.0%** | **100.0%** |

How the LLM layer got here (each stage measured with the same rubric):

| Prompt version | First drafts passing every check | Main failure |
|---|---|---|
| v1: format spec only | 75% | Reason omitted the forecast numbers (82.5% cited them) |
| v2: + plain-English example sentence, no field names | **95%** | Occasional wrong confidence label |

- **Every LLM explanation is verified before it is shown.** A failed draft gets one corrective retry naming the failed checks; if that also fails, the deterministic template is shown and labelled as a fallback. In this sample: 38 passed first time, 2 were fixed on retry, 0 fell back. Of the explanations shown, 100% are LLM-written and 100% pass the rubric.
- **System facts are never left to the model.** The "Action status" line is set by code and updated when an admin approves or rejects.
- **The rubric found real bugs on both sides.**
  - Template: one explanation claimed "81%, above the 90% SLA threshold"; another called 71% "within the 70% target".
  - LLM: raw JSON field names in user-facing text, and stale or incorrect status lines.
  - The rubric's own false positives were fixed too: negated mentions such as "no anomaly detected", and labels such as "90th percentile" being read as numbers.
- **Spend is capped** at `OPENAI_MAX_CALLS_PER_HOUR` (default 120); beyond the cap, explanations use the template.
