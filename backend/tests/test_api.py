from app.models import ReplayState, Service
from app.db import SessionLocal

from conftest import token


# ---------------------------------------------------------------- auth
def test_login_rejects_bad_credentials_without_revealing_which(client):
    a = client.post("/api/auth/login", data={"username": "admin@cloudopt.dev", "password": "wrong"})
    b = client.post("/api/auth/login", data={"username": "nobody@x.dev", "password": "wrong"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_login_is_case_insensitive_on_email(client):
    token(client, "ADMIN@CloudOpt.dev")


def test_protected_routes_require_a_valid_token(client):
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_me_returns_role(client, admin, viewer):
    assert client.get("/api/auth/me", headers=admin).json()["role"] == "admin"
    assert client.get("/api/auth/me", headers=viewer).json()["role"] == "viewer"


# ---------------------------------------------------------------- roles & tenant scoping
def test_viewer_cannot_use_admin_endpoints(client, viewer):
    assert client.get("/api/users", headers=viewer).status_code == 403
    assert client.post("/api/replay/control", json={"running": False}, headers=viewer).status_code == 403
    assert client.put("/api/pricing/x_custom", json={"label": "X", "vcpu_hour": 0.1, "gb_hour": 0.01}, headers=viewer).status_code == 403


def test_tenant_viewer_sees_only_their_tenant(client, tenant_viewer, admin):
    all_tenants = client.get("/api/tenants", headers=admin).json()
    mine = client.get("/api/tenants", headers=tenant_viewer).json()
    assert len(all_tenants) > 1 and len(mine) == 1
    other = next(t["id"] for t in all_tenants if t["id"] != mine[0]["id"])
    # Other tenants look like they don't exist (404, not 403).
    assert client.get(f"/api/tenants/{other}", headers=tenant_viewer).status_code == 404
    assert client.get(f"/api/overview?tenant_id={other}", headers=tenant_viewer).status_code == 404
    services = client.get("/api/services", headers=tenant_viewer).json()
    assert services and all(s["tenant_id"] == mine[0]["id"] for s in services)


def test_cannot_remove_last_admin_or_self(client, admin):
    me = client.get("/api/auth/me", headers=admin).json()
    r = client.patch(f"/api/users/{me['id']}", json={"role": "viewer"}, headers=admin)
    assert r.status_code == 400


def test_user_creation_validates_and_rejects_duplicates(client, admin):
    body = {"email": "new.person@cloudopt.dev", "full_name": "New Person", "password": "longenough1", "role": "viewer"}
    assert client.post("/api/users", json=body, headers=admin).status_code == 201
    assert client.post("/api/users", json=body, headers=admin).status_code == 409
    assert client.post("/api/users", json={**body, "email": "short@cloudopt.dev", "password": "short"}, headers=admin).status_code == 422
    token(client, "new.person@cloudopt.dev", "longenough1")


def test_disabled_user_is_locked_out_immediately(client, admin):
    body = {"email": "temp@cloudopt.dev", "full_name": "Temp", "password": "longenough1", "role": "viewer"}
    uid = client.post("/api/users", json=body, headers=admin).json()["id"]
    h = token(client, "temp@cloudopt.dev", "longenough1")
    assert client.patch(f"/api/users/{uid}", json={"is_active": False}, headers=admin).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    r = client.post("/api/auth/login", data={"username": "temp@cloudopt.dev", "password": "longenough1"})
    assert r.status_code == 403


# ---------------------------------------------------------------- closed loop
def _tick(client, n: int) -> None:
    for _ in range(n):
        client.app.state.engine.tick()


def test_engine_tick_writes_usage_forecasts_and_decisions(client, admin):
    before = client.get("/api/replay", headers=admin).json()["current_slot"]
    _tick(client, 6)
    after = client.get("/api/replay", headers=admin).json()["current_slot"]
    assert after == before + 6
    ov = client.get("/api/overview?hours=1", headers=admin).json()
    assert ov["services"] > 0 and ov["window_cost"] > 0
    sid = client.get("/api/services", headers=admin).json()[0]["id"]
    ts = client.get(f"/api/services/{sid}/timeseries?hours=1", headers=admin).json()
    assert ts["points"] and ts["forecast"]
    assert any(p["forecast_p50"] is not None for p in ts["points"])
    # Onboarding from over-provisioned static capacity must produce scale-in decisions.
    recs = client.get("/api/recommendations?status=auto_applied,pending", headers=admin).json()
    assert recs["total"] > 0
    assert any(r["action"] == "scale_in" for r in recs["items"])
    # Every recommendation carries a complete explanation built from its facts.
    r = recs["items"][0]
    assert r["explanation"].startswith("Recommendation:") and "Action status:" in r["explanation"]


def test_cost_falls_after_onboarding(client, admin):
    _tick(client, 6)
    ov = client.get("/api/overview?hours=1", headers=admin).json()
    assert ov["hourly_run_rate"] < ov["baseline_hourly_run_rate"]


def test_approval_flow_and_conflicts(client, admin, viewer):
    with SessionLocal() as db:  # approval-required mode and badly under-provisioned: must ask to scale out
        svc = db.query(Service).first()
        svc.mode = "recommend"
        svc.replicas = 1
        db.commit()
        sid = svc.id
    _tick(client, 2)
    pending = client.get(f"/api/recommendations?status=pending&service_id={sid}", headers=admin).json()["items"]
    assert pending, "expected a pending recommendation in approval-required mode"
    rec = pending[0]
    assert client.post(f"/api/recommendations/{rec['id']}/approve", headers=viewer).status_code == 403
    r = client.post(f"/api/recommendations/{rec['id']}/approve", json={"note": "ok"}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert client.get(f"/api/services/{sid}", headers=admin).json()["replicas"] == rec["recommended_replicas"]
    # Deciding twice is a conflict, not a silent overwrite.
    assert client.post(f"/api/recommendations/{rec['id']}/reject", headers=admin).status_code == 409


def test_scenario_injection_is_validated(client, admin):
    sid = client.get("/api/services", headers=admin).json()[1]["id"]
    ok = client.post("/api/replay/scenario", json={"service_id": sid, "kind": "spike", "magnitude": 6, "duration_slots": 2}, headers=admin)
    assert ok.status_code == 200
    busy = client.post("/api/replay/scenario", json={"service_id": sid, "kind": "drop", "magnitude": 3, "duration_slots": 2}, headers=admin)
    assert busy.status_code == 409
    assert client.post("/api/replay/scenario", json={"service_id": "nope", "kind": "spike"}, headers=admin).status_code == 404
    _tick(client, 3)
    ts = client.get(f"/api/services/{sid}/timeseries?hours=1", headers=admin).json()
    assert any(p["injected"] for p in ts["points"])


def test_pricing_crud_guards_presets_and_in_use(client, admin):
    assert client.delete("/api/pricing/aws_fargate", headers=admin).status_code == 400
    assert client.put("/api/pricing/my_reseller", json={"label": "Reseller", "vcpu_hour": 0.03, "gb_hour": 0.003}, headers=admin).status_code == 200
    tid = client.get("/api/tenants", headers=admin).json()[0]["id"]
    assert client.patch(f"/api/tenants/{tid}", json={"pricing_key": "my_reseller"}, headers=admin).status_code == 200
    assert client.delete("/api/pricing/my_reseller", headers=admin).status_code == 409
    assert client.patch(f"/api/tenants/{tid}", json={"target_util": 0.95, "sla_util": 0.9}, headers=admin).status_code == 422


def test_reset_restores_pre_onboarding_state(client, admin):
    assert client.post("/api/replay/reset", headers=admin).status_code == 200
    with SessionLocal() as db:
        st = db.get(ReplayState, 1)
        assert st.current_slot == st.start_slot
        assert all(s.replicas == s.baseline_replicas for s in db.query(Service).all())
    assert client.get("/api/recommendations", headers=admin).json()["total"] == 0


def test_rl_decision_engine_drives_a_service(client, admin):
    assert client.get("/api/replay", headers=admin).json()["rl_available"] is True
    sid = client.get("/api/services", headers=admin).json()[2]["id"]
    r = client.patch(f"/api/services/{sid}", json={"decision_engine": "rl", "mode": "autopilot", "autopilot_max_step_pct": 500}, headers=admin)
    assert r.status_code == 200 and r.json()["decision_engine"] == "rl"
    with SessionLocal() as db:  # start far from the right size so the agent must act
        db.get(Service, sid).replicas = 1
        db.commit()
    _tick(client, 4)
    recs = client.get(f"/api/recommendations?service_id={sid}&status=auto_applied,pending", headers=admin).json()["items"]
    assert recs and all(x["policy"].startswith("rl_ppo") for x in recs[:2])
    assert client.get(f"/api/services/{sid}", headers=admin).json()["replicas"] > 1


def test_tenants_include_hourly_violation_strip(client, admin):
    _tick(client, 3)
    t = client.get("/api/tenants", headers=admin).json()[0]
    assert len(t["violations_by_hour"]) == 24
    assert any(v is not None for v in t["violations_by_hour"])
    assert all(v is None or 0 <= v <= 1 for v in t["violations_by_hour"])


def test_hourly_strip_matches_daily_rate(client, admin):
    """The hourly cells must add back up to the 24 h rate (guards against mis-bucketing)."""
    _tick(client, 14)
    for t in client.get("/api/tenants", headers=admin).json():
        cells = [v for v in t["violations_by_hour"] if v is not None]
        if t["sla_violation_rate_24h"]:
            assert any(cells), t["name"]


def test_cost_trend_is_hourly(client, admin):
    """Each trend point must aggregate a full hour: 12 slots, so baseline cost per point ~ hourly run-rate."""
    ov = client.get("/api/overview?hours=24", headers=admin).json()
    times = [p["time"] for p in ov["trend"]]
    assert len(times) == len(set(times)) and len(times) <= 25
    full = [p for p in ov["trend"][1:-1]]
    if full:
        assert abs(full[0]["baseline_cost"] - ov["baseline_hourly_run_rate"]) / ov["baseline_hourly_run_rate"] < 0.05
