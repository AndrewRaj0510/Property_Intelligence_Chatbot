"""Bridge from the eval (Phase 5) to the app (Phase 4): writes app/eval_summary.json, shown in the UI sidebar.

Only aggregate numbers are written (rates, latency, tokens, cost). Never questions, answers, sources, trace
ids, error messages, model output or anything from the environment, so the file is safe to ship in the
container and to commit. Values are validated against a fixed schema before writing.
"""
import json
import time
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "app" / "eval_summary.json"
_ALLOWED = {
    "retrieval": {"n", "hit@5", "mrr@10", "latency_ms_p50", "errors"},
    "e2e": {"tag", "questions", "completed", "provider_failures", "correct", "tool_ok", "citation_valid",
            "refusal_ok", "leak_free", "latency_s_p50", "latency_s_p95", "tokens_avg", "tokens_p95",
            "llm_calls_avg", "actual_cost_per_1k_usd", "equiv_cost_per_1k_usd"},
}


def _clean(v):
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str) and len(v) <= 40 and v.replace("_", "").replace("-", "").isalnum():
        return v
    raise ValueError("eval summary accepts only numbers and short identifiers")


def update_summary(section: str, data: dict) -> None:
    allowed = _ALLOWED[section]
    if section == "retrieval":  # {mode: {metric: value}}
        clean = {m: {k: _clean(v) for k, v in d.items() if k in allowed}
                 for m, d in data.items() if m in ("keyword", "semantic", "hybrid")}
    else:
        clean = {k: _clean(v) for k, v in data.items() if k in allowed}
    cur = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    cur[section] = clean
    cur["updated"] = time.strftime("%Y-%m-%d")
    PATH.write_text(json.dumps(cur, indent=1) + "\n", encoding="utf-8")
