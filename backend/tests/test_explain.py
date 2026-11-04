import pytest

from app.services.explain import template_explanation
from app.services.explain_rubric import score

BASE = {
    "service_name": "checkout-api", "tenant_name": "Larkspur Logistics", "replica_size": "1 vCPU / 2 GB",
    "observed_peak_now": 3.2, "horizon_minutes": 60, "target_util": 0.7, "sla_util": 0.9,
    "pricing_label": "AWS Fargate (approx.)", "confidence": 0.81, "anomaly": None,
    "units": "demand in concurrently busy workers (1 worker = 1 vCPU)",
}


def facts(**kw):
    return {**BASE, **kw}


SCALE_OUT = facts(current_replicas=3, recommended_replicas=5, forecast_p50_next_hour=3.9, forecast_p90_next_hour=4.6,
                  predicted_util_if_unchanged=1.533, predicted_util_if_changed=0.92, risk_current=0.62, risk_recommended=0.08,
                  hourly_cost_delta=0.0987, monthly_cost_delta=72.04)
SCALE_IN = facts(current_replicas=12, recommended_replicas=6, forecast_p50_next_hour=2.1, forecast_p90_next_hour=2.8,
                 predicted_util_if_unchanged=0.233, predicted_util_if_changed=0.467, risk_current=0.0, risk_recommended=0.01,
                 hourly_cost_delta=-0.2961, monthly_cost_delta=-216.13, observed_peak_now=3.4)
WITH_ANOMALY = {**SCALE_OUT, "anomaly": {"kind": "spike", "observed": 9.4, "expected": 3.1}}


@pytest.mark.parametrize("f", [SCALE_OUT, SCALE_IN, WITH_ANOMALY], ids=["scale_out", "scale_in", "anomaly"])
def test_template_passes_rubric(f):
    text = template_explanation(f)
    s = score(text, f)
    assert s["passed"] == s["total"], (s, text)


def test_template_matches_brief_format():
    text = template_explanation(SCALE_OUT)
    assert text.splitlines()[0] == "Recommendation: Increase checkout-api replicas from 3 to 5."
    assert text.endswith("Action status: Awaiting approval.")


def test_rubric_catches_wrong_and_invented_numbers():
    text = template_explanation(SCALE_OUT).replace("from 3 to 5", "from 3 to 7").replace("62%", "41%")
    s = score(text, SCALE_OUT)
    assert not s["checks"]["replica_counts"]
    assert not s["checks"]["no_invented_numbers"]


def test_rubric_catches_missing_anomaly_reference():
    text = template_explanation(SCALE_OUT)  # generated without the anomaly
    assert not score(text, WITH_ANOMALY)["checks"]["anomaly_consistent"]


def test_rubric_catches_false_threshold_claim():
    f = {**SCALE_OUT, "predicted_util_if_unchanged": 0.81}
    text = template_explanation(f)
    assert "above the 70% target" in text and score(text, f)["checks"]["threshold_claims_true"]
    bad = text.replace("above the 70% target utilisation", "above the 90% SLA threshold")
    assert not score(bad, f)["checks"]["threshold_claims_true"]


def test_rounded_numbers_are_not_flagged_as_invented():
    f = {**WITH_ANOMALY, "anomaly": {"kind": "spike", "observed": 9.36, "expected": 3.14}}
    assert score(template_explanation(f), f)["checks"]["no_invented_numbers"]


def test_negated_anomaly_mention_is_not_a_claim():
    text = template_explanation(SCALE_OUT).replace("Reason: ", "Reason: No anomaly was detected; ")
    assert score(text, SCALE_OUT)["checks"]["anomaly_consistent"]
    assert not score(text.replace("No anomaly was detected", "An anomaly was detected"), SCALE_OUT)["checks"]["anomaly_consistent"]


def test_status_line_is_set_by_the_system():
    from app.services.explain import with_status
    text = template_explanation(SCALE_OUT).replace("Action status: Awaiting approval.", "Action status: pending")
    assert with_status(text, "approved").endswith("Action status: Approved.")
    assert with_status("Recommendation: x", "pending").endswith("Action status: Awaiting approval.")
