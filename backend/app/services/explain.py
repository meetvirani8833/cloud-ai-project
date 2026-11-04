"""Natural-language justification for scaling recommendations.

The recommendation engine produces a structured ``facts`` dict (forecast, capacity, risk,
cost, anomaly). The explanation is generated *only* from those facts: by the OpenAI API when
a key is configured, otherwise by a deterministic template. The template output is also the
fallback whenever the API errors or times out, so the live demo never depends on the network.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque

from app.config import get_settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the explanation layer of a cloud cost-optimisation product used by small SaaS teams without a FinOps engineer.
Write the recommendation in exactly this format: plain text, six lines, no markdown, nothing else.
Recommendation: <Increase|Reduce> <service_name> replicas from <current_replicas> to <recommended_replicas>.
Reason: <1-3 sentences in plain English, e.g. "The median forecast for the next hour is 2.4 busy workers (90th percentile 3.5) against 2.4 now; with 32 replicas peak utilisation would be only 22%, and 12 replicas keep it at 58%, below the 70% target." Always state both forecast numbers and the current demand; mention an anomaly only if the facts contain one.>
Estimated cost impact: <+$X.XX or -$X.XX>/hour (<+$X.XX or -$X.XX>/month at <pricing_label>).
Estimated risk: SLA-violation risk <risk_current as a whole percent>% -> <risk_recommended as a whole percent>% over the next hour.
Confidence: <High if >= 0.75, Medium if >= 0.5, else Low> (<confidence with 2 decimals>).
Action status: <status>.

Rules:
- Write for a busy engineer: plain English, never the JSON field names (no underscores).
- Use only numbers present in the facts JSON. Never invent or derive new metrics (no capacity counts, no differences).
- Utilisation and risk values in the facts are fractions: write them as whole percentages (0.92 -> 92%).
- Only say utilisation is "above" or "below" a threshold if that is numerically true. Thresholds: target_util and sla_util.
- Money to cents with the sign and $ exactly as shown above."""


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _money(x: float) -> str:
    sign = "+" if x > 0 else "-" if x < 0 else "±"
    return f"{sign}${abs(x):,.2f}"


def confidence_label(c: float) -> str:
    return "High" if c >= 0.75 else "Medium" if c >= 0.5 else "Low"


STATUS_TEXT = {
    "pending": "Awaiting approval",
    "auto_applied": "Applied automatically (within autopilot guardrail)",
    "approved": "Approved",
    "rejected": "Rejected",
    "expired": "Expired (forecast window passed)",
    "superseded": "Superseded by a newer recommendation",
}


def template_explanation(f: dict, status: str = "pending") -> str:
    verb = "Increase" if f["recommended_replicas"] > f["current_replicas"] else "Reduce"
    reason_parts = []
    if f.get("anomaly"):
        a = f["anomaly"]
        reason_parts.append(
            f"an unexpected {a['kind']} was detected: peak demand {a['observed']:.1f} workers vs {a['expected']:.1f} expected"
        )
    # Trend from the median forecast; the p90 is the provisioning bound, not the expectation.
    now, p50 = f["observed_peak_now"], f["forecast_p50_next_hour"]
    trend = "rise" if p50 > 1.1 * now else "fall" if p50 < 0.9 * now else "stay close to its current level"
    reason_parts.append(
        f"the model expects demand to {trend} over the next {f['horizon_minutes']} minutes "
        f"(p50 {p50:.1f}, p90 {f['forecast_p90_next_hour']:.1f} busy workers, currently {now:.1f})"
    )
    if verb == "Increase":
        u = f["predicted_util_if_unchanged"]
        if u > f["sla_util"]:
            limit = f"above the {_pct(f['sla_util'])} SLA threshold"
        elif u > f["target_util"]:
            limit = f"above the {_pct(f['target_util'])} target utilisation (SLA threshold {_pct(f['sla_util'])})"
        else:
            limit = "so extra headroom is held while the anomaly lasts"
        reason_parts.append(
            f"with {f['current_replicas']} replicas predicted peak CPU utilisation would reach {_pct(u)}, {limit}"
        )
    else:
        after = f["predicted_util_if_changed"]
        if after <= f["target_util"]:
            bound = f", within the {_pct(f['target_util'])} target"
        elif after <= f["sla_util"]:
            bound = f", still below the {_pct(f['sla_util'])} SLA threshold"
        else:
            bound = " at the hour's forecast peak; shorter-horizon forecasts allow this reduction"
        reason_parts.append(
            f"current capacity would run at only {_pct(f['predicted_util_if_unchanged'])} predicted peak utilisation; "
            f"{f['recommended_replicas']} replicas keep it at {_pct(after)}{bound}"
        )
    reason = "; ".join(reason_parts)
    reason = reason[0].upper() + reason[1:] + "."
    return "\n".join([
        f"Recommendation: {verb} {f['service_name']} replicas from {f['current_replicas']} to {f['recommended_replicas']}.",
        f"Reason: {reason}",
        f"Estimated cost impact: {_money(f['hourly_cost_delta'])}/hour ({_money(f['monthly_cost_delta'])}/month at {f['pricing_label']}).",
        f"Estimated risk: SLA-violation risk {_pct(f['risk_current'])} -> {_pct(f['risk_recommended'])} over the next hour.",
        f"Confidence: {confidence_label(f['confidence'])} ({f['confidence']:.2f}).",
        f"Action status: {STATUS_TEXT.get(status, status)}.",
    ])


