"""Configuration and control: pricing, tenant/service settings, replay, scenarios, audit log,
and the offline evaluation reports."""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import check_tenant_access, current_user, require_admin
from app.config import REPO_ROOT
from app.db import get_db
from app.models import AuditLog, PricingModel, ReplayState, Service, Tenant, User
from app.schemas import (PricingOut, PricingUpsert, ReplayControl, ScenarioIn, ServiceOut, ServiceUpdate, TenantOut,
                         TenantUpdate)
from app.services.engine import reset_replay

router = APIRouter(prefix="/api", tags=["admin"])

REPORTS = REPO_ROOT / "reports"


@router.get("/pricing", response_model=list[PricingOut])
def list_pricing(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return db.scalars(select(PricingModel).order_by(PricingModel.is_custom, PricingModel.key)).all()


@router.put("/pricing/{key}", response_model=PricingOut)
def upsert_pricing(key: str, body: PricingUpsert, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if not re.fullmatch(r"[a-z0-9_]{2,40}", key):
        raise HTTPException(422, detail="Key must be 2-40 characters: lowercase letters, digits, underscores")
    p = db.get(PricingModel, key)
    if p is None:
        p = PricingModel(key=key, is_custom=True, label=body.label, vcpu_hour=body.vcpu_hour, gb_hour=body.gb_hour)
        db.add(p)
    else:
        p.label, p.vcpu_hour, p.gb_hour = body.label, body.vcpu_hour, body.gb_hour
    db.add(AuditLog(actor=admin.email, action="pricing_saved", target=key, details=body.model_dump()))
    db.commit()
    return p


@router.delete("/pricing/{key}", status_code=204)
def delete_pricing(key: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    p = db.get(PricingModel, key)
    if p is None:
        raise HTTPException(404, detail="Pricing model not found")
    if not p.is_custom:
        raise HTTPException(400, detail="Built-in provider presets cannot be deleted")
    in_use = db.scalars(select(Tenant.name).where(Tenant.pricing_key == key)).all()
    if in_use:
        raise HTTPException(409, detail=f"In use by: {', '.join(in_use)}. Switch them to another model first.")
    db.delete(p)
    db.add(AuditLog(actor=admin.email, action="pricing_deleted", target=key))
    db.commit()


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
def update_tenant(tenant_id: str, body: TenantUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(404, detail="Tenant not found")
    if body.pricing_key is not None and db.get(PricingModel, body.pricing_key) is None:
        raise HTTPException(422, detail="Unknown pricing model")
    target = body.target_util if body.target_util is not None else t.target_util
    sla = body.sla_util if body.sla_util is not None else t.sla_util
    if target >= sla:
        raise HTTPException(422, detail="Target utilisation must be below the SLA threshold")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(t, k, v)
    db.add(AuditLog(actor=admin.email, action="tenant_updated", target=tenant_id, details=body.model_dump(exclude_none=True)))
    db.commit()
    return t


@router.patch("/services/{service_id}", response_model=ServiceOut)
def update_service(service_id: str, body: ServiceUpdate, request: Request, db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)):
    s = db.get(Service, service_id)
    if s is None:
        raise HTTPException(404, detail="Service not found")
    lo = body.min_replicas if body.min_replicas is not None else s.min_replicas
    hi = body.max_replicas if body.max_replicas is not None else s.max_replicas
    if lo > hi:
        raise HTTPException(422, detail="Minimum replicas cannot exceed maximum replicas")
    with request.app.state.engine.lock:
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(s, k, v)
        s.replicas = min(max(s.replicas, s.min_replicas), s.max_replicas)
        db.add(AuditLog(actor=admin.email, action="service_updated", target=service_id,
                        details=body.model_dump(exclude_none=True)))
        db.commit()
    return s


@router.post("/replay/control")
def replay_control(body: ReplayControl, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    st = db.get(ReplayState, 1)
    if body.running is not None:
        if body.running and st.finished:
            raise HTTPException(409, detail="The replay has finished. Reset it to start again.")
        st.running = body.running
    if body.seconds_per_slot is not None:
        st.seconds_per_slot = body.seconds_per_slot
    db.add(AuditLog(actor=admin.email, action="replay_control", details=body.model_dump(exclude_none=True)))
    db.commit()
    return {"running": st.running, "seconds_per_slot": st.seconds_per_slot}


@router.post("/replay/reset")
def replay_reset(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    engine = request.app.state.engine
    with engine.lock:
        st = db.get(ReplayState, 1)
        reset_replay(db, engine, st)
        db.add(AuditLog(actor=admin.email, action="replay_reset_requested"))
        db.commit()
    return {"ok": True}


@router.post("/replay/scenario")
def inject_scenario(body: ScenarioIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    s = db.get(Service, body.service_id)
    if s is None:
        raise HTTPException(404, detail="Service not found")
    st = db.get(ReplayState, 1)
    if st.scenario and st.scenario.get("slots_left", 0) > 0:
        raise HTTPException(409, detail="A scenario is already running. Wait for it to finish.")
    if not st.running:
        raise HTTPException(409, detail="Resume the replay to inject a scenario")
    st.scenario = {"service_id": body.service_id, "service_name": s.name, "kind": body.kind,
                   "magnitude": body.magnitude, "duration": body.duration_slots, "slots_left": body.duration_slots}
    db.add(AuditLog(actor=admin.email, action="scenario_injected", target=body.service_id, details=body.model_dump()))
    db.commit()
    return st.scenario


@router.get("/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(min(max(limit, 1), 500))).all()
    return [{"id": r.id, "at": r.at, "actor": r.actor, "action": r.action, "target": r.target, "details": r.details}
            for r in rows]


@router.get("/reports/evaluation")
def evaluation(_: User = Depends(current_user)):
    """Offline evaluation results (cloudopt.eval / forecast / anomaly / rl), as produced by the
    training pipeline. Missing reports are returned as null so the UI can say what is pending."""
    def load(name):
        p = REPORTS / name
        return json.loads(p.read_text()) if p.exists() else None

    return {
        "comparison": load("comparison_delay0.json"),
        "comparison_delay1": load("comparison_delay1.json"),
        "frontier": load("frontier.json"),
        "forecast": load("forecast_xgb.json"),
        "forecast_lstm": load("forecast_lstm.json"),
        "anomaly": load("anomaly_eval.json"),
        "rl": load("rl_eval.json"),
        "explainability": load("explainability_eval.json"),
    }


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
def get_tenant(tenant_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    check_tenant_access(user, tenant_id)
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(404, detail="Tenant not found")
    return t
