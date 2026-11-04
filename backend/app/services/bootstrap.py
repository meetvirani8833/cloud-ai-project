"""First-start seeding: pricing presets, tenants/services from the demo bundle, users, and
pre-onboarding history.

History design: slots before ``start_slot`` (trace days 1-10) are written as the customer's
life *before* onboarding, i.e. static "just to be safe" provisioning at ``baseline_replicas``.
From ``start_slot`` on the engine manages capacity, so the dashboard shows the before/after.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from cloudopt.sim.pricing import PRESETS

from app.config import get_settings
from app.models import PricingModel, ReplayState, Service, Tenant, UsageMetric, User
from app.security import hash_password
from app.services.engine import SLOT, replica_hour_price
from app.services.runtime import Runtime

log = logging.getLogger(__name__)

DEMO_VIEWER = ("viewer@cloudopt.dev", "Demo Viewer")


def _epoch(start_slot: int, slots_per_day: int) -> datetime:
    """A Monday 00:00 UTC such that the onboarding slot lands within the past week."""
    start_offset = timedelta(minutes=5 * start_slot)
    candidate = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - start_offset
    return candidate - timedelta(days=candidate.weekday())


def seed(db: Session, rt: Runtime) -> None:
    s = get_settings()
    for p in PRESETS.values():
        if db.get(PricingModel, p.key) is None:
            db.add(PricingModel(key=p.key, label=p.label, vcpu_hour=p.vcpu_hour, gb_hour=p.gb_hour))
    db.flush()

    if db.scalar(select(func.count()).select_from(Tenant)) == 0:
        seen: set[str] = set()
        for svc in rt.catalog["services"]:
            if svc["tenant_id"] not in seen:
                seen.add(svc["tenant_id"])
                db.add(Tenant(id=svc["tenant_id"], name=svc["tenant_name"], source_hash=svc["source_owner_hash"]))
            db.add(Service(
                id=svc["app_id"], tenant_id=svc["tenant_id"], name=svc["service_name"], trigger=svc["trigger"],
                source_hash=svc["source_app_hash"], replica_vcpu=svc["replica_vcpu"],
                replica_mem_gb=svc["replica_mem_gb"], replicas=svc["baseline_replicas"],
                baseline_replicas=svc["baseline_replicas"],
            ))
        db.flush()

    if db.scalar(select(func.count()).select_from(User)) == 0:
        db.add(User(email=s.seed_admin_email.lower(), full_name="Platform Admin",
                    password_hash=hash_password(s.seed_admin_password), role="admin"))
        db.add(User(email=DEMO_VIEWER[0], full_name=DEMO_VIEWER[1],
                    password_hash=hash_password(s.seed_admin_password), role="viewer"))
        first_tenant = rt.catalog["services"][0]
        db.add(User(email="ops@northvale.cloudopt.dev", full_name=f"{first_tenant['tenant_name']} Ops",
                    password_hash=hash_password(s.seed_admin_password), role="viewer",
                    tenant_id=first_tenant["tenant_id"]))

    state = db.get(ReplayState, 1)
    if state is None:
        start = (s.replay_start_day - 1) * rt.catalog["slots_per_day"]
        state = ReplayState(id=1, epoch=_epoch(start, rt.catalog["slots_per_day"]), start_slot=start,
                            current_slot=start, seconds_per_slot=s.replay_seconds_per_slot)
        db.add(state)
        db.flush()
        _backfill(db, rt, state)
    db.commit()
    rt.reset_observations(state.start_slot)
    _restore_observations(db, rt, state)


def _backfill(db: Session, rt: Runtime, state: ReplayState) -> None:
    services = {x.id: x for x in db.scalars(select(Service)).all()}
    tenants = {x.id: x for x in db.scalars(select(Tenant)).all()}
    prices = {p.key: p for p in db.scalars(select(PricingModel)).all()}
    hours = SLOT.total_seconds() / 3600
    slots = np.arange(state.start_slot)
    times = [state.epoch + int(t) * SLOT for t in slots]
    for sid, i in rt.index.items():
        sv, ten = services[sid], tenants[services[sid].tenant_id]
        price = replica_hour_price(prices[ten.pricing_key], sv.replica_vcpu, sv.replica_mem_gb)
        cap = sv.baseline_replicas * sv.replica_vcpu
        cost = sv.baseline_replicas * price * hours
        rows = [
            {
                "service_id": sid, "time": times[t], "slot": int(t), "rps": float(rt.rps[i, t]),
                "concurrency": float(rt.src_mean[i, t]), "concurrency_peak": float(rt.src_peak[i, t]),
                "mem_mb": float(rt.mem_mb[i, t]), "replicas": sv.baseline_replicas,
                "cpu_util": float(rt.src_mean[i, t] / cap), "cpu_util_peak": float(rt.src_peak[i, t] / cap),
                "cost_usd": cost, "baseline_cost_usd": cost,
                "sla_violation": bool(rt.src_peak[i, t] / cap > ten.sla_util), "injected": False,
            }
            for t in slots
        ]
        db.execute(insert(UsageMetric), rows)
    log.info("Backfilled %d slots of pre-onboarding history for %d services", len(slots), len(services))


def _restore_observations(db: Session, rt: Runtime, state: ReplayState) -> None:
    """After a restart, reload replayed slots (including injected scenarios) from the database
    so the forecasters see exactly the history the dashboard shows."""
    rows = db.execute(
        select(UsageMetric.service_id, UsageMetric.slot, UsageMetric.concurrency, UsageMetric.concurrency_peak)
        .where(UsageMetric.slot >= state.start_slot, UsageMetric.slot < state.current_slot)
    ).all()
    for sid, slot, mean, peak in rows:
        i = rt.index.get(sid)
        if i is not None:
            rt.obs_mean[i, slot] = mean
            rt.obs_peak[i, slot] = peak
    # Any gap (e.g. a crash mid-tick) falls back to the source trace.
    for t in range(state.start_slot, state.current_slot):
        missing = np.isnan(rt.obs_peak[:, t])
        rt.obs_peak[missing, t] = rt.src_peak[missing, t]
        rt.obs_mean[missing, t] = rt.src_mean[missing, t]
