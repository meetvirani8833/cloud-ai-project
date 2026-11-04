"""The closed loop, one tick per replayed 5-minute slot.

    observe slot t  ->  record usage/cost/SLA at current replicas
                    ->  detect anomalies (residual vs. the forecast issued at t-1)
                    ->  forecast t+1..t+12 with quantiles
                    ->  decide replicas for t+1 (forecast-driven, anomaly-aware, look-ahead scale-in)
                    ->  autopilot applies changes within its guardrail; larger ones become
                        pending recommendations with a cost delta, risk and explanation
                    ->  admins approve/reject from the dashboard (applied at the next tick)

Only one engine may run per database (run the API with a single worker).
"""

from __future__ import annotations

import logging
import math
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from cloudopt.forecast.xgb import quantile_index

from app.db import SessionLocal
from app.models import (Anomaly, AuditLog, ForecastPoint, PricingModel, Recommendation, ReplayState, Service,
                        Tenant, UsageMetric)
from app.services import explain
from app.services.runtime import Runtime, exceed_probability

log = logging.getLogger(__name__)

SLOT = timedelta(minutes=5)
PROVISION_QUANTILE = 0.9  # forecast quantile the policy provisions for
SCALE_IN_PATIENCE = 3  # consecutive slots of lower demand before scaling in
LOOKAHEAD_HORIZONS = (3, 6)  # scale-in may not go below what these forecasts (15/30 min) need
PENDING_TTL_SLOTS = 6  # pending recommendations expire after 30 simulated minutes
REJECT_COOLDOWN_SLOTS = 12  # no repeat recommendation in the same direction for 1 hour
MIN_CHANGE_PCT = 10.0  # ignore tiny changes in "recommend" mode
HOURS_PER_MONTH = 730


def slot_time(epoch: datetime, slot: int) -> datetime:
    return epoch + slot * SLOT


def replica_hour_price(p: PricingModel, vcpu: float, mem_gb: float) -> float:
    return vcpu * p.vcpu_hour + mem_gb * p.gb_hour


