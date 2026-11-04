# System Design and Implementation Decisions

Report material: what was built, why, and where the simplifications are. Numbers come from `reports/*.json`.

## 1. Closed loop

Each replayed 5-minute slot runs one tick of `backend/app/services/engine.py`:

1. **Observe.** Write usage for slot *t* at the current replica count: request rate, mean and busiest-minute concurrency, CPU utilisation, cost, the counterfactual static cost, and the SLA flag.
2. **Detect.** Score slot *t* against the 5-minute forecast issued at *t−1* (residual Isolation Forest + p05–p95 band).
3. **Forecast.** Quantile forecasts (p05…p99) for *t+1, t+3, t+6, t+12* with XGBoost.
4. **Decide.** Scale out to ⌈p90 forecast for *t+1* / (replica vCPU × target utilisation)⌉, with an anomaly guard: after a flagged spike, assume it persists one more slot. Scale in only down to what the 15- and 30-minute p90 forecasts also require (look-ahead protection, added after observing a queue worker with 30-minute bursts being scaled in just before each burst), and only after 3 consecutive lower recommendations.
5. **Act or ask.** *Autopilot* applies changes up to a per-service guardrail (default ±50% per step). A larger change is applied up to the guardrail and the remainder becomes a pending recommendation. *Approval-required* services route every change through a human. Pending items expire after 30 simulated minutes, because a forecast window that has passed is stale. A rejection suppresses the same proposal for one hour.
6. **Cost and explain.** Hourly and monthly cost delta under the tenant's pricing model, SLA risk before and after (from the quantile forecasts), confidence (from the forecast interval width), and a natural-language explanation generated only from those structured facts.

The offline simulator (`cloudopt/sim`) implements the same timing semantics, and `cloudopt.sim.policies.CloudOpt` reproduces the engine's decision rule, so the offline evaluation numbers describe the product's actual behaviour.

## 2. Data and modelling conventions

- **Source.** Azure Functions Trace 2019: 14 days, per-minute invocations. Tenant = subscription (`HashOwner`), service = app (`HashApp`). See `docs/data_profile_azure.md`.
- **Why not the Borg sample.** It has no temporal continuity (at most 4 of 8,928 five-minute windows per job; daily autocorrelation ≈ 0). It is kept for its request-vs-usage ratios (median 0.217), which calibrate one static baseline.
- **Compute demand** = invocations × mean execution time ÷ window length: the average number of busy workers (Little's law). One busy worker = one vCPU (`cpu_per_worker`, configurable). This is the main modelling assumption: the trace has no CPU measurements.
- **SLA** is judged on the busiest minute in each slot (peaks are 1.1× the 5-minute mean at the median, 3.7× at p99). A slot violates the SLA when peak utilisation exceeds 90% (configurable per tenant).
- **Replica sizing.** Each service uses the largest size in {0.25, 0.5, 1, 2, 4} vCPU (2 GB per vCPU) that still needs at least 3 replicas at median demand, so it can scale in both directions.
- **Scaling study set.** 264 apps (201 tenants) whose median demand is at least 0.25 workers. Smaller apps sit at one replica under any policy. All 728 selected apps are used to train the forecasters.
- **Split.** Days 1–10 train, days 11–12 validation (early stopping, threshold tuning, RL training), days 13–14 test.

## 3. Components

| Component | Implementation | Key choice |
|---|---|---|
| Ingestion | `ingestion/preprocess_azure.py`; replay engine | Simulated: the trace is replayed at accelerated speed (default 60×) through the same write path a real exporter would use |
| Forecasting | `cloudopt/forecast/xgb.py`, `lstm.py` | One pooled multi-quantile XGBoost model per horizon on per-app-normalised log demand; LSTM for 1 h and 24 h paths |
| Anomaly detection | `cloudopt/anomaly.py` | Features are forecast residuals, not raw values, so predictable surges are not flagged; threshold tuned on validation |
| Optimisation | `cloudopt/sim/policies.py` (`CloudOpt`), `cloudopt/rl` | Forecast-quantile rule with guard and hysteresis; PPO agent in a custom Gymnasium env as the learned alternative |
| Cost | `cloudopt/sim/pricing.py`, pricing table in the DB | Provider-agnostic vCPU-hour + GB-hour model; presets plus custom/reseller rates |
| Explainability | `backend/app/services/explain.py` | OpenAI from structured facts, with a deterministic template fallback; automatic rubric (`explain_rubric.py`) |
| Dashboard | `frontend/` | React + Tailwind; React Query polling; loading, empty and error states throughout |
| Auth | `backend/app/security.py`, `api/deps.py` | bcrypt + HS256 JWT; admin/viewer roles; viewers optionally scoped to one tenant (other tenants return 404) |

## 4. Baselines

- **Static peak.** Provisioned once for the training-period peak at target utilisation ("just to be safe").
- **Static, Borg-calibrated.** Median utilisation matches the 21.7% observed in Google's cluster.
- **Reactive (HPA-style).** desired = ⌈current × observed utilisation / target⌉, with a 10% tolerance band and a 15-minute scale-down stabilisation window. It acts on the last slot only.
- **Naive forecasts.** Persistence; 30-minute moving maximum.
- **Oracle.** Perfect foresight. A lower bound, not achievable.

All strategies are compared on identical demand (test days 13–14). Because one default setting can flatter or penalise a strategy, `cloudopt/eval/frontier.py` sweeps each strategy's knob and compares the resulting cost-vs-violation curves.

## 5. Limitations (state these in the report)

1. Telemetry is replayed public trace data, not live tenant infrastructure. Actions change simulated capacity, not real deployments.
2. CPU demand is derived (invocations × duration) rather than measured, and one busy execution is assumed to use one vCPU.
3. The trace is serverless (Azure Functions). It is used as realistic request traffic for services that tenants provision themselves; the real platform autoscales these apps on its own.
4. 14 days of data: weekly seasonality is observed only twice.
5. Anomaly labels are synthetic (injected); precision against real traffic is a lower bound because genuine anomalies are unlabelled.
6. Pricing presets are approximate list prices; tenant names are fictional labels for anonymised trace owners.
7. One engine per database (single API worker). Horizontal scaling would need a job queue or leader election.
8. Schema management uses `create_all` rather than migrations, to keep the timeline realistic.

## 6. Future work

- Connectors to read CloudWatch, Azure Monitor and Prometheus metrics, and actuators for the Kubernetes and cloud APIs.
- Vertical rightsizing (choosing replica size) as an action alongside replica count.
- Per-tenant SLO definitions based on latency rather than utilisation.
- Online model retraining and drift detection.
