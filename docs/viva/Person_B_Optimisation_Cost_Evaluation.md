# Person B: Optimisation (Rules + RL), Cost Model and the Evaluation

**Your job in the evaluation:** explain *how a forecast becomes a scaling decision, how we price it, how the reinforcement-learning agent works, and above all how we proved the system beats the alternatives.* You own the centrepiece result: **CloudOpt vs. baselines on the same traffic.** If an evaluator asks "how do you know it works?", they are asking you.

Rename "Person B" with your name. Read the shared section, then your own sections, then skim the other two files for cross-questions.

---

## 0. Shared overview (all three of us must be able to say this)

**The problem in 30 seconds.** Companies pay for cloud servers by the hour. Traffic goes up and down all day, so the question "how many servers do we need right now?" never stops. Too few and the app slows down (breaking its SLA, the reliability promise). Too many and you pay for idle machines. Small SaaS teams with no FinOps engineer (the person who manages cloud cost) over-provision "just to be safe". Our own analysis of Google's cluster data: a typical job uses about **22%** of the CPU it reserves, and **64%** use under half.

**Our solution.** CloudOpt is a closed loop that runs every 5 minutes for every service of every tenant (customer company): *collect telemetry → forecast demand → detect anomalies → decide replica count (rules, or an RL agent) → price the change → explain it in plain English → auto-apply small changes or ask a human to approve big ones.* A replica is one running copy of an app.

**Data.** Real Microsoft Azure Functions trace (2019): 14 days, per-minute request counts, 24,274 apps from 14,681 subscriptions. We replay it at 60× speed (one 5-minute slot every 5 seconds) because we have no live cloud to connect to. Tenant = Azure subscription; service = app.

**The 8 components** (from the brief): 1 data and ingestion, 2 forecasting, 3 anomaly detection, 4 optimisation (rules + RL), 5 cost intelligence, 6 explainability (LLM), 7 dashboard, 8 login and roles.

**Numbers to memorise** (all on held-out test days 13–14, 264 apps from 199 tenants):

| What | Number |
|---|---|
| Cheaper than fixed "peak" provisioning, with SLA violations ≤ 1% | **80.6%** |
| Cheaper than a reactive (HPA-style) autoscaler at equal reliability | **39.8%** |
| SLA violations: CloudOpt vs reactive autoscaler (default settings) | **0.64% vs 14.95%** (23× fewer) |
| Forecast error at 5 min (WAPE): ours vs naive "same as last time" | **17.7% vs 30.1%** |
| Anomaly detection F1: ours vs tuned threshold | **0.54 vs 0.26** |
| RL agent vs our rules at equal reliability | about **7% cheaper**, but can't go below ~2% violations |
| Explanations shown to users that pass every fact-check | **100%** (LLM first drafts: 95%) |
| Automated tests | **38** pass (29 backend + 9 simulator) |

**Say it carefully.** The traffic is real but *replayed*; savings are *simulated* savings, not money a real company saved. CPU is *estimated*, not measured. We say both ourselves, before anyone asks.

**Honest status.** All 8 components are built and tested and run on our laptop. Code is on GitHub: https://github.com/meetvirani8833/cloud-ai-project . The hosted live link is not set up yet; if asked, say the demo runs locally and a backup video exists or will exist (check with the team before the evaluation which of these is true).

---

## 1. Your 60-second self-introduction

> "I built the decision-making and evaluation side. I wrote the simulator that models servers, cost and SLA violations, the baseline strategies we compare against, the forecast-driven scaling policy, and the reinforcement-learning agent. I also built the cost model with provider price tables. The key result: on two days of unseen real traffic, CloudOpt is 80.6% cheaper than provisioning for the peak while keeping slowdowns under 1%, and at equal reliability it's 39.8% cheaper than a Kubernetes-style reactive autoscaler. We compare strategies using cost-versus-reliability curves so the result doesn't depend on one lucky setting."

---

## 2. What you own

| Topic | Files |
|---|---|
| Simulator | `cloudopt/sim/simulator.py` |
| Policies and baselines | `cloudopt/sim/policies.py` |
| Cost model | `cloudopt/sim/pricing.py` |
| Naive forecasts used as baselines | `cloudopt/forecast/baselines.py` |
| Evaluation | `cloudopt/eval/compare.py`, `cloudopt/eval/frontier.py` |
| RL | `cloudopt/rl/env.py`, `cloudopt/rl/train.py` |
| Live version of the same rule | `backend/app/services/engine.py` (`_decide`, `_apply_target`) |
| Results | `reports/comparison_delay0.json`, `reports/frontier.json`, `reports/rl_eval.json`, `reports/figures/frontier.png`, `docs/results.md` §1 and §4 |
| Tests | `tests/test_sim.py` |

