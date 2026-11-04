# Person A: Data, Forecasting and Anomaly Detection

**Your job in the evaluation:** explain *where the data came from, why we changed it, how we turned it into something a model can learn from, how the forecasts work, and how we detect anomalies.* You are the person who answers "is the AI actually any good?" for the prediction half of the system.

Rename "Person A" with your name. Read the shared section first (everyone must know it), then your own sections, then skim the other two people's files so you can answer cross-questions.

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

> "I worked on the data and prediction side. I analysed the datasets and found that our first one, Google's Borg sample, had no usable time patterns, so I moved us to Microsoft's Azure Functions trace, which has two weeks of minute-by-minute traffic. I built the pipeline that turns it into per-app demand, trained the forecasting models, XGBoost and an LSTM, which predict demand from 5 minutes to 24 hours ahead with confidence ranges, and built the anomaly detector. On unseen days our 5-minute forecast has about 40% less error than the naive guess, and our anomaly detector doubles the F1 score of a tuned threshold."

---

## 2. What you own (so you can point at the code)

| Topic | Files |
|---|---|
| Borg analysis (why it failed) | `ingestion/parse_kaggle.py`, `ingestion/profile_kaggle.py`, `docs/data_profile.md` |
| Azure preprocessing | `ingestion/preprocess_azure.py`, `docs/data_profile_azure.md` |
| Data loading | `cloudopt/data.py`, `cloudopt/config.py` (train/val/test split) |
| Features | `cloudopt/forecast/features.py` |
| XGBoost forecaster | `cloudopt/forecast/xgb.py`, `cloudopt/forecast/metrics.py` |
| LSTM | `cloudopt/forecast/lstm.py` |
| Anomaly detection | `cloudopt/anomaly.py` |
| Results | `reports/forecast_xgb.json`, `reports/forecast_lstm.json`, `reports/anomaly_eval.json`, `docs/results.md` §2–3 |

---

## 3. The data story (the most important thing you explain)

**Step 1: the original plan.** The project brief and our first plan used Google's Borg 2019 cluster trace (Kaggle sample, 405,894 rows, 34 columns, 31 days). Before building anything we tested whether it could support forecasting.

**Step 2: what we found.**
- No job or instance had more than **4 of 8,928** possible 5-minute windows in the month. There was no continuous history for anything.
- The aggregate series had a day-to-day autocorrelation of **0.004** (essentially zero; real traffic is usually strongly daily).
- One instance was repeated 7,704 times across 6,640 machines, an artefact of how the Kaggle file was exported.
- The Kaggle file is a random sample of rows, so the time structure was destroyed.

**Step 3: what we kept it for.** It still shows real over-provisioning: median usage-to-request ratio of **0.217**, and 63.9% of records use less than half of what they reserved. That is real evidence for our problem statement, and one of our baselines uses that ratio.

**Step 4: the switch.** We moved to the **Azure Functions Trace 2019** (Shahrad et al., USENIX ATC 2020; CC BY 4.0, so commercial use is allowed unlike Borg's non-commercial licence). It has 14 days of per-minute invocation counts, per-function execution time, per-app memory, and an owner (subscription) per app. Daily autocorrelation is **0.62**, and a natural tenant field. The problem statement did not change; we were told implementation was flexible.

> **If asked "isn't changing the dataset a failure?"** No. We tested the data *before* building on it and found a real flaw. Discovering that early is the correct engineering behaviour, and we documented the evidence in `docs/data_profile.md`.

**Preprocessing (what the pipeline does).**
1. Sums functions to *app* level (in Azure Functions the app is the unit of resource allocation).
2. Builds minute-by-minute series, then 5-minute slots (4,032 slots over 14 days).
3. Computes **demand in busy workers** with *Little's law*: busy workers = request rate × time per request. Example: 10 requests per second × 0.5 s each = 5 CPUs busy on average. We assume one busy request uses one CPU.
4. Also records the **busiest single minute** in each 5-minute slot. SLA is judged on that, so short spikes aren't hidden by averaging. (Peaks are 1.1× the mean at the median, 3.7× at the 99th percentile.)
5. Memory is available only for days 1–12; days 13–14 are filled with each app's median and flagged.
6. Selection: **728 apps** (542 tenants) with traffic all 14 days, at most 5% empty minutes and at least 10 requests per minute on average. For cost experiments we use the **264 apps** whose median demand is at least 0.25 busy workers (smaller apps would sit at 1 replica under any policy).

**Split.** Days 1–10 train, 11–12 validation (early stopping, threshold tuning, RL training), 13–14 test. Every reported result is on days 13–14. Split is chronological, never random, because random splits leak the future.

---

## 4. Forecasting: how it works

**What is predicted.** The *peak* demand (busy workers, busiest minute) for slot *t*, given data up to *t − h*, for horizons h = 1, 3, 6, 12, 288 slots, which is 5 min, 15 min, 30 min, 1 hour, 24 hours.

**Model: XGBoost quantile regression.** One pooled model per horizon, trained on all 728 apps at once. Demand is divided by each app's training mean and log-transformed so apps with very different sizes can share one model. It outputs six quantiles (p05, p10, p50, p90, p95, p99), so the forecast is a *range*, not one number.

**Features** (all strictly from the past, so no leakage): the last 12 slots, rolling means and maxima over 6/12/36/144 slots, the same slot yesterday and last week, time of day (sin/cos), day of week index, and the app's size.

**Why quantiles?** Scaling cares about *risk*. Provisioning for "90% sure demand stays below this" protects the SLA without paying for worst cases. The range also feeds the confidence shown on recommendations.

**Settings.** Depth 6, up to 400 rounds with early stopping on validation days, learning rate 0.08. (Depth 8 was about 2% more accurate but 4× bigger. The live demo has to fit on a free hosting tier, so we chose depth 6; models are ~11 MB each.)

**LSTM.** PyTorch, 2 layers, 64 hidden units, pinball (quantile) loss, outputs p10/p50/p90. One model for 1 hour ahead (look-back 6 h) and one for 24 hours ahead using hourly data (look-back 3 days).

**Metrics.**
- **WAPE** = total absolute error ÷ total actual demand (primary).
- **nMAE** = absolute error ÷ each app's mean demand, averaged over apps (so the biggest apps don't dominate).
- **MAE, RMSE, MAPE** are reported as the brief asks. MAPE is unstable near zero demand, so we don't lead with it.

