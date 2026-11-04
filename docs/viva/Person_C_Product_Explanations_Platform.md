# Person C: The Product (Backend, Login, Dashboard), Explanations and the Live Demo

**Your job in the evaluation:** explain *the working product: how the live loop runs, how login and roles work, how the dashboard is built, how the LLM writes explanations and how we make sure they're true*, and run the **live demo**. You answer "is this a real product or a notebook?"

Rename "Person C" with your name. Read the shared section, then your own sections, then skim the other two files for cross-questions.

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

> "I built the product around the models: the FastAPI backend, the database, secure login with admin and viewer roles, the live engine that runs the forecast-detect-decide loop every five simulated minutes, and the React dashboard. I also built the explanation layer: an LLM writes the reason behind each recommendation, but because LLMs can make things up, every explanation is automatically checked against the real numbers before anyone sees it. With our first prompt only 75% of drafts were fully correct; after improving it, 95%; and after the fact-check, 100% of what users see is correct. I'll also run the live demo."

---

## 2. What you own

| Topic | Files |
|---|---|
| API and app startup | `backend/app/main.py`, `backend/app/api/routes/*.py` |
| Auth and roles | `backend/app/security.py`, `backend/app/api/deps.py`, `backend/app/api/routes/auth.py` |
| Database | `backend/app/models.py`, `backend/app/db.py`, `docker-compose.yml` |
| Live loop | `backend/app/services/engine.py`, `runtime.py`, `bootstrap.py` |
| Explanations | `backend/app/services/explain.py`, `explain_rubric.py`, `backend/scripts/eval_explanations.py` |
| Dashboard | `frontend/src/` (pages, components, charts) |
| Tests | `backend/tests/` |
| Deployment files | `Dockerfile`, `.dockerignore`, `frontend/vercel.json`, `.env.example` |
| Docs | `README.md`, `docs/system_design.md` |

---

## 3. The architecture in one picture

```
Azure trace (replayed at 60x) ──► replay engine (a background thread in the API)
                                      │  every 5 seconds = one 5-minute slot
                                      ▼
   record usage/cost/SLA ─► detect anomalies ─► forecast ─► decide replicas
                                      │                         │
                            PostgreSQL + TimescaleDB       autopilot applies it, or
                            (usage_metrics, forecast_points  creates a pending recommendation
                             are time-series "hypertables")  + explanation
                                      ▼
                   FastAPI (JWT auth, roles) ◄──── React dashboard (polls every 5 s)
```

