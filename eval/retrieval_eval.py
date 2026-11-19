"""Retrieval eval: Hit@5 and MRR@10 at document (parent) level for keyword vs semantic vs hybrid.
Uses golden_set items that have `relevant` parent ids and a `search` filter (the same doc_type filter the tool uses).
No LLM calls; semantic/hybrid need the embedding API. Writes eval/results/retrieval.json and .md.
Usage: python eval/retrieval_eval.py"""
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import retrieval  # noqa: E402

MODES = ["keyword", "semantic", "hybrid"]


def load() -> list[dict]:
    rows = [json.loads(l) for l in (ROOT / "eval" / "golden_set.jsonl").open(encoding="utf-8") if l.strip()]
    return [r for r in rows if r.get("relevant") and r.get("search")]


CACHE = ROOT / "eval" / "results" / "query_embeddings.json"
_cache: dict = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
_embed = retrieval.embed_query


def _cached_embed(text: str):
    """Disk cache so re-runs don't spend the free embedding quota (50 req/day without credits)."""
    if text not in _cache:
        _cache[text] = list(_embed(text))
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(_cache), encoding="utf-8")
    return _cache[text]


retrieval.embed_query = _cached_embed


def ranked_parents(q: str, mode: str, flt: dict) -> list[str]:
    res = retrieval.search(q, mode=mode, k=10, max_per_parent=1, expand=False, **flt)
    if res.timings_ms.get("embed_failed"):  # hybrid silently fell back to keyword: not a hybrid result
        raise RuntimeError("embedding unavailable, hybrid degraded to keyword")
    return [h.payload.get("parent_id", h.id) for h in res.hits]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    items = load()
    per_item, summary = [], {}
    for mode in MODES:
        hits, rrs, lat, errors = [], [], [], 0
        for it in items:
            t0 = time.perf_counter()
            try:
                ranked = ranked_parents(it["question"], mode, it["search"])
            except Exception as e:  # noqa: BLE001 - embedding API down: count as a miss, keep going
                errors += 1
                ranked = []
                print(f"[{mode}] {it['id']} error: {type(e).__name__}")  # type only: messages can carry URLs/ids
            lat.append((time.perf_counter() - t0) * 1000)
            rel = set(it["relevant"])
            rank = next((i + 1 for i, p in enumerate(ranked) if p in rel), None)
            hits.append(1 if rank and rank <= 5 else 0)
            rrs.append(1 / rank if rank else 0.0)
            per_item.append({"id": it["id"], "mode": mode, "rank": rank, "top3": ranked[:3]})
            if mode != "keyword":
                time.sleep(0.5)  # gentle on the free embedding endpoint
        summary[mode] = {"n": len(items), "hit@5": round(sum(hits) / len(items), 3),
                         "mrr@10": round(sum(rrs) / len(items), 3),
                         "latency_ms_p50": round(statistics.median(lat)), "errors": errors}
        print(mode, summary[mode], flush=True)

    out = ROOT / "eval" / "results"
    out.mkdir(exist_ok=True)
    (out / "retrieval.json").write_text(json.dumps({"summary": summary, "items": per_item}, indent=1), encoding="utf-8")
    md = ["| Mode | Hit@5 | MRR@10 | p50 latency (ms) | errors |", "|---|---|---|---|---|"]
    md += [f"| {m} | {s['hit@5']:.2f} | {s['mrr@10']:.2f} | {s['latency_ms_p50']} | {s['errors']} |"
           for m, s in summary.items()]
    md.append(f"\n{len(items)} labelled queries (project, company, news), document-level relevance, doc_type pre-filter. "
              "Errors (embedding API unavailable) count as misses.")
    (out / "retrieval.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    from eval.summary import update_summary
    update_summary("retrieval", summary)


if __name__ == "__main__":
    main()