**Results (scaling-study apps, test days):**

| Horizon | XGBoost WAPE / nMAE | Naive persistence | Same slot yesterday | 80% band covers truth |
|---|---|---|---|---|
| 5 min | **17.7% / 0.313** | 30.1% / 0.375 | 29.1% / 0.707 | 80.4% |
| 15 min | **17.4% / 0.334** | 32.3% / 0.400 | 29.1% / 0.707 | 80.6% |
| 30 min | **13.7% / 0.344** | 16.5% / 0.386 | 29.1% / 0.707 | 81.4% |
| 1 hour | **14.5% / 0.365** | 16.1% / 0.391 | 29.1% / 0.707 | 81.7% |
| 24 hours | **24.7% / 0.578** | 29.1% / 0.707 | 29.1% / 0.707 | 78.9% |

**LSTM vs XGBoost.** 1 hour: LSTM 24.2% WAPE vs XGBoost 14.5%, so the LSTM loses (it doesn't even beat persistence's 16.1%). 24 hours (hourly): LSTM 22.8% WAPE / 0.516 nMAE vs XGBoost 22.1% / 0.525, so they tie. We report this honestly. The product uses XGBoost.

**Why the LSTM lost at short horizons.** Only 14 days of data (about 4,000 slots per app). Tree models with explicit lag and seasonal features are hard to beat on small tabular time-series data; the LSTM needs more history.

**Calibration.** A nominal 80% interval (p10–p90) contained the real value 79–82% of the time. That means the ranges can be trusted.

**Data caveat on the 24-hour horizon.** Only about 29% of apps have a strong daily pattern (lag-1-day autocorrelation > 0.5). Short horizons work for almost every app (87% have strong 5-minute-to-5-minute correlation); 24-hour forecasts are meaningful mainly for the daily-pattern apps. Say this before they ask.

---

## 5. Anomaly detection: how it works

**Problem.** A normal morning rush isn't an anomaly; a surprise spike is. A detector on raw values flags every busy period.