class Engine:
    def __init__(self, runtime: Runtime):
        self.rt = runtime
        self.lock = threading.RLock()
        self.below_count: dict[str, int] = defaultdict(int)
        self.below_max: dict[str, int] = {}
        self.cooldown: dict[tuple[str, str], int] = {}  # (service, direction) -> until slot
        self.last_anomaly: dict[str, dict] = {}

    # ------------------------------------------------------------------ helpers
    def _state(self, db: Session) -> ReplayState:
        return db.get(ReplayState, 1)

    def _multipliers(self, state: ReplayState, t: int) -> tuple[np.ndarray, set[str]]:
        """Synthetic scenario injected by an admin from the dashboard ("what if traffic spikes?")."""
        m = np.ones(self.rt.n)
        injected: set[str] = set()
        sc = state.scenario
        if sc and sc.get("slots_left", 0) > 0 and sc["service_id"] in self.rt.index:
            i = self.rt.index[sc["service_id"]]
            done = sc["duration"] - sc["slots_left"]
            if sc["kind"] == "spike":
                m[i] = sc["magnitude"]
            elif sc["kind"] == "drop":
                m[i] = 1.0 / sc["magnitude"]
            else:  # surge: ramps up over the first half, then holds
                ramp = min(1.0, (done + 1) / max(sc["duration"] / 2, 1))
                m[i] = 1.0 + (sc["magnitude"] - 1.0) * ramp
            injected.add(sc["service_id"])
            state.scenario = {**sc, "slots_left": sc["slots_left"] - 1}
        return m, injected

    # ------------------------------------------------------------------ main tick
    def tick(self) -> int | None:
        """Process one slot. Returns the slot processed, or None if paused/finished."""
        with self.lock, SessionLocal() as db:
            state = self._state(db)
            if state is None or not state.running:
                return None
            t = state.current_slot
            if t >= self.rt.T - 1:
                self._finish(db, state)
                return None
            epoch = state.epoch
            now = slot_time(epoch, t)
            services = {s.id: s for s in db.scalars(select(Service)).all()}
            tenants = {x.id: x for x in db.scalars(select(Tenant)).all()}
            prices = {p.key: p for p in db.scalars(select(PricingModel)).all()}

            mult, injected = self._multipliers(state, t)
            self.rt.observe(t, mult)

            # 1. usage at current replicas
            for sid, i in self.rt.index.items():
                s = services[sid]
                ten = tenants[s.tenant_id]
                price = replica_hour_price(prices[ten.pricing_key], s.replica_vcpu, s.replica_mem_gb)
                cap = s.replicas * s.replica_vcpu
                peak = float(self.rt.obs_peak[i, t])
                db.add(UsageMetric(
                    service_id=sid, time=now, slot=t,
                    rps=float(self.rt.rps[i, t] * mult[i]),
                    concurrency=float(self.rt.obs_mean[i, t]), concurrency_peak=peak,
                    mem_mb=float(self.rt.mem_mb[i, t]), replicas=s.replicas,
                    cpu_util=float(self.rt.obs_mean[i, t] / cap), cpu_util_peak=peak / cap,
                    cost_usd=s.replicas * price * SLOT.total_seconds() / 3600,
                    baseline_cost_usd=s.baseline_replicas * price * SLOT.total_seconds() / 3600,
                    sla_violation=peak / cap > ten.sla_util, injected=sid in injected,
                ))

            # 2. anomalies on slot t
            flag, kind, exp50, exp95 = self.rt.anomalies(t)
            self.last_anomaly = {}
            for sid, i in self.rt.index.items():
                if flag[i]:
                    a = Anomaly(service_id=sid, tenant_id=services[sid].tenant_id, time=now,
                                kind="spike" if kind[i] > 0 else "drop",
                                score=float(self.rt.obs_peak[i, t] / max(exp95[i], 1e-6)),
                                observed=float(self.rt.obs_peak[i, t]), expected=float(exp50[i]),
                                expected_high=float(exp95[i]), injected=sid in injected)
                    db.add(a)
                    self.last_anomaly[sid] = {"kind": a.kind, "observed": a.observed, "expected": a.expected}

            # 3. forecasts for t+h
            fc = {h: self.rt.forecast(t, h) for h in self.rt.horizons}
            self.rt.record_live(fc.get(1), t, flag & (kind > 0))
            for h, f in fc.items():
                if f is None:
                    continue
                target = slot_time(epoch, f.target_slot)
                for sid, i in self.rt.index.items():
                    db.merge(ForecastPoint(
                        service_id=sid, target_time=target, horizon=h, issued_slot=t,
                        p10=float(f.q[quantile_index(0.1), i]), p50=float(f.q[quantile_index(0.5), i]),
                        p90=float(f.q[quantile_index(0.9), i]), p95=float(f.q[quantile_index(0.95), i]),
                    ))

            # 4. expire stale pending recommendations
            db.execute(update(Recommendation)
                       .where(Recommendation.status == "pending", Recommendation.expires_slot <= t)
                       .values(status="expired"))

            # 5. decide replicas for t+1
            new_pending: list[Recommendation] = []
            if fc.get(1) is not None:
                for sid, i in self.rt.index.items():
                    s = services[sid]
                    if s.mode == "manual":
                        continue
                    rec = self._decide(db, s, tenants[s.tenant_id], prices[tenants[s.tenant_id].pricing_key], i, t, now, fc)
                    if rec is not None and rec.status == "pending":
                        new_pending.append(rec)

            state.current_slot = t + 1
            db.commit()

        # Explanations can call an external API: do it outside the tick's lock and transaction.
        for rec_id in [r.id for r in new_pending]:
            self._explain_async(rec_id)
        return t

    # ------------------------------------------------------------------ decision policy
    def _decide(self, db: Session, s: Service, ten: Tenant, pricing: PricingModel, i: int, t: int,
                now: datetime, fc: dict) -> Recommendation | None:
        qi = quantile_index(PROVISION_QUANTILE)
        if s.decision_engine == "rl":
            rl_target = self.rt.rl_action(i, t + 1, s.replicas)
            if rl_target is not None:
                return self._apply_target(db, s, ten, pricing, i, t, now, fc,
                                          int(min(max(rl_target, s.min_replicas), s.max_replicas)), "rl_ppo")
        need_next = float(fc[1].q[qi, i])
        anomaly = self.last_anomaly.get(s.id)
        observed_now = float(self.rt.obs_peak[i, t])
        if anomaly and anomaly["kind"] == "spike":
            # Guardrail: during an unexplained spike assume it persists for one more slot.
            need_next = max(need_next, observed_now)
        # Scale-in floor: never release capacity the 15/30-minute forecasts say will be needed.
        hold_need = max([need_next] + [float(fc[h].q[qi, i]) for h in LOOKAHEAD_HORIZONS if fc.get(h) is not None])

        def replicas(need: float) -> int:
            r = math.ceil(need / (s.replica_vcpu * ten.target_util) - 1e-9)
            return int(min(max(r, s.min_replicas), s.max_replicas))

        up, floor = replicas(need_next), replicas(hold_need)
        current = s.replicas

        # Scale out immediately; scale in only to the look-ahead floor and after sustained lower demand.
        if up > current:
            desired = up
            self.below_count[s.id] = 0
            self.below_max.pop(s.id, None)
        elif floor < current:
            self.below_count[s.id] += 1
            self.below_max[s.id] = max(self.below_max.get(s.id, 0), floor)
            if self.below_count[s.id] < SCALE_IN_PATIENCE:
                return None
            desired = self.below_max.pop(s.id)
            self.below_count[s.id] = 0
        else:
            self.below_count[s.id] = 0
            self.below_max.pop(s.id, None)
            return None

        return self._apply_target(db, s, ten, pricing, i, t, now, fc, desired, "forecast_xgb_p90")

    def _apply_target(self, db: Session, s: Service, ten: Tenant, pricing: PricingModel, i: int, t: int,
                      now: datetime, fc: dict, desired: int, policy: str) -> Recommendation | None:
        """Route a target replica count through autopilot / approval, whichever engine chose it."""
        current = s.replicas
        if desired == current:
            return None
        anomaly = self.last_anomaly.get(s.id)
        direction = "scale_out" if desired > current else "scale_in"
        pending = db.scalar(select(Recommendation).where(Recommendation.service_id == s.id,
                                                         Recommendation.status == "pending"))
        change_pct = 100 * abs(desired - current) / current

        if s.mode == "autopilot":
            step = max(1, math.floor(current * s.autopilot_max_step_pct / 100))
            capped = current + step if direction == "scale_out" else current - step
            capped = desired if abs(desired - current) <= step else capped
            if capped != desired:
                # Apply the guarded step now; the remainder needs a human.
                self._record(db, s, ten, pricing, i, t, now, fc, capped, "auto_applied", anomaly, policy=policy)
                s.replicas = capped
                return self._propose(db, s, ten, pricing, i, t, now, fc, desired, direction, pending, anomaly,
                                     base_replicas=capped, policy=policy)
            if pending is not None:
                pending.status = "superseded"
            self._record(db, s, ten, pricing, i, t, now, fc, desired, "auto_applied", anomaly, policy=policy)
            s.replicas = desired
            return None

        # recommend mode: everything needs approval, but skip trivial changes.
        if change_pct < MIN_CHANGE_PCT:
            return None
        return self._propose(db, s, ten, pricing, i, t, now, fc, desired, direction, pending, anomaly, policy=policy)

    def _propose(self, db, s, ten, pricing, i, t, now, fc, desired, direction, pending, anomaly,
                 base_replicas: int | None = None, policy: str = "forecast_xgb_p90"):
        if self.cooldown.get((s.id, direction), -1) > t:
            return None
        if pending is not None:
            same_dir = (pending.recommended_replicas > pending.current_replicas) == (direction == "scale_out")
            close = abs(pending.recommended_replicas - desired) <= max(1, 0.1 * pending.recommended_replicas)
            if same_dir and close:
                return None  # existing request still valid; don't spam the approver
            pending.status = "superseded"
        return self._record(db, s, ten, pricing, i, t, now, fc, desired, "pending", anomaly, base_replicas, policy)

    def _record(self, db, s, ten, pricing, i, t, now, fc, target, status, anomaly, base_replicas=None,
                policy: str = "forecast_xgb_p90"):
        current = base_replicas if base_replicas is not None else s.replicas
        price = replica_hour_price(pricing, s.replica_vcpu, s.replica_mem_gb)
        facts = self._facts(s, ten, pricing, i, t, fc, current, target, price, anomaly)
        rec = Recommendation(
            service_id=s.id, tenant_id=s.tenant_id, created_at=now, created_slot=t,
            expires_slot=t + PENDING_TTL_SLOTS,
            action="scale_out" if target > current else "scale_in",
            policy=policy + ("+anomaly_guard" if anomaly and policy.startswith("forecast") else ""),
            current_replicas=current, recommended_replicas=target,
            hourly_cost_current=current * price, hourly_cost_recommended=target * price,
            risk_current=facts["risk_current"], risk_recommended=facts["risk_recommended"],
            confidence=facts["confidence"], facts=facts, status=status,
        )
        if status == "auto_applied":
            rec.explanation, rec.explanation_source = explain.template_explanation(facts, status), "template"
            rec.decided_by, rec.decided_at = "autopilot", datetime.now(timezone.utc)
        else:
            rec.explanation, rec.explanation_source = explain.template_explanation(facts, status), "template"
        db.add(rec)
        db.flush()
        return rec

    def _facts(self, s, ten, pricing, i, t, fc, current, target, price, anomaly) -> dict:
        hs = [h for h in self.rt.horizons if fc.get(h) is not None]
        h_last = max(hs)
        p50 = max(float(fc[h].q[quantile_index(0.5), i]) for h in hs)
        p90 = max(float(fc[h].q[quantile_index(0.9), i]) for h in hs)

        def risk(replicas: int) -> float:
            thr = np.array([replicas * s.replica_vcpu * ten.sla_util])
            return float(max(exceed_probability(fc[h].q[:, i:i + 1], thr)[0] for h in hs))

        q1 = fc[1].q[:, i]
        width = (q1[quantile_index(0.9)] - q1[quantile_index(0.1)]) / max(q1[quantile_index(0.5)], 1e-6)
        confidence = float(np.clip(1.0 / (1.0 + width), 0.2, 0.98))
        return {
            "service_name": s.name,
            "tenant_name": ten.name,
            "current_replicas": int(current),
            "recommended_replicas": int(target),
            "replica_size": f"{s.replica_vcpu:g} vCPU / {s.replica_mem_gb:g} GB",
            "observed_peak_now": round(float(self.rt.obs_peak[i, t]), 2),
            "forecast_p50_next_hour": round(p50, 2),
            "forecast_p90_next_hour": round(p90, 2),
            "horizon_minutes": h_last * 5,
            "predicted_util_if_unchanged": round(p90 / (current * s.replica_vcpu), 3),
            "predicted_util_if_changed": round(p90 / (target * s.replica_vcpu), 3),
            "target_util": ten.target_util,
            "sla_util": ten.sla_util,
            "risk_current": round(risk(current), 3),
            "risk_recommended": round(risk(target), 3),
            "hourly_cost_delta": round((target - current) * price, 4),
            "monthly_cost_delta": round((target - current) * price * HOURS_PER_MONTH, 2),
            "pricing_label": pricing.label,
            "confidence": round(confidence, 2),
            "anomaly": anomaly,
            "units": "demand in concurrently busy workers (1 worker = 1 vCPU)",
        }

    def _explain_async(self, rec_id: int) -> None:
        def work():
            with SessionLocal() as db:
                rec = db.get(Recommendation, rec_id)
                if rec is None:
                    return
                text, source = explain.generate(rec.facts, rec.status)
                rec.explanation, rec.explanation_source = text, source
                db.commit()

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ admin actions
    def decide_recommendation(self, db: Session, rec: Recommendation, approve: bool, actor: str, note: str | None):
        """Approve applies the target at the next tick. Returns (ok, message)."""
        with self.lock:
            db.refresh(rec)
            if rec.status != "pending":
                return False, f"This recommendation is already {rec.status.replace('_', ' ')}"
            rec.status = "approved" if approve else "rejected"
            rec.decided_by, rec.decided_at, rec.decision_note = actor, datetime.now(timezone.utc), note
            rec.explanation = explain.with_status(rec.explanation, rec.status)
            if approve:
                svc = db.get(Service, rec.service_id)
                svc.replicas = int(min(max(rec.recommended_replicas, svc.min_replicas), svc.max_replicas))
                # The approved level is a floor for scale-in hysteresis to respect.
                self.below_count[svc.id] = 0
            else:
                direction = rec.action
                state = self._state(db)
                self.cooldown[(rec.service_id, direction)] = state.current_slot + REJECT_COOLDOWN_SLOTS
            db.add(AuditLog(actor=actor, action="recommendation_" + rec.status, target=str(rec.id),
                            details={"service": rec.service_id, "to": rec.recommended_replicas, "note": note}))
            db.commit()
            return True, "ok"

    def _finish(self, db: Session, state: ReplayState) -> None:
        if state.auto_loop:
            log.info("Replay reached the end of the trace; restarting from the onboarding day")
            reset_replay(db, self, state)
        else:
            state.running = False
            state.finished = True
            db.commit()


def reset_replay(db: Session, engine: Engine | None, state: ReplayState) -> None:
    """Rewind to the onboarding slot: drop replayed rows, restore pre-onboarding provisioning."""
    start = state.start_slot
    start_time = slot_time(state.epoch, start)
    db.execute(delete(UsageMetric).where(UsageMetric.slot >= start))
    db.execute(delete(ForecastPoint))
    db.execute(delete(Anomaly).where(Anomaly.time >= start_time))
    db.execute(delete(Recommendation))
    for s in db.scalars(select(Service)).all():
        s.replicas = s.baseline_replicas
    state.current_slot = start
    state.scenario = None
    state.finished = False
    state.running = True
    db.add(AuditLog(actor="system", action="replay_reset"))
    db.commit()
    if engine is not None:
        engine.rt.reset_observations(start)
        engine.below_count.clear()
        engine.below_max.clear()
        engine.cooldown.clear()