---

## 3. The simulator (the foundation of every number)

One decision per **5-minute slot**, for all apps at once.

- Each app runs N **replicas** of a fixed size (vCPU, memory). Capacity = N × vCPU, assuming 1 busy worker = 1 vCPU.
- **Replica size:** the largest of {0.25, 0.5, 1, 2, 4} vCPU that still needs at least 3 replicas at the app's median demand, with 2 GB per vCPU. That leaves room to scale in both directions.
- **Information rule:** a policy deciding for slot *t* sees only data before *t*. No peeking.
- **Scale-down is immediate. Scale-up** can take `provision_delay` slots to land (replica start-up time). The headline results use delay 0; `comparison_delay1.json` has delay 1 (5 minutes).
- **SLA violation:** in slot *t*, the *busiest minute's* demand ÷ capacity > **90%**. We use the peak minute so short spikes count.
- **Target utilisation:** 70% (what policies size for).
- **Cost** = replicas × (vCPU × price per vCPU-hour + GB × price per GB-hour) × 5/60 hours.
- **Warm-up:** each policy runs for one extra day before the test window (unscored), so every strategy enters the test in its own steady state, not a cold start.

**Pricing presets** (approximate public list prices; verify before quoting in the report): AWS Fargate $0.04048 per vCPU-hour and $0.004445 per GB-hour; Azure Container Instances $0.0486 and $0.0054; GCP Cloud Run $0.0648 and $0.0072. Admins can add custom/reseller price tables in the dashboard. Monthly cost = hourly × 730.

**Tests:** `tests/test_sim.py` checks e.g. the oracle never violates, a forecaster never uses future data, a scale-up lands only after the delay, the oracle covers the delay window.

---

## 4. The strategies we compare (baselines)

| Strategy | What it does |
|---|---|
| **Static, sized for peak** | Provision once for the highest training-period demand at target utilisation. "Just to be safe." This is the main baseline (savings are measured against it). |
| **Static, Borg-calibrated** | Provision so median utilisation equals 21.7%, the real over-provisioning measured in Google's cluster. |
| **Reactive (HPA-style)** | Kubernetes HPA rule: desired = ⌈current × observed utilisation ÷ target⌉, with a 10% tolerance band and a scale-down stabilisation window of 3 slots (15 min). Looks only at the last slot. |
| **Naive forecasts** | Persistence (last value) and 30-minute moving max. |
| **Forecast only** | Provision for the XGBoost p90 forecast, no stability layers. |
| **CloudOpt (production policy)** | Forecast p90 + anomaly guard + look-ahead scale-in + hysteresis (below). |
| **Oracle** | Perfect foresight. A lower bound, not achievable; shows how far we are from the best possible. |

**The CloudOpt rule (default engine).**
1. **Scale out** to ⌈p90 forecast for the next slot ÷ (replica vCPU × 0.70)⌉.
2. **Anomaly guard:** after a flagged spike, assume it persists one more slot (provision for at least the observed peak).
3. **Look-ahead scale-in:** never reduce capacity below what the **15-minute and 30-minute** p90 forecasts need. (Added after we saw a queue worker with 30-minute bursts being scaled in right before each burst.)
4. **Hysteresis:** scale in only after **3 consecutive** lower recommendations.
5. **Autopilot guardrail:** per service, up to ±50% change per step is applied automatically; the rest becomes a pending recommendation for approval. Pending items **expire after 30 minutes** (a forecast window that has passed is stale). A rejection suppresses the same proposal for 1 hour.

**Confidence and risk shown on each recommendation.** *Risk* = estimated probability that peak demand exceeds the SLA capacity, interpolated from the forecast quantiles, before and after the change. *Confidence* = 1 ÷ (1 + (p90−p10)/p50), clipped to [0.2, 0.98]: a narrow forecast range means high confidence.

---

## 5. The results

**Default settings** (`reports/comparison_delay0.json`):

