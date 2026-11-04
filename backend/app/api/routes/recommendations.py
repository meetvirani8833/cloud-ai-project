"""Recommendation feed and the human-in-the-loop approve/reject actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import check_tenant_access, current_user, require_admin, visible_tenant_ids
from app.db import get_db
from app.models import Recommendation, User
from app.schemas import Decision, RecommendationOut

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

STATUSES = {"pending", "approved", "rejected", "expired", "auto_applied", "superseded"}


@router.get("")
def list_recommendations(
    status: str | None = Query(None, description="Comma-separated statuses"),
    tenant_id: str | None = None,
    service_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(Recommendation)
    scope = visible_tenant_ids(user)
    if scope is not None:
        stmt = stmt.where(Recommendation.tenant_id.in_(scope))
    if tenant_id:
        check_tenant_access(user, tenant_id)
        stmt = stmt.where(Recommendation.tenant_id == tenant_id)
    if service_id:
        stmt = stmt.where(Recommendation.service_id == service_id)
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        bad = wanted - STATUSES
        if bad:
            raise HTTPException(422, detail=f"Unknown status: {', '.join(sorted(bad))}")
        stmt = stmt.where(Recommendation.status.in_(wanted))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Recommendation.created_slot.desc(), Recommendation.id.desc())
                      .offset(offset).limit(limit)).all()
    return {"total": total, "items": [RecommendationOut.model_validate(r) for r in rows]}


def _get(db: Session, rec_id: int, user: User) -> Recommendation:
    rec = db.get(Recommendation, rec_id)
    if rec is None:
        raise HTTPException(404, detail="Recommendation not found")
    check_tenant_access(user, rec.tenant_id)
    return rec


@router.get("/{rec_id}", response_model=RecommendationOut)
def get_recommendation(rec_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _get(db, rec_id, user)


def _decide(request: Request, rec_id: int, body: Decision, approve: bool, db: Session, admin: User):
    rec = _get(db, rec_id, admin)
    ok, msg = request.app.state.engine.decide_recommendation(db, rec, approve, admin.email, body.note)
    if not ok:
        raise HTTPException(409, detail=msg)  # e.g. expired or superseded while the admin was reading it
    db.refresh(rec)
    return RecommendationOut.model_validate(rec)


@router.post("/{rec_id}/approve", response_model=RecommendationOut)
def approve(rec_id: int, request: Request, body: Decision | None = None, db: Session = Depends(get_db),
            admin: User = Depends(require_admin)):
    return _decide(request, rec_id, body or Decision(), True, db, admin)


@router.post("/{rec_id}/reject", response_model=RecommendationOut)
def reject(rec_id: int, request: Request, body: Decision | None = None, db: Session = Depends(get_db),
           admin: User = Depends(require_admin)):
    return _decide(request, rec_id, body or Decision(), False, db, admin)
