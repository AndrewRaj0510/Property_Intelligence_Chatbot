"""Full data refresh: scrape -> normalize -> SQLite -> chunks -> Qdrant.

Run by the scheduled GitHub Actions workflow (.github/workflows/refresh-data.yml) every 3 days,
or by hand: .venv/Scripts/python scripts/refresh_data.py [--skip-qdrant]

Safety gate: after normalizing, the new clean counts are compared with the previous run. If any
document type shrinks by more than MAX_DROP (e.g. the site was down or changed its layout), the
previous clean files are restored and nothing is rebuilt, so a bad scrape never reaches the app.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "clean"
TYPES = ("projects", "company", "news", "listings")
MAX_DROP = 0.2  # abort if a doc type loses more than 20% of its rows


def run(module: str) -> None:
    print(f"\n=== {module}", flush=True)
    subprocess.run([sys.executable, "-m", module], cwd=ROOT, check=True)


def snapshot() -> dict[str, bytes]:
    return {t: (CLEAN / f"{t}.jsonl").read_bytes() for t in TYPES if (CLEAN / f"{t}.jsonl").exists()}


def count(data: bytes) -> int:
    return sum(1 for line in data.splitlines() if line.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-qdrant", action="store_true", help="do not rebuild the Qdrant collection")
    args = ap.parse_args()

    before = snapshot()
    for m in ("scraper.darglobal", "scraper.darglobal_content", "scraper.wasalt", "ingestion.normalize"):
        run(m)
    after = snapshot()

    failed = []
    for t in TYPES:
        old, new = count(before.get(t, b"")), count(after.get(t, b""))
        print(f"{t:9} {old:4} -> {new:4}")
        if new == 0 or (old and new < old * (1 - MAX_DROP)):
            failed.append(t)
    if failed:
        for t, data in before.items():
            (CLEAN / f"{t}.jsonl").write_bytes(data)
        sys.exit(f"Sanity gate failed for {failed}; previous clean data restored, nothing rebuilt.")

    run("ingestion.build_sqlite")
    run("ingestion.chunking")
    if not args.skip_qdrant:
        run("ingestion.build_qdrant")
    # Record the snapshot date so the app's prompt and UI caption show when data was scraped.
    stats_path = ROOT / "data" / "index_stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    stats["snapshot_date"] = datetime.now(timezone.utc).strftime("%d %b %Y")
    stats_path.write_text(json.dumps(stats, indent=2))
    print("\nRefresh complete.")


if __name__ == "__main__":
    main()
