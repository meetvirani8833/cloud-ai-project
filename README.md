# CloudOpt: AI-Powered Cloud Resource Optimization and Cost Prediction

A closed-loop decision-support platform for small SaaS teams without FinOps staff. It forecasts each service's demand, detects anomalies, decides replica counts with a forecast-driven policy (plus an RL alternative), prices current and recommended configurations, explains every change in plain language, and puts it all in a multi-tenant dashboard with human approval.

```
telemetry replay ─► forecasting (XGBoost quantiles, LSTM) ─► anomaly detection (residual + Isolation Forest)
      ▲                                                                   │
      │                                                                   ▼
 dashboard ◄─ explanation (OpenAI / template) ◄─ cost model ◄─ optimisation (forecast policy / PPO)
 (approve / reject)
```

## Repository layout

| Path | What |
|---|---|
| `ingestion/` | Raw-data parsing, profiling and preprocessing (Azure Functions 2019 trace, Borg sample) |
| `cloudopt/` | Shared Python package: data loading, simulator, policies, forecasting, anomaly detection, RL, evaluation |
| `backend/` | FastAPI service: JWT auth and roles, replay engine (the live closed loop), REST API |
| `frontend/` | React + Vite + Tailwind dashboard |
| `ml/artifacts/` | Trained models (git-ignored except `demo/`, the compact bundle the live product loads) |
| `reports/` | Evaluation outputs (JSON + figures), also shown on the dashboard's Evaluation page |
| `docs/` | Data profiles and design notes (report material) |
| `data/` | Raw and processed data (git-ignored) |

## Quick start (local)

Prerequisites: Python 3.11+, Node 20+, Docker.

```bash
# 1. Python environment
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements/backend.txt -r requirements/rl.txt   # .venv/bin/python on macOS/Linux
.venv/Scripts/python -m pip install -e .

# 2. Database (TimescaleDB; DB_IMAGE=postgres:16 also works)
docker compose up -d db
cp .env.example .env            # set JWT_SECRET and SEED_ADMIN_PASSWORD

# 3. API + live replay (single worker: the replay engine must run once)
cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --port 8000

# 4. Dashboard (new terminal)
cd frontend && npm install && npm run dev     # http://localhost:5173
```

Demo accounts are created on first start with the password in `SEED_ADMIN_PASSWORD`:

- `admin@cloudopt.dev`: admin
- `viewer@cloudopt.dev`: read-only, all tenants
- `ops@northvale.cloudopt.dev`: read-only, one tenant

The repository ships the trained demo bundle (`ml/artifacts/demo/`), so steps 1–4 are enough to run the product.

## Rebuilding data, models and evaluation

Raw data (not in git) goes in `data/raw/`: the Azure Functions Trace 2019 (Kaggle `theodoram/azure-2019-public-dataset`) and optionally the Borg sample (`derrickmwiti/google-2019-cluster-sample`).

```bash
python ingestion/preprocess_azure.py         # -> data/processed/azure_*.parquet
python -m cloudopt.forecast.xgb              # XGBoost quantile models, 5 min .. 24 h
python -m cloudopt.anomaly                   # residual Isolation Forest + evaluation
python -m cloudopt.forecast.lstm             # LSTM 1 h / 24 h + comparison
python -m cloudopt.eval.frontier             # baselines + CloudOpt cost-vs-SLA curves
python -m cloudopt.rl.train                  # PPO policies + comparison
python backend/scripts/eval_explanations.py  # explanation rubric (template; OpenAI if key set)
python -m cloudopt.demo_bundle               # compact bundle for the live product
```

Tests: `python -m pytest` (simulator) and `cd backend && ../.venv/Scripts/python -m pytest` (API, auth, closed loop).

## Data

| Dataset | Role |
|---|---|
| Azure Functions Trace 2019 (CC BY 4.0) | **Primary.** Per-minute invocations for 14 days; tenant = subscription, service = app |
| Google Borg 2019 Kaggle sample (CC BY-NC-SA 4.0) | Secondary. Real request-vs-usage ratios calibrate the over-provisioned baseline |

Ingestion is a simulated replay of public trace data, because there is no live tenant infrastructure. See [docs/data_profile_azure.md](docs/data_profile_azure.md) and [docs/data_profile.md](docs/data_profile.md). The Borg scripts are `ingestion/parse_kaggle.py` and `ingestion/profile_kaggle.py`.
