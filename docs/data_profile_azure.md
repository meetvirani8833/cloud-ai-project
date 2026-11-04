# Data Profile: Azure Functions Trace 2019 (primary dataset)

Source: Microsoft Azure Functions Trace 2019 (Shahrad et al., "Serverless in the Wild", USENIX ATC 2020), Kaggle mirror `theodoram/azure-2019-public-dataset`. Licence **CC BY 4.0**, which permits commercial use, unlike the Borg sample's CC BY-NC-SA.

Built by `ingestion/preprocess_azure.py`. All numbers below are from the full files.

## Why this replaced the Borg sample as the primary dataset

The Kaggle Borg export has no temporal continuity (see [data_profile.md](data_profile.md)): no job has more than 4 of 8,928 five-minute windows, and the aggregate series has 24 h autocorrelation of 0.004. The Azure Functions trace records **every minute for 14 days** for every included function.

| Test | Borg Kaggle sample | Azure Functions 2019 |
|---|---|---|
| Whole-trace autocorrelation, 1 day | 0.004 (hourly) | **0.62** (per-minute), 0.64 (hourly) |
| Whole-trace autocorrelation, 1 week | −0.025 (hourly) | **0.39** (per-minute), 0.30 (hourly) |
| Longest continuous per-entity series | 4 windows | **20,160 minutes** (all 14 days) |
| Tenant field | hashed `user` | hashed `HashOwner` (Azure subscription) |

The Borg file is kept as a secondary source: it supplies real evidence of over-provisioning (63.9% of usage records use less than half their CPU request, median 22%), which calibrates the static-provisioning baseline.

## Raw files

| File | Shape | Content |
|---|---|---|
| `invocations_per_function_md.feather` | 618,545 × 1,445 | Invocations per function per minute; one row per function-day; 1,440 minute columns |
| `function_durations_percentiles.feather` | 662,927 × 15 | Per function per day: mean/min/max execution time (ms), percentiles, count |
| `app_memory_percentiles.feather` | 206,809 × 13 | Per app per day: allocated memory (MB) mean and percentiles; **days 1–12 only** |
| `day1_iit_hist_df.feather` | 46,412 × 18 | Inter-invocation-time histogram for day 1 (from the dataset's companion notebook); not used |

Cardinality: 14,681 owners (tenants), 24,274 apps, 72,359 functions, 14 days, 12.5 billion invocations. Triggers: timer, http, queue, orchestration, event, storage, others.

Duplicates removed: 64 function-day rows in durations, 39 app-day rows in memory.

## Modelling conventions

- **Tenant = `HashOwner`** (an Azure subscription), **service = `HashApp`**. In Azure Functions the app is the unit of resource allocation, so functions are summed to app level.
- **Request volume** = invocations per minute (directly observed).
- **Compute demand ("concurrency")** = invocations/min × mean execution time (s) / 60. By Little's law, this is the average number of concurrently busy workers. Mean execution time is the invocation-weighted average across the app's functions for that day. CPU utilisation is then derived in the simulator as `concurrency / provisioned capacity`. This is an assumption to state in the report: the trace has no direct CPU measurement.
- **Memory** = average allocated MB per app per day. Days 13–14 are missing in the public trace and are filled with each app's median (`mem_filled = True`, 14.5% of app-days).
- **Framing:** the trace is used as realistic request traffic for SaaS services that a tenant provisions with replicas/instances. The real Azure Functions platform autoscales these apps itself; we deliberately treat the workload as if the tenant had to provision it, which is the scenario the problem statement describes.
- **Time:** the trace gives relative day 1–14 and minute 1–1,440 only. Timestamps are relative (`t` = minute 0..20,159, `slot` = 5-minute window 0..4,031); the replay service maps them onto wall-clock time.

## Selection (default thresholds)

An app is kept if it has traffic on all 14 days, ≤5% zero-invocation minutes, a mean of ≥10 invocations/minute, duration data on all 14 days, and memory data on ≥10 days.

**Result: 728 apps across 542 tenants** (91 tenants have ≥2 apps, 30 have ≥3). Dominant triggers: http 215, queue 163, timer 163, event 153, orchestration 19, storage 13, others 2.

## Properties of the selected set (5-minute resolution)

| Metric | Value |
|---|---|
| Rows | 2,935,296 (728 apps × 4,032 slots, no gaps, no nulls) |
| Request rate (rps) | mean 11.7, p90 13.1, p99 189, max 4,111 |
| Concurrency (busy workers) | mean 7.4, p90 3.4, p99 85, max 5,697 (heavy-tailed) |
| Allocated memory (MB) | mean 176, p90 236, max 1,117 |
| Autocorrelation, lag 5 min | median 0.91; 87% of apps > 0.5 |
| Autocorrelation, lag 1 day | median 0.29; 29% of apps > 0.5 |
| Autocorrelation, lag 7 days | median 0.18; 27% of apps > 0.5 |

Implications:
- Short-horizon (5–60 min) forecasting is well supported for almost all apps.
- 24-hour-horizon forecasting is meaningful only for the ~29% of apps with daily seasonality. Report per-segment results and do not average them away.
- Timer-triggered apps are often near-constant and trivially predictable. Demo tenants should favour http/queue/event apps with visible daily patterns.
- With only 14 days, weekly seasonality is observed twice. Proposed split: days 1–10 train, 11–12 validation, 13–14 test.

## Validation performed

- Minute ordering checked against raw rows at the start, middle and end of days 1, 7 and 14 for 5 random apps: 0 mismatches.
- Per-app invocation totals in the minute table equal raw totals for all 728 apps.
- 5-minute invocation sum equals minute sum exactly.

## Outputs (`data/processed/`)

| File | Grain | Key columns |
|---|---|---|
| `azure_apps.parquet` | app | app_id, tenant_id, HashOwner, HashApp, total_invocations, mean_per_min, empty_share, trigger_share_*, apps_in_tenant |
| `azure_app_minute.parquet` | app × minute | app_id, t, invocations |
| `azure_app_daily.parquet` | app × day | duration_ms, duration_p99_ms, mem_mb, mem_p99_mb, mem_filled |
| `azure_app_5min.parquet` | app × 5 min | app_id, slot, invocations, rps, concurrency, mem_mb |
