"""Explainability evaluation: generate recommendations by running the live engine on a scratch
SQLite database, then score each explanation with the automatic rubric.

Scores the deterministic template always, and the OpenAI layer when OPENAI_API_KEY is set
(limited to --max-llm calls to bound cost).

Usage (from repo root):
    .venv/Scripts/python backend/scripts/eval_explanations.py [--ticks 120] [--max-llm 40]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["REPLAY_ENABLED"] = "false"
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Recommendation, Service  # noqa: E402
from app.services import explain  # noqa: E402
from app.services.explain_rubric import score  # noqa: E402


def summarize(scores: list[dict]) -> dict:
    if not scores:
        return {"n": 0}
    per_check = Counter()
    for s in scores:
        per_check.update({k: int(v) for k, v in s["checks"].items()})
    return {
        "n": len(scores),
        "all_checks_passed_pct": round(100 * sum(s["passed"] == s["total"] for s in scores) / len(scores), 1),
        "mean_score_pct": round(100 * sum(s["passed"] / s["total"] for s in scores) / len(scores), 1),
        "per_check_pass_pct": {k: round(100 * v / len(scores), 1) for k, v in per_check.items()},
        "examples_failed": [s for s in scores if s["passed"] < s["total"]][:5],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=120)
    ap.add_argument("--max-llm", type=int, default=40)
    ap.add_argument("--rescore", action="store_true", help="Re-run the rubric on saved outputs; no API calls")
    args = ap.parse_args()
    out = ROOT / "reports" / "explainability_eval.json"
    if args.rescore:
        report = json.loads(out.read_text())
        saved = json.loads((ROOT / "reports" / "explanations_openai.json").read_text())
        report["openai"] = {**summarize([{**score(o["text"], o["facts"]), "text": o["text"]} for o in saved]),
                            "model": report["openai"]["model"], "sources": report["openai"].get("sources")}
        out.write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report["openai"].items() if k != "examples_failed"}, indent=2))
        return

    # Background explanation calls during the replay would hit the API once per pending
    # recommendation; only the capped sample below should cost anything.
    from app.services.engine import Engine
    Engine._explain_async = lambda self, rec_id: None

    with TestClient(app):
        with SessionLocal() as db:  # half the services need approval so pending recommendations occur
            for i, s in enumerate(db.scalars(select(Service)).all()):
                s.mode = "recommend" if i % 2 else "autopilot"
            db.commit()
        for _ in range(args.ticks):
            app.state.engine.tick()
        with SessionLocal() as db:
            recs = db.scalars(select(Recommendation)).all()
            facts = [(r.facts, r.status) for r in recs]

    template = [score(explain.template_explanation(f, st), f) for f, st in facts]
    report = {"recommendations": len(facts), "with_anomaly": sum(bool(f.get("anomaly")) for f, _ in facts),
              "template": summarize(template)}
    if get_settings().openai_api_key:
        sample = [x for x in facts if x[0].get("anomaly")][: args.max_llm // 2]
        sample += [x for x in facts if not x[0].get("anomaly")][: args.max_llm - len(sample)]
        first, final, sources, outputs = [], [], Counter(), []
        for f, st in sample:
            r = explain.generate_detailed(f, st)
            sources[r["source"]] += 1
            if r["attempts"]:
                first.append({**score(r["attempts"][0]["text"], f), "text": r["attempts"][0]["text"]})
                outputs.append({"facts": f, "status": st, "text": r["attempts"][0]["text"]})
            final.append({**score(r["text"], f), "text": r["text"]})
        report["openai"] = {**summarize(first), "model": get_settings().openai_model, "sources": dict(sources),
                            "note": "raw first drafts from the LLM, before verification"}
        report["openai_verified"] = {**summarize(final), "sources": dict(sources),
                                     "note": "what users see: verified draft, corrected retry, or template fallback"}
        # Keep every first draft so the rubric can be re-run without new API calls.
        (ROOT / "reports" / "explanations_openai.json").write_text(json.dumps(outputs, indent=2))
    else:
        report["openai"] = None
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "examples_failed"}, indent=2)[:3000])


if __name__ == "__main__":
    main()