**Our method.**
1. Take the 5-minute-ahead forecast (p05–p95 band) issued one slot earlier.
2. Build 4 scale-free features from the *residual* (actual minus forecast, measured in units of the forecast's own p10–p90 width): the residual now, the residual one slot ago, and the log-distance outside the p95 and p05 bounds.
3. An **Isolation Forest** (200 trees) trained on clean training-day residuals scores how unusual each point is. (Isolation Forest finds rare points because they're easy to isolate with random splits.)
4. An alert needs **both**: score above a tuned threshold **and** the value outside the forecast's p05–p95 band. Spike = above, drop = below.

**Evaluation (real data has no anomaly labels).** We *injected* labelled events into test days: short spikes (2.5–6× for 1–3 slots) and drops (down to 5–30%), plus gradual 24–36-slot surges that are **benign** and must *not* alert. On the test days: 274 spikes, 259 drops, 259 surges. Threshold tuned on validation days with a different random seed, then reported on test days with another seed. Scoring is per *event* (what an on-call engineer experiences).

| Detector | Precision | Recall | F1 | False alarms on surges | Alerts |
|---|---|---|---|---|---|
| **Residual + Isolation Forest (ours)** | 40% | **81%** | **0.54** | 17% | 1,090 |
| Isolation Forest without band check | 36% | 82% | 0.50 | 18% | 1,228 |
| Forecast band only | 5% | 96% | 0.10 | 97% | 10,191 |
| Tuned raw threshold | 16% | 70% | 0.26 | 20% | 2,356 |

**Reading it.** Band-only catches nearly everything but raises 10,191 alerts (useless). The Isolation Forest is what makes it usable. Ours has 2× the F1 of the raw threshold and 54% fewer alerts.

**Precision caveat.** It's a *lower bound*: real traffic contains genuine anomalies we didn't label, which count against precision.

**How it's used live.** After a flagged spike, the decision engine assumes the spike persists one more slot (anomaly guard). Admins can inject a synthetic spike from Settings and watch it detected within a cycle.

---

## 6. Questions you will probably get (and answers)

**Q1. Why did you change the dataset?**
We tested the Borg sample before building: at most 4 of 8,928 time windows per job, daily autocorrelation 0.004, plus an export artefact. Nothing could be forecast. Azure Functions has 14 days of minute-level data with daily autocorrelation 0.62.

**Q2. Your data has no CPU. How do you know demand?**
Little's law from queueing theory: average busy workers = arrival rate × average service time. We state "one busy request = one CPU" as a limitation.

**Q3. Why is this serverless data valid for server scaling?**
Azure scales those apps itself. We use the *traffic* as realistic load for services a company would run on servers it provisions. It's an assumption we state.

**Q4. Why only 14 days? Is that enough?**
It's enough for daily patterns (seen 14 times) but weekly patterns appear only twice, which limits the 24-hour horizon. We split chronologically and report that horizon separately.

**Q5. How do you prevent data leakage?**
Chronological split; features only use data at or before the forecast issue time; normalisation scale computed from training days only; the live runtime masks every future slot (not-yet-replayed slots are NaN).

**Q6. Why XGBoost and not only deep learning?**
The brief lists both. We tested both honestly. XGBoost wins clearly at short horizons and ties at 24 hours on this small dataset, and it's fast, small and gives quantiles directly.

**Q7. Why quantile forecasts instead of a single number?**
Capacity decisions depend on risk. p90 means "enough capacity 90% of the time"; the width of the range also becomes the confidence score.

**Q8. How do you know the intervals are right?**
Calibration: the 80% band contains the truth 79–82% of the time across horizons.

**Q9. WAPE vs MAPE?**
MAPE explodes when actual values are near zero, which is common here. WAPE (total error ÷ total demand) is stable; nMAE weights every app equally. We still report MAPE as the brief asks.

**Q10. How do you evaluate anomaly detection without labels?**
Inject labelled events into held-out days, tune thresholds on validation days with a different seed, report on test days. We also include benign surges to measure false alarms, plus ablations.

**Q11. Why does the anomaly detector use residuals?**
So predictable surges don't count as anomalies. The band-only ablation shows what happens without the Isolation Forest, and the raw-threshold baseline shows what happens without residuals.

**Q12. Precision is only 40%. Isn't that bad?**
It's a lower bound (unlabelled real anomalies count as false alarms), and at 1,090 alerts versus 2,356 for the baseline it's far more usable. An operator can tune the threshold; we picked the best F1 on validation.

**Q13. Why a pooled model instead of one per app?**
Per-app series are short (about 2,900 training slots) and apps differ in size by four orders of magnitude. Normalising and pooling lets small apps borrow patterns from similar ones.

**Q14. What would improve forecasts?**
More history (weekly seasonality), exogenous signals (deployments, marketing calendar), and per-app fine-tuning.

**Hard ones (answer honestly):**
- *"Your LSTM is worse than XGBoost, so why include it?"* The brief asks for both; we evaluated it properly, and reporting a negative result honestly is part of the evaluation.
- *"Is 17.7% error good?"* It is 40% below the naive baseline at the same horizon. Traffic is bursty, so there's an irreducible noise floor; the oracle gap shows what perfect foresight would add.
- *"The savings aren't real money."* Correct: simulated on replayed real traffic. The method and comparisons are reproducible.

---

## 7. Cross-questions from the others' areas (quick answers)

- **How does the forecast become a scaling decision?** Replicas = ⌈p90 forecast ÷ (replica vCPU × 70% target)⌉, plus guards (Person B).
- **How do you know the explanation is true?** Every LLM explanation is automatically checked against the real numbers; failures get one retry, then a template (Person C).
- **What's the RL part?** A PPO agent trained in a Gymnasium simulator; about 7% cheaper at equal reliability but can't reach very low violation rates, so it's an option (Person B).

---

## 8. Demo duties and things not to say

- In the demo, you show the **service detail page**: black line = actual demand, shaded band = what the forecast expected, dotted blue past "now" = next-hour forecast. Then inject a spike (Settings → Replay & scenarios → pick `checkout-api`, Sudden spike 5×, 2 intervals) and show it on the **Anomalies** page within a few seconds.
- **Don't say** "real company savings", "we measured CPU", "the LSTM is better", or "our anomaly detector has 40% accuracy" (it's precision, a different thing).
- **If you don't know:** say "I'd want to check the exact number in our results file" and point to `reports/` or `docs/results.md`. Never invent a number.
