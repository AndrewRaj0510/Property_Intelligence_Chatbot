"""Phase 2 sanity check: run sample queries through keyword / semantic / hybrid retrieval.

Usage: python scripts/sanity_retrieval.py [--k 5]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.retrieval import search  # noqa: E402

QUERIES = [
    ("beachfront villas with branded interiors", {}),
    ("Pagani", {}),
    ("golf course community", {"country": "Oman"}),
    ("How do I buy a DarGlobal property?", {"doc_type": "company"}),
    ("revenue growth results", {"doc_type": "news", "date_from": "2026-07-01"}),
    ("3 bedroom apartment for rent", {"doc_type": "listing"}),
]


def main() -> None:
    k = int(sys.argv[sys.argv.index("--k") + 1]) if "--k" in sys.argv else 5
    failures: list[str] = []
    for q, f in QUERIES:
        print(f"\n=== {q!r} {f or ''}")
        for mode in ("keyword", "semantic", "hybrid"):
            try:
                r = search(q, mode=mode, k=k, **f)
            except Exception as e:  # noqa: BLE001 - embedding endpoint outage
                print(f"  [{mode}] FAILED: {type(e).__name__}: {str(e)[:80]}")
                failures.append(mode)
                continue
            if r.timings_ms.get("embed_failed"):
                failures.append(mode)
            note = " (embedding failed -> keyword fallback)" if r.timings_ms.get("embed_failed") else ""
            print(f"  [{mode}] {r.timings_ms.get('total', 0):.0f} ms{note}")
            for h in r.hits:
                tag = "+" if h.expanded else " "
                print(f"   {tag}{h.score:7.3f} {h.id[:70]:70} {h.url}")
    print(f"\n{len(failures)} mode runs hit embedding failures" if failures else "\nall modes OK")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