| Strategy | Cost (2 days) | SLA violations | Replica changes / app / day |
|---|---|---|---|
| Static, sized for peak | $165,940 | 1.82% | 0 |
| Static, Borg-calibrated | $51,792 | 4.67% | 0 |
| Reactive (HPA-style) | $18,465 | **14.95%** | 27.0 |
| Naive: last value | $20,537 | 7.24% | 117.5 |
| Naive: 30-min max | $33,010 | 1.47% | 32.9 |
| Forecast only (XGBoost p90) | $23,475 | 1.25% | 79.4 |
| **CloudOpt (production)** | **$32,126** | **0.64%** | **25.6** |
| Oracle | $20,532 | 0.00% | 117.4 |

**How to read it.** The reactive autoscaler looks cheapest only because it lets apps overload **15% of the time**. CloudOpt costs about a fifth of static-peak and has the *fewest* slowdowns of all realistic strategies. Cost alone is never a fair comparison.

**The fair comparison: cost-vs-SLA curves** (`reports/figures/frontier.png`). A single setting can flatter any method, so for each strategy we swept its "how careful" knob (reactive target utilisation 30–80%; forecast quantile p50–p99 and a safety margin for forecast-based ones) and drew cost against violations. Strategies are compared **only where their curves overlap** (never extrapolated).

| Claim | Value |
|---|---|
| CloudOpt cheaper than reactive at equal SLA (2.8% violations, the best reactive can do) | **39.8%** |
| CloudOpt savings vs static peak with violations ≤ 1% | **80.6%** |
| Extra cost of CloudOpt's stability layers vs forecast-only at 1% violations | **24.3%** (buys about 3× fewer scaling changes: 25.6 vs 79.4 per app per day) |
| RL agent vs CloudOpt at equal SLA (mean over RL points) | **7% cheaper** |

> **Be ready to explain the 24%.** In our simulator, scaling changes are free, so the pure forecast policy is the most cost-efficient. Real systems pay for every change (cold starts, draining, rescheduling), so we deliberately trade about 24% more cost for about 3× fewer changes. We state this openly rather than hide it.

---

## 6. Reinforcement learning

**Setup.** Custom **Gymnasium** environment (`cloudopt/rl/env.py`), **Stable-Baselines3 PPO**. One episode = one app over 144 slots (12 hours).

- **Observation (13 numbers):** current capacity relative to the forecast p50/p90/p99, recent peaks, mean, ratio of p90 to last peak, time of day (sin/cos), log of current replicas, capacity relative to static-peak, anomaly-spike flag.
- **Actions (8):** multiply replicas by 0.5, 0.75, 0.9, 1, 1.1, 1.25, 1.5 or 2.
- **Reward per slot:** −(capacity ÷ static-peak capacity) − λ × [SLA violation] − 0.05 × [replicas changed].
- **Training data:** the **validation days (11–12)**, because forecasts on the training days are in-sample and unrealistically accurate; the agent would learn to over-trust them. Evaluated on test days (13–14) through the same simulator as every other strategy.
- 1,000,000 steps per policy (about 5 minutes on CPU each), λ ∈ {1, 2, 4, 8}.

| λ | Cost | SLA violations | Changes / app / day | Reward PPO | CloudOpt rules | Reactive |
|---|---|---|---|---|---|---|
| 1 | $19,071 | 5.74% | 14.2 | **−222.7** | −275.8 | −276.4 |
| 2 | $23,276 | 2.74% | 13.2 | **−252.1** | −279.5 | −362.5 |
| 4 | $25,610 | 2.30% | 23.0 | **−280.5** | −286.9 | −534.8 |
| 8 | $26,087 | 2.17% | 22.4 | −332.1 | **−301.8** | −879.3 |

**Conclusions.**
- PPO beats the reactive autoscaler at every λ and beats our rules at λ ≤ 4. About **7% cheaper at equal reliability**, with the fewest replica changes.
- **Limitation:** it can't push violations below about 2.2% even at λ = 8, while the rules reach 0.64%. So the **hybrid**: rules by default, RL as a per-service option for cost-first services. Both pass through the same autopilot guardrails and human approval.
- The deployed policy is λ = 2, exported to **ONNX (23 KB)** so the server needs no PyTorch; the ONNX output matches PyTorch on 100% of test inputs.
- **An ablation we kept** (`reports/archive/`): with a 5× smaller churn penalty and 300k steps the agent changed replicas about 227 times per app per day. That shows why the change penalty matters.

---

## 7. Questions you will probably get (and answers)

