"""Sample Wasalt (KSA) listings -> data/raw/wasalt.jsonl

Discovery: the English product sitemap on cdn.wasalt.sa (sale + rent listing pages).
robots.txt disallows /search, so listing search pages are never requested.

Listing slugs encode purpose, property type, area, bedrooms and id, e.g.
  /en/property/sale/apartment-279-sqm-with-6-bedrooms-5856616
  /en/property/sale/villa-437-sqm-facing-north-on-20m-width-street-5856510
These are always parsed. Detail pages (price, city, district, description) are fetched only
with --details. As of 18 Nov 2026 wasalt.sa answers non-browser clients with a Cloudflare
challenge (HTTP 403, cf-mitigated: challenge); the scraper does not try to bypass it and
stops requesting detail pages after a few consecutive challenges.

No personal data is stored: agent names and phone numbers are never extracted.

Reproducibility: the sitemap changes daily, so the first run saves the sampled URLs to
data/raw/wasalt_sample_manifest.txt and later runs reuse exactly those listings. Use --resample
to draw a new sample. Listings that have left the sitemap are kept and flagged
`in_current_sitemap: false` (likely sold/rented or delisted).

Usage: .venv/Scripts/python -m scraper.wasalt [--n 300] [--rent-share 0.35] [--details] [--resample]
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
from datetime import datetime, timezone

from scraper.common import RAW_DIR, BlockedError, PoliteClient, setup_logging, write_jsonl

MANIFEST = RAW_DIR / "wasalt_sample_manifest.txt"
SITEMAP = "https://cdn.wasalt.sa/sitemap/product_sitemap_en_sa.xml.gz"
URL_RE = re.compile(r"^https://wasalt\.sa/en/property/(sale|rent)/(.+)-(\d+)$")
log = logging.getLogger("scraper.wasalt")


def parse_slug(url: str) -> dict | None:
    m = URL_RE.match(url)
    if not m:
        return None
    purpose, slug, lid = m.groups()
    ptype = re.match(r"([a-z]+(?:-[a-z]+)*?)(?=-\d|-with|-facing|$)", slug)
    area = re.search(r"-(\d+)-sqm", "-" + slug)
    beds = re.search(r"with-(\d+)-bedrooms?", slug)
    floors = re.search(r"with-(\d+)-floors?", slug)
    facing = re.search(r"facing-((?:north|south|east|west)(?:-(?:north|south|east|west))?)", slug)
    street = re.search(r"on-(\d+)m-width-street", slug)
    return {
        "listing_id": lid,
        "purpose": purpose,
        "property_type": ptype.group(1).replace("-", " ") if ptype else None,
        "area_sqm_slug": int(area.group(1)) if area else None,
        "bedrooms": int(beds.group(1)) if beds else None,
        "floors": int(floors.group(1)) if floors else None,
        "facing": facing.group(1).replace("-", " ") if facing else None,
        "street_width_m": int(street.group(1)) if street else None,
    }


def parse_detail(text: str) -> dict:
    """Best-effort extraction from a listing page (schema.org JSON-LD). Untested against live
    pages because they are behind a Cloudflare challenge; returns only what it finds."""
    out: dict = {}
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', text, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if not isinstance(d, dict):
                continue
            offers = d.get("offers") or {}
            if isinstance(offers, dict) and offers.get("price"):
                out.setdefault("price", offers.get("price"))
                out.setdefault("currency", offers.get("priceCurrency"))
            addr = d.get("address") or {}
            if isinstance(addr, dict):
                out.setdefault("city", addr.get("addressLocality"))
                out.setdefault("district", addr.get("streetAddress") or addr.get("addressRegion"))
            if d.get("name"):
                out.setdefault("title", d["name"])
            if d.get("description"):
                out.setdefault("description", d["description"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--rent-share", type=float, default=0.35)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--details", action="store_true", help="also try fetching detail pages")
    ap.add_argument("--resample", action="store_true", help="ignore the saved manifest and draw a new sample")
    args = ap.parse_args()
    setup_logging()

    client = PoliteClient(min_interval=1.0, max_retries=2)
    locs = client.get_sitemap_locs(SITEMAP)
    log.info("%d listing URLs in sitemap", len(locs))

    lastmods = dict(locs)
    if MANIFEST.exists() and not args.resample:
        urls = [u.strip() for u in MANIFEST.read_text(encoding="utf-8").splitlines()
                if u.strip() and not u.startswith("#")]
        sample = [(u, lastmods.get(u), parse_slug(u)) for u in urls]
        log.info("reusing %d listings from %s (%d no longer in sitemap)", len(sample), MANIFEST.name,
                 sum(u not in lastmods for u in urls))
    else:
        sample = draw_sample(locs, args)
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(f"# Wasalt sample drawn {datetime.now(timezone.utc).date()} seed={args.seed} "
                            f"n={args.n} rent_share={args.rent_share}\n" + "\n".join(u for u, _, _ in sample) + "\n",
                            encoding="utf-8")
        log.info("saved new sample manifest (%d URLs)", len(sample))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, consecutive_blocks, details_ok = [], 0, 0
    try_details = args.details
    for url, lastmod, p in sample:
        row = {"source": "wasalt", "url": url, "lastmod": lastmod, **p, "in_current_sitemap": url in lastmods,
               "detail_fetched": False, "scraped_at": now}
        if try_details:
            try:
                r = client.get(url)
                row.update(parse_detail(r.text))
                row["detail_fetched"] = True
                details_ok += 1
                consecutive_blocks = 0
            except BlockedError as e:
                consecutive_blocks += 1
                log.warning("%s", e)
                if consecutive_blocks >= 3:
                    log.warning("wasalt.sa keeps serving a Cloudflare challenge; continuing with sitemap fields only")
                    try_details = False
        rows.append(row)

    n = write_jsonl(RAW_DIR / "wasalt.jsonl", rows)
    log.info("wrote %d listings (%d with detail pages)", n, details_ok)


def draw_sample(locs, args) -> list[tuple]:
    # Keep only slugs with at least type + area or bedrooms, so the sample is informative.
    parsed = []
    for url, lastmod in locs:
        p = parse_slug(url)
        if p and p["property_type"] and (p["area_sqm_slug"] or p["bedrooms"]):
            parsed.append((url, lastmod, p))
    rng = random.Random(args.seed)
    rent = [x for x in parsed if x[2]["purpose"] == "rent"]
    sale = [x for x in parsed if x[2]["purpose"] == "sale"]
    n_rent = round(args.n * args.rent_share)
    sample = rng.sample(rent, min(n_rent, len(rent))) + rng.sample(sale, min(args.n - n_rent, len(sale)))
    log.info("sampled %d (rent %d, sale %d)", len(sample), n_rent, len(sample) - n_rent)
    return sample


if __name__ == "__main__":
    main()
