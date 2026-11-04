"""Read endpoints behind the dashboard: overview KPIs, tenants, services, time series, anomalies.

All times are simulated trace time. "Now" is the last replayed slot. Every endpoint is scoped
to the caller's visible tenants (tenant-scoped viewers see only their own company).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import check_tenant_access, current_user, visible_tenant_ids
from app.db import get_db
from app.models import Anomaly, ForecastPoint, PricingModel, Recommendation, ReplayState, Service, Tenant, UsageMetric, User
from app.schemas import AnomalyOut, ServiceOut, TenantOut
from app.services.engine import SLOT, replica_hour_price, slot_time

router = APIRouter(prefix="/api", tags=["dashboard"])

SLOTS_PER_HOUR = 12


def _state(db: Session) -> ReplayState:
    state = db.get(ReplayState, 1)
    if state is None:
        raise HTTPException(503, detail="The platform is still initialising. Try again in a few seconds.")
    return state


def _scope(stmt, model, tenant_ids):
    return stmt if tenant_ids is None else stmt.where(model.tenant_id.in_(tenant_ids))


def _services(db: Session, tenant_ids, tenant_id: str | None = None) -> list[Service]:
    stmt = _scope(select(Service), Service, tenant_ids)
    if tenant_id:
        stmt = stmt.where(Service.tenant_id == tenant_id)
    return list(db.scalars(stmt.order_by(Service.tenant_id, Service.id)).all())


def _run_rates(db: Session, services: list[Service]) -> tuple[float, float]:
    """Current and pre-onboarding hourly cost (USD/hour) for these services."""
    tenants = {t.id: t for t in db.scalars(select(Tenant)).all()}
    prices = {p.key: p for p in db.scalars(select(PricingModel)).all()}
    cur = base = 0.0
    for s in services:
        price = replica_hour_price(prices[tenants[s.tenant_id].pricing_key], s.replica_vcpu, s.replica_mem_gb)
        cur += s.replicas * price
        base += s.baseline_replicas * price
    return cur, base


def _window_stats(db: Session, service_ids: list[str], lo: int, hi: int) -> dict:
    """Aggregates over slots [lo, hi)."""
    if not service_ids or hi <= lo:
        return {"cost": 0.0, "baseline_cost": 0.0, "violations": 0, "slots": 0, "avg_util": None}
    row = db.execute(
        select(
            func.coalesce(func.sum(UsageMetric.cost_usd), 0.0),
            func.coalesce(func.sum(UsageMetric.baseline_cost_usd), 0.0),
            func.coalesce(func.sum(case((UsageMetric.sla_violation, 1), else_=0)), 0),
            func.count(),
            func.avg(UsageMetric.cpu_util),
        ).where(UsageMetric.service_id.in_(service_ids), UsageMetric.slot >= lo, UsageMetric.slot < hi)
    ).one()
    return {"cost": float(row[0]), "baseline_cost": float(row[1]), "violations": int(row[2]),
            "slots": int(row[3]), "avg_util": None if row[4] is None else float(row[4])}


@router.get("/replay")
def replay_status(request: Request, db: Session = Depends(get_db), _: User = Depends(current_user)):
    st = _state(db)
    last = max(st.current_slot - 1, 0)
    total = 4032
    return {
        "current_slot": last,
        "sim_time": slot_time(st.epoch, last),
        "onboarded_at": slot_time(st.epoch, st.start_slot),
        "trace_day": last // 288 + 1,
        "running": st.running,
        "finished": st.finished,
        "seconds_per_slot": st.seconds_per_slot,
        "speedup": round(SLOT.total_seconds() / st.seconds_per_slot, 1),
        "progress": round((st.current_slot - st.start_slot) / max(total - st.start_slot, 1), 4),
        "scenario": st.scenario if st.scenario and st.scenario.get("slots_left", 0) > 0 else None,
        "rl_available": request.app.state.engine.rt.rl is not None,
    }


@router.get("/overview")
def overview(
    tenant_id: str | None = None,
    hours: int = Query(24, ge=1, le=24 * 14),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if tenant_id:
        check_tenant_access(user, tenant_id)
    st = _state(db)
    scope = visible_tenant_ids(user)
    services = _services(db, scope, tenant_id)
    ids = [s.id for s in services]
    now = st.current_slot
    win_lo = max(now - hours * SLOTS_PER_HOUR, 0)
    cur_rate, base_rate = _run_rates(db, services)
    since = _window_stats(db, ids, st.start_slot, now)
    window = _window_stats(db, ids, win_lo, now)
    prev = _window_stats(db, ids, max(win_lo - hours * SLOTS_PER_HOUR, 0), win_lo)
    pre = _window_stats(db, ids, max(st.start_slot - hours * SLOTS_PER_HOUR, 0), st.start_slot)

    # Hourly buckets for the trend chart (actual vs. pre-onboarding provisioning).
    # Floor division: in SQLAlchemy 2.x "/" is true division, which would put every slot in its own "hour".
    bucket = (UsageMetric.slot // SLOTS_PER_HOUR).label("bucket")
    trend_rows = db.execute(
        select(bucket, func.sum(UsageMetric.cost_usd), func.sum(UsageMetric.baseline_cost_usd),
               func.avg(UsageMetric.cpu_util), func.sum(case((UsageMetric.sla_violation, 1), else_=0)), func.count(),
               func.count(func.distinct(UsageMetric.slot)))
        .where(UsageMetric.service_id.in_(ids), UsageMetric.slot >= win_lo, UsageMetric.slot < now)
        .group_by(bucket).order_by(bucket)
    ).all() if ids else []
    # Costs are per-hour rates: an hour cut by the window edge, or still in progress, is scaled by
    # the slots it actually contains, so partial hours don't show up as fake dips.
    trend = [
        {"time": st.epoch + timedelta(hours=int(b)),
         "cost": round(float(c) * SLOTS_PER_HOUR / max(int(k), 1), 4),
         "baseline_cost": round(float(bc) * SLOTS_PER_HOUR / max(int(k), 1), 4),
         "avg_util": round(float(u or 0), 4), "violation_rate": round(int(v) / max(int(n), 1), 4),
         "partial": int(k) < SLOTS_PER_HOUR}
        for b, c, bc, u, v, n, k in trend_rows
    ]

    pend_q = _scope(select(func.count()).select_from(Recommendation), Recommendation, scope)
    pend_q = pend_q.where(Recommendation.status == "pending")
    anom_q = _scope(select(func.count()).select_from(Anomaly), Anomaly, scope)
    anom_q = anom_q.where(Anomaly.time >= slot_time(st.epoch, win_lo))
    if tenant_id:
        pend_q = pend_q.where(Recommendation.tenant_id == tenant_id)
        anom_q = anom_q.where(Anomaly.tenant_id == tenant_id)
    pending, anomalies = db.scalar(pend_q), db.scalar(anom_q)

    def rate(w):
        return None if w["slots"] == 0 else w["violations"] / w["slots"]

    return {
        "window_hours": hours,
        "services": len(services),
        "tenants": len({s.tenant_id for s in services}),
        "hourly_run_rate": round(cur_rate, 4),
        "baseline_hourly_run_rate": round(base_rate, 4),
        "projected_monthly_savings": round((base_rate - cur_rate) * 730, 2),
        "savings_since_onboarding": round(since["baseline_cost"] - since["cost"], 2),
        "savings_since_onboarding_pct": None if since["baseline_cost"] == 0 else
        round(100 * (since["baseline_cost"] - since["cost"]) / since["baseline_cost"], 2),
        "window_cost": round(window["cost"], 2),
        "prev_window_cost": round(prev["cost"], 2) if prev["slots"] else None,
        "sla_violation_rate": rate(window),
        "prev_sla_violation_rate": rate(prev),
        "pre_onboarding_sla_violation_rate": rate(pre),
        "avg_util": window["avg_util"],
        "pre_onboarding_avg_util": pre["avg_util"],
        "pending_recommendations": int(pending or 0),
        "anomalies": int(anomalies or 0),
        "trend": trend,
    }


@router.get("/tenants")
def list_tenants(db: Session = Depends(get_db), user: User = Depends(current_user)):
    st = _state(db)
    scope = visible_tenant_ids(user)
    stmt = select(Tenant) if scope is None else select(Tenant).where(Tenant.id.in_(scope))
    tenants = db.scalars(stmt.order_by(Tenant.id)).all()
    pend = dict(db.execute(select(Recommendation.tenant_id, func.count()).where(Recommendation.status == "pending")
                           .group_by(Recommendation.tenant_id)).all())
    lo = max(st.current_slot - 24 * SLOTS_PER_HOUR, 0)
    # Hourly SLA-violation rate per tenant over the last 24 h (index 0 = oldest hour), for the heat strip.
    # Aggregate per slot in SQL and bucket into hours here, keeping the hour index relative to `lo`.
    per_slot = db.execute(
        select(Service.tenant_id, UsageMetric.slot, func.sum(case((UsageMetric.sla_violation, 1), else_=0)), func.count())
        .join(Service, Service.id == UsageMetric.service_id)
        .where(UsageMetric.slot >= lo, UsageMetric.slot < st.current_slot)
        .group_by(Service.tenant_id, UsageMetric.slot)
    ).all()
    sums: dict[str, list[list[int]]] = {t.id: [[0, 0] for _ in range(24)] for t in tenants}
    for tid, slot, v, n in per_slot:
        h = (int(slot) - lo) // SLOTS_PER_HOUR
        if tid in sums and 0 <= h < 24:
            sums[tid][h][0] += int(v)
            sums[tid][h][1] += int(n)
    hourly = {tid: [round(v / n, 4) if n else None for v, n in cells] for tid, cells in sums.items()}
    out = []
    for t in tenants:
        services = _services(db, scope, t.id)
        cur, base = _run_rates(db, services)
        w = _window_stats(db, [s.id for s in services], lo, st.current_slot)
        since = _window_stats(db, [s.id for s in services], st.start_slot, st.current_slot)
        out.append({
            **TenantOut.model_validate(t).model_dump(),
            "services": len(services),
            "hourly_run_rate": round(cur, 4),
            "baseline_hourly_run_rate": round(base, 4),
            "savings_pct": None if base == 0 else round(100 * (base - cur) / base, 2),
            "savings_since_onboarding": round(since["baseline_cost"] - since["cost"], 2),
            "sla_violation_rate_24h": None if w["slots"] == 0 else round(w["violations"] / w["slots"], 4),
            "avg_util_24h": w["avg_util"],
            "pending_recommendations": int(pend.get(t.id, 0)),
            "violations_by_hour": hourly[t.id],
            "hours_start": slot_time(st.epoch, lo),
        })
    return out


@router.get("/services")
def list_services(tenant_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if tenant_id:
        check_tenant_access(user, tenant_id)
    st = _state(db)
    services = _services(db, visible_tenant_ids(user), tenant_id)
    last = st.current_slot - 1
    latest = {u.service_id: u for u in db.scalars(select(UsageMetric).where(UsageMetric.slot == last)).all()}
    names = dict(db.execute(select(Tenant.id, Tenant.name)).all())
    out = []
    for s in services:
        u = latest.get(s.id)
        out.append({
            **ServiceOut.model_validate(s).model_dump(),
            "tenant_name": names.get(s.tenant_id),
            "rps": None if u is None else round(u.rps, 3),
            "cpu_util": None if u is None else round(u.cpu_util, 4),
            "cpu_util_peak": None if u is None else round(u.cpu_util_peak, 4),
            "sla_violation": None if u is None else u.sla_violation,
        })
    return out


@router.get("/services/{service_id}")
def get_service(service_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = db.get(Service, service_id)
    if s is None:
        raise HTTPException(404, detail="Service not found")
    check_tenant_access(user, s.tenant_id)
    return {**ServiceOut.model_validate(s).model_dump(), "tenant_name": s.tenant.name}


@router.get("/services/{service_id}/timeseries")
def service_timeseries(
    service_id: str,
    hours: int = Query(6, ge=1, le=24 * 14),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    s = db.get(Service, service_id)
    if s is None:
        raise HTTPException(404, detail="Service not found")
    check_tenant_access(user, s.tenant_id)
    st = _state(db)
    lo = max(st.current_slot - hours * SLOTS_PER_HOUR, 0)
    usage = db.scalars(select(UsageMetric).where(UsageMetric.service_id == service_id, UsageMetric.slot >= lo)
                       .order_by(UsageMetric.slot)).all()
    # Past: what the 5-minute model predicted for each slot (forecast vs. actual overlay).
    past_fc = {f.target_time: f for f in db.scalars(
        select(ForecastPoint).where(ForecastPoint.service_id == service_id, ForecastPoint.horizon == 1,
                                    ForecastPoint.target_time >= slot_time(st.epoch, lo))).all()}
    # Future: the latest forecast for each horizon.
    future = db.scalars(select(ForecastPoint).where(
        ForecastPoint.service_id == service_id, ForecastPoint.issued_slot == st.current_slot - 1)
        .order_by(ForecastPoint.horizon)).all()
    cap = lambda r: r * s.replica_vcpu  # noqa: E731
    points = []
    for u in usage:
        f = past_fc.get(u.time)
        points.append({
            "time": u.time, "slot": u.slot, "demand": round(u.concurrency_peak, 4), "demand_mean": round(u.concurrency, 4),
            "rps": round(u.rps, 3), "replicas": u.replicas, "capacity": round(cap(u.replicas), 4),
            "cpu_util": round(u.cpu_util, 4), "cpu_util_peak": round(u.cpu_util_peak, 4), "cost": round(u.cost_usd, 5),
            "baseline_cost": round(u.baseline_cost_usd, 5), "sla_violation": u.sla_violation, "injected": u.injected,
            "forecast_p50": None if f is None else round(f.p50, 4),
            "forecast_p10": None if f is None else round(f.p10, 4),
            "forecast_p90": None if f is None else round(f.p90, 4),
        })
    return {
        "service_id": service_id,
        "onboarded_slot": st.start_slot,
        "sla_capacity_factor": s.tenant.sla_util,
        "points": points,
        "forecast": [{"time": f.target_time, "horizon_minutes": f.horizon * 5, "p10": round(f.p10, 4),
                      "p50": round(f.p50, 4), "p90": round(f.p90, 4), "p95": round(f.p95, 4)} for f in future],
    }


@router.get("/anomalies", response_model=list[AnomalyOut])
def list_anomalies(
    tenant_id: str | None = None,
    service_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if tenant_id:
        check_tenant_access(user, tenant_id)
    stmt = _scope(select(Anomaly), Anomaly, visible_tenant_ids(user))
    if tenant_id:
        stmt = stmt.where(Anomaly.tenant_id == tenant_id)
    if service_id:
        stmt = stmt.where(Anomaly.service_id == service_id)
    return db.scalars(stmt.order_by(Anomaly.time.desc()).limit(limit)).all()