**Tech stack:** Python, FastAPI, SQLAlchemy, PostgreSQL + TimescaleDB (a Postgres extension that's fast for time-stamped data), JWT login, XGBoost/scikit-learn/ONNX Runtime for inference, React + TypeScript + Vite + Tailwind CSS + Recharts + TanStack Query. The OpenAI API (gpt-4.1-mini) for explanations only.

**Why TimescaleDB?** The data is relational (tenants → services → usage) with a time-series layer. TimescaleDB gives time-series speed while keeping normal SQL joins. If a host doesn't offer the extension, the app falls back to plain Postgres.

**Why no Kafka?** A message queue adds complexity with no benefit at this scale; a simple replay loop does the same job.

---

## 4. The live loop (what happens every tick)

For each service, per slot *t* (`backend/app/services/engine.py`):
1. **Observe:** write usage at the current replica count: requests/s, mean and peak demand, CPU utilisation, **cost**, the **baseline cost** (what the same slot would have cost with the pre-onboarding static setup), and whether the SLA was violated.
2. **Detect:** score slot *t* against the forecast issued at *t−1*.
3. **Forecast:** quantile forecasts for t+1, t+3, t+6, t+12 (5, 15, 30, 60 minutes). Stored for the dashboard chart.
4. **Expire** stale pending recommendations (older than 30 simulated minutes).
5. **Decide** replicas for t+1 (rules, or RL agent if that service uses it).
6. **Act:** *Autopilot* services apply changes within the guardrail (default ±50% per step). Larger changes become a **pending recommendation** with cost impact, risk before/after, confidence and an explanation. *Approval-required* services send every change to a human. *Manual* services are only observed.

**Replay and onboarding.** The first 10 days (trace days 1–10) are written as "life before onboarding": static, over-provisioned capacity. From day 11 on, CloudOpt manages capacity, which is why the Overview cost chart has a sharp drop. The replay can be paused, sped up or reset (Settings → Replay), and admins can inject scenarios (spike, drop, gradual surge).

**Important constraints.** One API worker only (the engine must not run twice against one database). Schema created with `create_all` rather than migrations (to keep the timeline realistic); we added a small additive migration for one new column. Both are listed as limitations.

---

## 5. Login, roles and security

- **Passwords:** hashed with **bcrypt** (passwords over 72 bytes are rejected, not silently truncated).
- **Sessions:** **JWT** (HS256) access tokens, expiry 8 hours; the secret comes from an environment variable (`.env`, git-ignored).
- **Roles:** **admin** can approve/reject, change settings, manage users and view the audit log. **viewer** is read-only. A viewer can be **scoped to one tenant**: other tenants return **404** (not 403) so a scoped user can't even discover they exist.
- **Safety rules:** the last active admin can't be demoted or disabled; you can't disable yourself; disabled accounts are rejected immediately (the token check re-reads the user every request).
- **Login errors** are identical for "unknown email" and "wrong password" (no account enumeration).
- **Audit log:** every login, approval, rejection, setting change, scenario and user change is recorded.
- **Demo accounts** (created on first start): `admin@cloudopt.dev`, `viewer@cloudopt.dev`, `ops@northvale.cloudopt.dev` (one tenant only). The password is in your local `.env` (`SEED_ADMIN_PASSWORD`); don't put it on slides.
- **Secrets:** the OpenAI key is only in `.env`, which is git-ignored; it was checked that no key is in the repository. Rotate it after the evaluation.

---

## 6. The explanation layer (generative AI)

**What it does.** For every recommendation the engine builds a structured **facts** object (forecast p50/p90, current demand, predicted utilisation with and without the change, risk before/after, cost delta, confidence, any anomaly). An LLM (**OpenAI gpt-4.1-mini**, prompt only, no training) writes the explanation in the format of the brief:

> Recommendation: Reduce checkout-api replicas from 32 to 19.
> Reason: The median forecast for the next hour is 3.21 busy workers (90th percentile 6.57) against 2.85 now; with 19 replicas peak utilisation would be 69%, just below the 70% target.
> Estimated cost impact: −$0.32/hour (−$234/month at AWS Fargate (approx.)).
> Estimated risk: SLA-violation risk 1% → 6% over the next hour.
> Confidence: Medium (0.64).
> Action status: Awaiting approval.

**The problem with LLMs and how we solved it.** LLMs can state false things confidently. So every explanation passes an automatic **rubric** (`explain_rubric.py`, **11 checks**): all required lines present; replica counts correct; direction correct (increase/reduce); quotes the forecast numbers; mentions an anomaly only if one triggered it (negations like "no anomaly" handled); cost sign correct; risk before/after cited; threshold claims are true (e.g. never "81% is above 90%"); confidence label matches (High ≥ 0.75, Medium ≥ 0.5, else Low); no internal field names leaked; **no invented numbers** (every number must come from the facts, compared at the precision it's printed).

**Pipeline:** LLM draft → rubric → if it fails, **one retry** with the failed checks listed → if it fails again (or the API errors or the key is missing), a **deterministic template** is shown, labelled as a fallback. The "Action status" line is **set by code**, never by the model, and is updated when an admin approves or rejects. A spend cap (`OPENAI_MAX_CALLS_PER_HOUR`, default 120) falls back to the template beyond it.

**Results (`reports/explainability_eval.json`).**

| | Pass all checks |
|---|---|
| Template (596 recommendations from the live engine) | **99.3%** |
| LLM first drafts, our first prompt (40 samples) | 75% |
| LLM first drafts, improved prompt (40 samples) | **95%** (38 first time, 2 fixed on retry) |
| What users see after the fact-check | **100%** |

**What the rubric found.** Template bugs: one explanation said "81%, above the 90% SLA threshold", another called 71% "within the 70% target". LLM problems: raw field names like `forecast_p50_next_hour` in user-facing text, stale status lines, wrong confidence labels. All fixed. The rubric's own false positives were fixed too (negated mentions, "90th percentile" read as a number).

**Why the LLM never decides anything.** It only *explains* a decision the models and rules already made, from facts we pass in. It cannot change replica counts.

---

## 7. The dashboard

- **Overview:** KPI tiles (current run-rate, projected monthly savings, SLA violation rate, average CPU utilisation, each compared with before CloudOpt), the hourly cost chart (dashed = before, blue = now), "since onboarding" before/now bars, top savings (tick meters), "needs attention" (24-hour SLA-violation heat strip per tenant), latest scaling decisions, recent anomalies.
- **Recommendations:** the approval inbox (Needs approval / Autopilot / History), each card with replicas, cost impact, SLA risk, confidence, the explanation, an expiry countdown, and Approve/Reject (reject asks for an optional reason saved to the audit log).
- **Tenants / Services / Anomalies / Savings report (CSV download) / Evaluation / Settings** (replay controls, scenario injection, pricing models, users, audit log).
- **UX details:** loading, empty and error states everywhere; connection-lost banner; session expiry returns to login with a message; permission-aware buttons (viewers see disabled actions with a reason); responsive layout with a bottom tab bar and card lists on phones; data refreshes every 5 seconds.

---

## 8. Quality: tests and bugs we found

**Tests:** 29 backend tests (login, roles, tenant isolation, user management, the closed loop, approvals, scenario validation, pricing guards, reset, RL engine, hourly strips, explanation rubric) + 9 simulator tests = **38**.

**Real bugs the tests/screenshots caught (good to mention):**
- The hourly cost chart plotted 5-minute costs as hourly because SQLAlchemy 2 treats `/` as true division, so every slot became its own "hour". Fixed with `//` plus regression tests.
- A pending-approval test showed that, after onboarding, services were already over-provisioned, so the test now sets a service under-provisioned on purpose.
- Port 5432 was already used by a local PostgreSQL service, so our database runs in Docker on 5433.

---

## 9. Questions you will probably get (and answers)

**Q1. Is this a real product or just a notebook?**
It's a working multi-tenant web app: real database, real login with roles, a live loop, an approval workflow, audit log and a React dashboard. The data is replayed, because we have no live cloud tenant.

**Q2. How does login work?**
Email and password; bcrypt-hashed; the server issues a JWT; every request is checked against the user record, so disabling an account locks it out immediately. Admin and viewer roles; viewers can be limited to one tenant.

**Q3. How do you keep one tenant from seeing another's data?**
Every endpoint filters by the user's allowed tenants; asking for another tenant returns 404. It's covered by automated tests.

**Q4. Why can't an LLM be trusted, and what did you do?**
It can state false numbers. We generate explanations only from structured facts, verify every one with an 11-check rubric, retry once, and fall back to a template. 100% of shown explanations pass.

**Q5. Does the LLM make decisions?**
No. Decisions come from the forecast-based rules or the RL agent. The LLM only explains.

**Q6. What if the OpenAI API is down or the key is missing?**
The explanation falls back to the deterministic template and the system carries on. There's also an hourly call cap.

**Q7. What's "human in the loop" here?**
Small changes (within a per-service guardrail, default 50%) apply automatically; larger ones wait for an admin to approve or reject; everything is audited. Services can also be set to approval-only or observe-only.

**Q8. Why TimescaleDB and FastAPI?**
TimescaleDB for time-series speed with normal SQL joins; FastAPI so the ML code and API share one language (Python).

**Q9. How does it scale to many tenants?**
Inference is batched across all services in one NumPy/XGBoost call per tick, so cost grows slowly with service count. The limit is one engine per database (single worker). Scaling out would need a job queue or leader election; that's listed as future work.

**Q10. How would this connect to a real cloud?**
Replace the replay with a metrics adapter (CloudWatch, Azure Monitor or Prometheus) and replace "set replicas" with a call to the Kubernetes/cloud API. The decision engine, cost model, approvals and explanations stay the same.

**Q11. Where's the hosted link?**
(Answer according to the real situation at evaluation time.) The system runs locally and the code is on GitHub: github.com/meetvirani8833/cloud-ai-project. Hosting is prepared (Dockerfile, Vercel config); the planned setup is Railway for API + database and Vercel for the frontend.

**Q12. What about security of the API key?**
It's only in a git-ignored `.env`, never committed; we recommend rotating it and setting a spend limit.

**Hard ones:**
- *"Isn't the replay fake?"* The traffic is real production data from Azure; the *replay* is the simulation of live arrival. It's a standard approach when no live tenant exists, and the report says so.
- *"Why does the demo use 8 fictional companies?"* The trace is anonymised (hashed owners), so we give the 8 selected tenants fictional names; the hashes are kept so every number traces back to the source.
- *"Could an admin approve a bad change?"* Yes; that's the point of human-in-the-loop. Each card shows risk before/after and confidence, bad approvals are audited, and the next ticks correct course.

---

## 10. Cross-questions from the others' areas

- **How good are the forecasts?** 17.7% WAPE at 5 min vs 30.1% naive; 80% bands contain truth 79–82% of the time (Person A).
- **Why did you change datasets?** Borg sample had no usable time series (4 of 8,928 windows per job, daily autocorrelation 0.004); Azure Functions has 14 days per-minute data (Person A).
- **How is the decision made?** Replicas for the p90 forecast at 70% target plus anomaly guard, look-ahead scale-in and hysteresis (Person B).
- **How do you know it beats alternatives?** Same unseen traffic through every strategy; 39.8% cheaper than reactive at equal SLA, 80.6% cheaper than static peak (Person B).

---

## 11. The live demo: your script (about 5 minutes)

**Before you start (do this 10 minutes earlier):**
1. Start Docker Desktop, then in the project folder: `docker compose up -d db`.
2. Terminal 1: `cd backend` then `..\.venv\Scripts\python -m uvicorn app.main:app --port 8000`.
3. Terminal 2: `cd frontend` then `npm run dev`; open http://localhost:5173.
4. Sign in as `admin@cloudopt.dev` (password from `.env`). Check the pill at top right says "Live".
5. Have the backup video ready. Check the internet if you use the LLM explanations (without internet the template is used, and that's fine).

**Script:**
1. **Overview.** "Dashed line is what these 8 companies paid before CloudOpt; blue is now. The drop is when CloudOpt took over." Point at the KPIs and the heat strip.
2. **Service page** (Services → `checkout-api`). Show demand, forecast band and next-hour forecast.
3. **Inject a scenario:** Settings → Replay & scenarios → service `checkout-api`, *Sudden spike*, 5×, 2 intervals → Inject. Say "now we pretend traffic suddenly jumps."
4. **Within about 10 seconds:** Anomalies page shows the spike; service page shows demand jump; replicas rise.
5. **Recommendations:** open a pending card, read the explanation, point at cost impact, risk and confidence, click **Approve** (or Reject with a reason). Mention it's in the audit log (Settings → Audit log).
6. **Roles:** sign out, sign in as `viewer@cloudopt.dev`: buttons are disabled, Settings is hidden. Optionally `ops@northvale.cloudopt.dev` sees only one company.
7. **Evaluation page:** hand over to Person B for the results.

**If something fails:** don't debug live. Say "let me show the recorded run", play the backup video, then continue with slides.

---

## 12. Things not to say

- Don't call savings "real". Say "simulated on replayed real traffic".
- Don't say the LLM "decides" or "predicts". It *explains*.
- Don't say "100% accurate explanations"; say "every explanation shown passes our automatic fact-check; the LLM's raw first drafts pass 95% of the time".
- Don't show the `.env` file or the API key on screen (you opened it in your editor: close that tab before presenting).
- **If you don't know:** point at `docs/system_design.md` or the code; never invent a number.
