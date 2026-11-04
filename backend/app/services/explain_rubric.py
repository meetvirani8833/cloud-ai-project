"""Automatic rubric for recommendation explanations.

Each check compares the generated text against the structured ``facts`` it was generated
from, so it measures whether the explanation references the evidence that actually triggered
the recommendation, and whether it invents numbers.
"""

from __future__ import annotations

import re

# Numbers not glued to letters (so "p50" or "h12" are labels, not values).
NUM = re.compile(r"(?<![A-Za-z\d.])[-+−]?\$?\d[\d,]*\.?\d*%?(?![A-Za-z\d])")
FROM_TO = re.compile(r"\bfrom\s+(\d+)\b.{0,30}?\bto\s+(\d+)\b", re.IGNORECASE)


def _numbers(text: str) -> list[tuple[float, float]]:
    """(value, tolerance) per number; the tolerance is half a unit of the printed precision,
    so "1.3" matches a stored 1.26 but "1.4" does not."""
    out = []
    for tok in NUM.findall(text):
        t = tok.replace("$", "").replace(",", "").replace("−", "-").rstrip("%").rstrip(".")
        try:
            val = float(t)
        except ValueError:
            continue
        decimals = len(t.split(".")[1]) if "." in t else 0
        out.append((val, 0.5 * 10 ** -decimals + 1e-9))
    return out


def _allowed_numbers(f: dict) -> list[float]:
    vals: list[float] = []

    def add(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            vals.extend([x, abs(x), round(x, 2), round(abs(x), 2), round(100 * x), round(100 * abs(x)), round(100 * x, 1)])
        elif isinstance(x, dict):
            for v in x.values():
                add(v)
        elif isinstance(x, str):
            vals.extend(v for v, _ in _numbers(x))

    for v in f.values():
        add(v)
    vals.extend([5, 60, 730, 1, 0, 100])  # units/time constants used in the template wording
    return vals


def _close(x: tuple[float, float], allowed: list[float]) -> bool:
    val, tol = x
    return any(abs(val - a) <= tol for a in allowed)


def _replicas_ok(text: str, f: dict) -> bool:
    line = next((ln for ln in text.splitlines() if ln.startswith("Recommendation:")), text)
    m = FROM_TO.search(line)
    return bool(m) and (int(m.group(1)), int(m.group(2))) == (f["current_replicas"], f["recommended_replicas"])


CLAIM = re.compile(r"(\d+)%[^.;]*?\b(above|below|exceeds?|within)\b[^.;]*?(\d+)%")


def _threshold_claims_ok(low: str, f: dict) -> bool:
    """Every "X% above/below Y%" style claim must be numerically true."""
    for a, word, b in CLAIM.findall(low):
        x, y = float(a), float(b)
        if word in ("above", "exceeds", "exceed") and not x > y:
            return False
        if word in ("below", "within") and not x <= y:
            return False
    return True


NEGATED = re.compile(r"\b(no|not|without|nor)\b[^.;,]{0,25}?(anomal\w*|unexpected\w*|spikes?)")


def _anomaly_ok(low: str, f: dict) -> bool:
    """Mentions an anomaly if and only if one triggered the recommendation. Negated mentions
    ("no anomaly detected") count as not claiming one."""
    claimed = NEGATED.sub("", low)
    asserts = any(w in claimed for w in ("anomal", "unexpected", "spike"))
    return asserts == bool(f.get("anomaly"))


COST_SIGN = re.compile(r"([+\-−])\s*\$?\s*\d")


def _cost_sign_ok(text: str, f: dict) -> bool:
    """The cost line's sign (or wording) must match the sign of the hourly cost delta."""
    line = next((ln for ln in text.splitlines() if ln.lower().startswith("estimated cost impact")), "").lower()
    m = COST_SIGN.search(line)
    if m:
        positive = m.group(1) == "+"
    elif any(w in line for w in ("increase", "additional", "extra", "more")):
        positive = True
    elif any(w in line for w in ("saving", "decrease", "reduction", "less", "lower")):
        positive = False
    else:
        return False
    return positive == (f["hourly_cost_delta"] > 0)


FIELD_NAME = re.compile(r"[A-Za-z]+_[A-Za-z_]+")


def _confidence_ok(text: str, f: dict) -> bool:
    """High >= 0.75, Medium >= 0.5, else Low (the product's definition)."""
    line = next((ln for ln in text.splitlines() if ln.lower().startswith("confidence")), "").lower()
    want = "high" if f["confidence"] >= 0.75 else "medium" if f["confidence"] >= 0.5 else "low"
    return want in line and not any(w in line for w in {"high", "medium", "low"} - {want})


def score(text: str, f: dict) -> dict:
    low = text.lower()
    up = f["recommended_replicas"] > f["current_replicas"]
    checks = {
        "has_all_fields": all(k in text for k in ("Recommendation:", "Reason:", "Estimated cost impact:", "Estimated risk:", "Confidence:", "Action status:")),
        "replica_counts": _replicas_ok(text, f),
        "direction": (("increase" in low or "scale out" in low or "add" in low) if up else ("reduce" in low or "decrease" in low or "scale in" in low or "remove" in low)),
        "cites_forecast": any(_close(x, [f["forecast_p50_next_hour"], f["forecast_p90_next_hour"]]) for x in _numbers(text)),
        "anomaly_consistent": _anomaly_ok(low, f),
        "cost_sign": _cost_sign_ok(text, f),
        "risk_cited": str(round(100 * f["risk_current"])) in text and str(round(100 * f["risk_recommended"])) in text,
        "threshold_claims_true": _threshold_claims_ok(low, f),
        "confidence_label": _confidence_ok(text, f),
        "no_field_names": FIELD_NAME.search(text) is None,
    }
    allowed = _allowed_numbers(f)
    unknown = [x for x in _numbers(text) if not _close(x, allowed)]
    checks["no_invented_numbers"] = len(unknown) == 0
    return {"passed": sum(checks.values()), "total": len(checks), "checks": checks, "unknown_numbers": unknown[:10]}
