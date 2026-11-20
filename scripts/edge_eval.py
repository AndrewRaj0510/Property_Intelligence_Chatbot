"""Live edge-case eval for list / table questions (real LLM). Checks per question:
expected tool used, completeness (every expected name appears in the answer), guardrail not triggered, latency.
Expected names come from SQLite, so the check stays correct if the data is refreshed.
Usage: python scripts/edge_eval.py [--only N]   -> data/edge_eval.jsonl"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

os.environ.setdefault("RATE_LIMIT_TURNS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402
from app.graph import run_turn  # noqa: E402
from app.observability import flush  # noqa: E402


def names(where: str) -> list[str]:
    con = sqlite3.connect(config.SQLITE_PATH)
    out = [r[0] for r in con.execute(f"SELECT name FROM projects WHERE {where}")]
    con.close()
    return out


# (question, expected tool, expected names, must_contain any-of phrases, follow_up_of_previous)
CASES = [
    ("List all DarGlobal projects in Oman.", "list_projects", names("country='Oman'"), [], False),
    ("Which of those have villas?", "list_projects", names("country='Oman' AND property_types LIKE '%villa%'"), [], True),
    ("How many projects does DarGlobal have in KSA?", "list_projects", [], [str(len(names("country='Saudi Arabia'")))], False),
    ("Show me every Trump branded project as a table.", "list_projects", names("brand_partner LIKE '%Trump%'"), [], False),
    ("Any DarGlobal projects in Marbella?", "list_projects", names("city IN ('Benahavis','Casares')"), [], False),
    ("Do they have projects in Egypt?", "list_projects", [], ["egypt"], False),
    ("Projects in the UAE under 2 million AED?", "list_projects", names("country='United Arab Emirates'"), ["price"], False),
    ("Which off-plan projects are there in Dubai?", "list_projects", names("city='Dubai' AND status='Under Development'"), [], False),
    ("GCC projects with Fendi interiors?", "list_projects", names("brand_partner LIKE '%Fendi%'"), [], False),
    ("projetcs in qatarr", "list_projects", names("country='Qatar'"), [], False),
    ("Which projects in Oman have golf course views?", "search_projects", [], ["Fairway Villas"], False),
    ("List all DarGlobal projects with their country.", "list_projects", names("1=1"), [], False),
    ("ما هي مشاريع دار جلوبال في قطر؟", "list_projects", names("country='Qatar'"), [], False),
    ("List 5 apartments for sale on Wasalt in a table.", "search_listings", [], ["Wasalt #"], False),
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    only = int(sys.argv[sys.argv.index("--only") + 1]) if "--only" in sys.argv else None
    history: list[dict] = []
    results = []
    out = config.DATA_DIR / "edge_eval.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for i, (q, tool, expected, phrases, follow) in enumerate(CASES, 1):
            if only and i != only:
                continue
            if not follow:
                history = []
            r = run_turn(q, history=history, session_id=f"edge-{i}", user_id="eval")
            history += [{"role": "user", "content": q}, {"role": "assistant", "content": r["answer"]}]
            low = r["answer"].lower()
            missing = [n for n in expected if n.lower() not in low]
            ok_phr = all(p.lower() in low for p in phrases)
            ok = (tool in r["tools_used"]) and not missing and ok_phr and not r["guardrail"].get("replaced") and not r["error"]
            rec = {"i": i, "question": q, "ok": ok, "tools": r["tools_used"], "expected_tool": tool,
                   "expected": len(expected), "missing": missing, "phrases_ok": ok_phr,
                   "guardrail": r["guardrail"], "error": r["error"], "calls": r["cost"]["llm_calls"],
                   "latency_ms": r["cost"]["latency_ms"], "answer": r["answer"]}
            results.append(rec)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[{'PASS' if ok else 'FAIL'}] {i:2} {q[:60]:60} tools={r['tools_used']} "
                  f"found={len(expected)-len(missing)}/{len(expected)} phrases={ok_phr} "
                  f"calls={r['cost']['llm_calls']} total={r['cost']['latency_ms'].get('total')}ms "
                  f"llm={r['cost']['latency_ms'].get('llm')}ms replaced={r['guardrail'].get('replaced')} err={r['error']}"
                  + (f"\n      missing: {missing}" if missing else ""), flush=True)
            time.sleep(2)
    lat = sorted(x["latency_ms"]["total"] for x in results)
    if lat:
        print(f"\n{sum(x['ok'] for x in results)}/{len(results)} pass | latency p50={lat[len(lat)//2]} ms "
              f"p95={lat[min(len(lat)-1, int(len(lat)*0.95))]} ms max={lat[-1]} ms")
    flush()


if __name__ == "__main__":
    main()