FIX_HINTS = {
    "has_all_fields": "Use exactly the six labelled lines.",
    "replica_counts": "Line 1 must say 'from <current_replicas> to <recommended_replicas>'.",
    "direction": "Use 'Increase' for more replicas and 'Reduce' for fewer.",
    "cites_forecast": "Quote forecast_p50_next_hour and forecast_p90_next_hour in the Reason.",
    "anomaly_consistent": "Mention an anomaly only if facts.anomaly is not null.",
    "cost_sign": "The cost line sign must match hourly_cost_delta.",
    "risk_cited": "State risk_current and risk_recommended as whole percentages.",
    "threshold_claims_true": "Only say a utilisation is above/below a threshold if numerically true.",
    "confidence_label": "Confidence label: High if >= 0.75, Medium if >= 0.5, else Low.",
    "no_invented_numbers": "Use only numbers from the facts; do not compute new ones.",
    "no_field_names": "Do not write JSON field names such as forecast_p50_next_hour; use plain English.",
}


_calls: deque[float] = deque()
_calls_lock = threading.Lock()


def _within_budget() -> bool:
    """Sliding one-hour cap on API calls so a long-running replay can't run up a bill."""
    limit = get_settings().openai_max_calls_per_hour
    now = time.monotonic()
    with _calls_lock:
        while _calls and now - _calls[0] > 3600:
            _calls.popleft()
        if len(_calls) >= limit:
            return False
        _calls.append(now)
        return True


def _openai(messages: list[dict]) -> str:
    from openai import OpenAI

    s = get_settings()
    client = OpenAI(api_key=s.openai_api_key, timeout=15.0, max_retries=1)
    resp = client.chat.completions.create(model=s.openai_model, temperature=0.2, messages=messages)
    return (resp.choices[0].message.content or "").strip()


def generate_detailed(facts: dict, status: str = "pending") -> dict:
    """LLM explanation verified against the facts by the rubric, with one corrective retry and a
    template fallback. Returns {"text", "source", "attempts": [{"text", "failed": [...]}, ...]}.
    source: openai (first draft passed), openai_retry (corrected draft passed), fallback
    (both drafts failed verification), template (no API key / API error). Never raises."""
    from app.services.explain_rubric import score

    attempts: list[dict] = []
    if get_settings().openai_api_key and _within_budget():
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"status": STATUS_TEXT.get(status, status), "facts": facts})},
        ]
        try:
            for source in ("openai", "openai_retry"):
                if source == "openai_retry" and not _within_budget():
                    break
                text = with_status(_openai(messages), status)
                failed = [k for k, ok in score(text, facts)["checks"].items() if not ok]
                attempts.append({"text": text, "failed": failed})
                if not failed:
                    return {"text": text, "source": source, "attempts": attempts}
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": "Fix these problems and rewrite all six lines: "
                              + " ".join(FIX_HINTS[k] for k in failed)}]
            log.info("LLM explanation failed verification (%s); using template", attempts[-1]["failed"])
            return {"text": template_explanation(facts, status), "source": "fallback", "attempts": attempts}
        except Exception as e:  # network, quota, auth, timeout
            log.warning("OpenAI explanation failed (%s); using template", e.__class__.__name__)
    return {"text": template_explanation(facts, status), "source": "template", "attempts": attempts}


def with_status(text: str, status: str) -> str:
    """Set the 'Action status' line from system state; the model never decides it."""
    line = f"Action status: {STATUS_TEXT.get(status, status)}."
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    for i, ln in enumerate(lines):
        if ln.lower().startswith("action status"):
            lines[i] = line
            return "\n".join(lines)
    return "\n".join(lines + [line])


def generate(facts: dict, status: str = "pending") -> tuple[str, str]:
    """Return (text, source). Never raises."""
    r = generate_detailed(facts, status)
    return r["text"], r["source"]
