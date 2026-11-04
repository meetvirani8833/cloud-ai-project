"""Request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["admin", "viewer"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    role: Role
    tenant_id: str | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=72)
    role: Role = "viewer"
    tenant_id: str | None = None


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    tenant_id: str | None = None
    clear_tenant: bool = False
    is_active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


class PricingOut(ORM):
    key: str
    label: str
    vcpu_hour: float
    gb_hour: float
    is_custom: bool


class PricingUpsert(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    vcpu_hour: float = Field(gt=0, le=10)
    gb_hour: float = Field(ge=0, le=10)


class ServiceOut(ORM):
    id: str
    tenant_id: str
    name: str
    trigger: str
    replica_vcpu: float
    replica_mem_gb: float
    replicas: int
    baseline_replicas: int
    min_replicas: int
    max_replicas: int
    mode: Literal["recommend", "autopilot", "manual"]
    autopilot_max_step_pct: float
    decision_engine: Literal["forecast", "rl"]


class ServiceUpdate(BaseModel):
    mode: Literal["recommend", "autopilot", "manual"] | None = None
    decision_engine: Literal["forecast", "rl"] | None = None
    min_replicas: int | None = Field(default=None, ge=1, le=10_000)
    max_replicas: int | None = Field(default=None, ge=1, le=10_000)
    autopilot_max_step_pct: float | None = Field(default=None, gt=0, le=500)


class TenantOut(ORM):
    id: str
    name: str
    pricing_key: str
    sla_util: float
    target_util: float


class TenantUpdate(BaseModel):
    pricing_key: str | None = None
    sla_util: float | None = Field(default=None, gt=0.3, le=1.0)
    target_util: float | None = Field(default=None, gt=0.2, lt=1.0)


class RecommendationOut(ORM):
    id: int
    service_id: str
    tenant_id: str
    created_at: datetime
    created_slot: int
    expires_slot: int
    action: str
    policy: str
    current_replicas: int
    recommended_replicas: int
    hourly_cost_current: float
    hourly_cost_recommended: float
    risk_current: float
    risk_recommended: float
    confidence: float
    facts: dict
    explanation: str
    explanation_source: str
    status: str
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None


class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class AnomalyOut(ORM):
    id: int
    service_id: str
    tenant_id: str
    time: datetime
    kind: str
    score: float
    observed: float
    expected: float
    expected_high: float
    injected: bool


class ScenarioIn(BaseModel):
    service_id: str
    kind: Literal["spike", "drop", "surge"]
    magnitude: float = Field(default=4.0, gt=0, le=50)
    duration_slots: int = Field(default=6, ge=1, le=288)


class ReplayControl(BaseModel):
    running: bool | None = None
    seconds_per_slot: float | None = Field(default=None, ge=0.5, le=300)
