"""ORM models.

Relational core (users, tenants, services, pricing, recommendations) plus time-series tables
(usage_metrics, forecast_points) that become TimescaleDB hypertables on Postgres.
Times are *simulated* trace time (see services/clock.py), stored as UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin | viewer
    # Viewers may be scoped to one tenant (a customer's own login); None = all tenants.
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PricingModel(Base):
    __tablename__ = "pricing_models"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    vcpu_hour: Mapped[float] = mapped_column(Float)
    gb_hour: Mapped[float] = mapped_column(Float)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source_hash: Mapped[str] = mapped_column(String(80))  # HashOwner in the Azure trace
    pricing_key: Mapped[str] = mapped_column(ForeignKey("pricing_models.key"), default="aws_fargate")
    sla_util: Mapped[float] = mapped_column(Float, default=0.9)
    target_util: Mapped[float] = mapped_column(Float, default=0.7)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    services: Mapped[list["Service"]] = relationship(back_populates="tenant", order_by="Service.id")
    pricing: Mapped[PricingModel] = relationship()


class Service(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # app_id from preprocessing
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    trigger: Mapped[str] = mapped_column(String(20))
    source_hash: Mapped[str] = mapped_column(String(80))  # HashApp
    replica_vcpu: Mapped[float] = mapped_column(Float)
    replica_mem_gb: Mapped[float] = mapped_column(Float)
    replicas: Mapped[int] = mapped_column(Integer)
    # Provisioning the customer had before onboarding (static, "just to be safe").
    baseline_replicas: Mapped[int] = mapped_column(Integer)
    min_replicas: Mapped[int] = mapped_column(Integer, default=1)
    max_replicas: Mapped[int] = mapped_column(Integer, default=10_000)
    # recommend: every change needs approval. autopilot: changes within the guardrail apply
    # automatically, larger ones need approval. manual: no recommendations.
    mode: Mapped[str] = mapped_column(String(12), default="autopilot")
    # forecast: CloudOpt forecast policy. rl: PPO agent (falls back to forecast if no model is bundled).
    decision_engine: Mapped[str] = mapped_column(String(12), default="forecast")
    autopilot_max_step_pct: Mapped[float] = mapped_column(Float, default=50.0)

    tenant: Mapped[Tenant] = relationship(back_populates="services")


class UsageMetric(Base):
    __tablename__ = "usage_metrics"

    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    slot: Mapped[int] = mapped_column(Integer)
    rps: Mapped[float] = mapped_column(Float)
    concurrency: Mapped[float] = mapped_column(Float)
    concurrency_peak: Mapped[float] = mapped_column(Float)
    mem_mb: Mapped[float] = mapped_column(Float)
    replicas: Mapped[int] = mapped_column(Integer)
    cpu_util: Mapped[float] = mapped_column(Float)
    cpu_util_peak: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float)
    baseline_cost_usd: Mapped[float] = mapped_column(Float)  # same slot under static provisioning
    sla_violation: Mapped[bool] = mapped_column(Boolean)
    injected: Mapped[bool] = mapped_column(Boolean, default=False)  # synthetic scenario applied


class ForecastPoint(Base):
    __tablename__ = "forecast_points"

    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), primary_key=True)
    target_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    horizon: Mapped[int] = mapped_column(Integer, primary_key=True)  # slots ahead when issued
    issued_slot: Mapped[int] = mapped_column(Integer)
    p10: Mapped[float] = mapped_column(Float)
    p50: Mapped[float] = mapped_column(Float)
    p90: Mapped[float] = mapped_column(Float)
    p95: Mapped[float] = mapped_column(Float)


class Anomaly(Base):
    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # spike | drop
    score: Mapped[float] = mapped_column(Float)
    observed: Mapped[float] = mapped_column(Float)
    expected: Mapped[float] = mapped_column(Float)
    expected_high: Mapped[float] = mapped_column(Float)
    injected: Mapped[bool] = mapped_column(Boolean, default=False)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # simulated time
    created_slot: Mapped[int] = mapped_column(Integer)
    expires_slot: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))  # scale_out | scale_in
    policy: Mapped[str] = mapped_column(String(30))  # which engine proposed it
    current_replicas: Mapped[int] = mapped_column(Integer)
    recommended_replicas: Mapped[int] = mapped_column(Integer)
    hourly_cost_current: Mapped[float] = mapped_column(Float)
    hourly_cost_recommended: Mapped[float] = mapped_column(Float)
    risk_current: Mapped[float] = mapped_column(Float)  # estimated P(SLA violation) next hour
    risk_recommended: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    facts: Mapped[dict] = mapped_column(JSON)  # structured evidence the explanation must cite
    explanation: Mapped[str] = mapped_column(Text, default="")
    explanation_source: Mapped[str] = mapped_column(String(12), default="pending")  # openai | openai_retry | fallback | template
    status: Mapped[str] = mapped_column(String(14), default="pending", index=True)
    # pending | approved | rejected | expired | auto_applied | superseded
    decided_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str] = mapped_column(String(120), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class ReplayState(Base):
    __tablename__ = "replay_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    # Simulated wall-clock of trace slot 0 (a Monday; trace day 1 is a weekday-start).
    epoch: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_slot: Mapped[int] = mapped_column(Integer)  # onboarding: first slot the AI manages
    current_slot: Mapped[int] = mapped_column(Integer)  # next slot to process
    running: Mapped[bool] = mapped_column(Boolean, default=True)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_loop: Mapped[bool] = mapped_column(Boolean, default=True)
    seconds_per_slot: Mapped[float] = mapped_column(Float, default=5.0)
    # Pending synthetic scenario: {"service_id", "kind", "magnitude", "slots_left"}
    scenario: Mapped[dict | None] = mapped_column(JSON, nullable=True)