**Q1. How do you know your system works?**
We replay the same unseen two days of real traffic through every strategy in a simulator and compare cost and SLA violations, including sweeping each strategy's setting so the comparison doesn't depend on one lucky choice.

**Q2. Isn't a simulator you wrote yourself circular?**
Fair concern. We reduced it by: writing the capacity and cost model down explicitly; using a realistic reactive baseline (tolerance band, scale-down window), not a straw man; the policies never see the future; the oracle gives a lower bound; and we sweep thresholds. The limitation remains: it's still a model, not a real cloud.

**Q3. Why is the reactive autoscaler cheaper in the table?**
Because it lets apps overload 15% of the time (14.95% violations). At equal reliability (2.8%), CloudOpt is 39.8% cheaper.

**Q4. Why not just use the forecast-only policy, since it's cheaper?**
It's cheaper in a simulator where changes are free, but it changes replicas about 79 times per app per day vs 25.6. Real systems pay for each change. We keep the stability layers and openly quote the 24% cost.

**Q5. Why does the SLA use the busiest minute?**
Averages hide short spikes that users feel. Peak minutes are 1.1× the mean at the median but 3.7× at the 99th percentile.

**Q6. Why 90% for the SLA and 70% as target?**
Standard autoscaler practice: size for about 70% utilisation, treat sustained load above about 90% as the point where latency degrades sharply. Both are configurable per tenant.

**Q7. How does RL differ from your rules, and why is it only an option?**
The agent learns its own scaling strategy by trial and error against a reward. It's cheaper at equal reliability but can't reach very low violation rates (about 2.2% at best), so rules stay the default. That's the hybrid the brief describes.

**Q8. How did you train the RL agent fairly?**
On the validation days, evaluated on test days it never saw; reward includes a penalty for changing too often; four violation penalties to show the trade-off.

**Q9. What if the forecast is wrong?**
The p90 provisioning gives headroom, the anomaly guard holds extra capacity after a surprise spike, scale-out is immediate, scale-in is delayed and checks 15/30-minute forecasts, and approvals/guardrails stop large mistakes.

**Q10. How are dollar figures computed? Are the prices real?**
Replicas × (vCPU × price + GB × price) × time. Presets approximate public list prices for Fargate, ACI and Cloud Run (to be verified before quoting); custom tables can be added. All savings are relative, so the percentages hold under any consistent price list.

**Q11. What's the oracle for?**
It's perfect foresight: a lower bound that tells us how close we are to the theoretical best. It isn't achievable.

**Q12. What does autopilot mean here?**
Per service, changes up to a guardrail (default 50%) apply automatically; larger ones wait for an admin; services can also be set to approval-only or manual (observe only).

**Hard ones:**
- *"Your results are all in simulation."* Yes, and we say so. The decision logic, cost model and approval flow are exactly what would run on a real system; the missing piece is a connector to a real cloud API.
- *"Why is the RL agent not better than rules everywhere?"* It optimises the reward we gave it; it trades reliability for cost and can't hit very low violation rates. The rule policy provides the safety floor.
- *"Why compare against HPA and not AWS Predictive Scaling?"* HPA's rule is public and reproducible; predictive scaling's internals aren't. We cite it in the literature review as related.

---

## 8. Cross-questions from the others' areas

- **Where do the forecasts come from?** Pooled XGBoost quantile models, 5 min to 24 h; 17.7% WAPE at 5 min vs 30.1% naive (Person A).
- **Anomaly detection?** Residual-based Isolation Forest; F1 0.54 vs 0.26 for a tuned threshold (Person A).
- **How are explanations generated and checked?** An LLM writes from structured facts; every explanation is checked against the numbers; 100% of shown ones pass (Person C).
- **How is access controlled?** JWT login; admins approve; viewers read-only and can be limited to one tenant (Person C).

---

## 9. Demo duties and things not to say

- In the demo you show **Evaluation** (cost-vs-SLA chart and the strategy table) and the **Overview** "before vs now" cost chart. Then explain one **recommendation card** (replicas, cost impact, risk before/after, confidence).
- Optional live moment: Settings → service → switch **Decision engine** from "Forecast policy" to "RL agent (PPO)" and show the same guardrails still apply.
- **Don't say** "the reactive autoscaler is bad" (say "reacts after the load arrives"); "real savings" (say "simulated"); or "RL is better" (say "cheaper at equal reliability but can't reach very low violations").
- **If you don't know:** point to `reports/` or `docs/results.md`; never invent a number.
