"""Raw -> clean schema, dedupe, data-quality report.

Inputs : data/raw/darglobal.jsonl, darglobal_company.jsonl, darglobal_news.jsonl, wasalt.jsonl
Outputs: data/clean/{projects,company,news,listings}.jsonl, data/clean/data_report.md

Usage: .venv/Scripts/python -m ingestion.normalize
"""
from __future__ import annotations

import random
import re
from collections import Counter
from pathlib import Path

from scraper.common import DATA_DIR, RAW_DIR, read_jsonl, write_jsonl

CLEAN_DIR = DATA_DIR / "clean"

# Wasalt slugs drop the decimal point ("158.88 sqm" -> "15888"). Policy, so the LLM never reads a
# wrong number as fact:
#   value within the plausible range for the type      -> area_sqm = value,       area_status "from_url"
#   residential type, value/100 within plausible range -> area_sqm = value / 100, area_status "estimated"
#   otherwise (e.g. land: 162987 could be 1,629.87 or 162,987) -> area_sqm NULL, area_status "ambiguous"
# area_sqm_raw always keeps the number exactly as it appears in the URL.
PLAUSIBLE_SQM = {"apartment": (20, 1000), "floor": (20, 1000), "villa": (80, 3000), "rest house": (100, 5000),
                 "building": (100, 5000), "land": (100, 5000)}
RESIDENTIAL = {"apartment", "floor", "villa"}


def resolve_area(raw: int | None, ptype: str | None) -> tuple[float | None, str | None]:
    if raw is None:
        return None, None
    lo, hi = PLAUSIBLE_SQM.get(ptype or "", (20, 5000))
    if lo <= raw <= hi:
        return float(raw), "from_url"
    if ptype in RESIDENTIAL and lo <= raw / 100 <= hi:
        return round(raw / 100, 2), "estimated"
    return None, "ambiguous"


def area_text(area: float | None, status: str | None, raw: int | None) -> str:
    """Human/LLM-readable area statement that carries its own uncertainty."""
    if status == "from_url":
        return f"{area:g} sqm"
    if status == "estimated":
        return f"about {area:g} sqm (estimated: the listing URL shows '{raw}', decimal point inferred)"
    if status == "ambiguous":
        return f"area unclear (the listing URL shows '{raw}'; check the listing page)"
    return "area not stated"
CITY_ALIASES = {"jeddha": "Jeddah", "jedda": "Jeddah", "ar riyadh": "Riyadh", "makkah": "Mecca"}
TYPE_ALIASES = {"floor": "floor (apartment floor)"}


STATUSES = [("sold out", "Sold Out"), ("completed", "Completed"), ("ready", "Ready"),
            ("under development", "Under Development"), ("under construction", "Under Development"),
            ("off-plan", "Off-plan"), ("off plan", "Off-plan")]


def norm_status(s: str | None) -> str | None:
    low = (s or "").lower()
    return next((v for k, v in STATUSES if low.startswith(k) or k in low[:25]), None)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def norm_city(c: str | None) -> str | None:
    if not c:
        return None
    c = c.strip()
    return CITY_ALIASES.get(c.lower(), c.title() if c.islower() else c)


def clean_projects(raw: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in raw:
        key = slugify(r["name"])
        if key in seen:  # e.g. Trump International Hotel & Tower Dubai is listed under two URLs
            continue
        seen.add(key)
        sections = dedupe_sections(r["sections"])
        out.append({
            "id": f"darglobal:{key}",
            "source": "darglobal",
            "name": r["name"].strip(),
            "collection": r.get("collection"),
            "country": r.get("country"),
            "city": norm_city(r.get("city")),
            "location_raw": r.get("location_raw"),
            "brand_partners": sorted(set(r.get("brand_partners") or [])),
            "property_types": sorted(set(r.get("property_types") or [])),
            "starting_price": r.get("starting_price"),
            "currency": r.get("currency"),
            "starting_price_raw": r.get("starting_price_raw"),
            "status": norm_status(r.get("status")),
            "summary": r.get("meta_description"),
            "sections": sections,
            "n_chars": sum(len(s["text"]) for s in sections),
            "url": r["url"],
            "scraped_at": r["scraped_at"],
            "stale": bool(r.get("stale")),
            "last_seen": r.get("last_seen") or r["scraped_at"],
        })
    return out


def clean_listings(raw: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in raw:
        if r["listing_id"] in seen:
            continue
        seen.add(r["listing_id"])
        ptype = r.get("property_type")
        raw_area = r.get("area_sqm_slug")
        area, area_status = resolve_area(raw_area, ptype)
        beds = r.get("bedrooms")
        title_bits = [ptype.title() if ptype else "Property", f"for {r['purpose']}"]
        if beds:
            title_bits.append(f"{beds} bedroom" + ("s" if beds > 1 else ""))
        if area_status == "from_url":
            title_bits.append(f"{area:g} sqm")
        elif area_status == "estimated":
            title_bits.append(f"about {area:g} sqm")
        price = r.get("price")
        out.append({
            "id": f"wasalt:{r['listing_id']}",
            "source": "wasalt",
            "title": r.get("title") or ", ".join(title_bits),
            "purpose": r["purpose"],
            "property_type": ptype,
            "price": float(price) if price not in (None, "") else None,
            "currency": r.get("currency") or ("SAR" if price else None),
            "rent_period": r.get("rent_period"),
            "bedrooms": beds,
            "bathrooms": r.get("bathrooms"),
            "area_sqm": area,
            "area_status": area_status,
            "area_sqm_raw": raw_area,
            "area_text": area_text(area, area_status, raw_area),
            # Fields this source does not provide; stated explicitly so the agent says so instead of guessing.
            "missing_fields": [f for f, v in (("price", price), ("city", r.get("city")),
                                              ("district", r.get("district")),
                                              ("description", r.get("description"))) if not v],
            "floors": r.get("floors"),
            "facing": r.get("facing"),
            "street_width_m": r.get("street_width_m"),
            "city": norm_city(r.get("city")),
            "district": r.get("district"),
            "country": "Saudi Arabia",
            "description": r.get("description"),
            "detail_fetched": r.get("detail_fetched", False),
            "listed_lastmod": r.get("lastmod"),
            "url": r["url"],
            "scraped_at": r["scraped_at"],
        })
    return out


def dedupe_sections(sections: list[dict]) -> list[dict]:
    """Drop empty sections and sections whose text is contained in another (e.g. card summaries)."""
    secs = [x for x in sections if x["text"].strip()]
    out = []
    for i, x in enumerate(secs):
        t = x["text"].strip()
        if any(j != i and t in y["text"] and (len(y["text"]) > len(t) or j < i) for j, y in enumerate(secs)):
            continue
        out.append(x)
    return out


def clean_company(raw: list[dict]) -> list[dict]:
    out = []
    for r in raw:
        secs = dedupe_sections(r["sections"])
        row = {"id": f"darglobal:company:{slugify(r['page'])}", "source": "darglobal", "doc_type": "company",
               "page": r["page"], "title": r["title"], "sections": secs,
               "n_chars": sum(len(x["text"]) for x in secs), "url": r["url"], "scraped_at": r["scraped_at"]}
        for k in ("faq", "contact"):
            if r.get(k):
                row[k] = r[k]
        out.append(row)
    return out


def clean_news(raw: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in sorted(raw, key=lambda x: x["date"], reverse=True):
        key = slugify(r["title"])
        if key in seen:
            continue
        seen.add(key)
        secs = dedupe_sections(r["sections"])
        out.append({"id": f"darglobal:news:{key[:80]}", "source": "darglobal", "doc_type": "news",
                    "kind": r["kind"], "title": r["title"], "date": r["date"], "sections": secs,
                    "n_chars": sum(len(x["text"]) for x in secs), "url": r["url"], "scraped_at": r["scraped_at"]})
    return out


def null_rates(rows: list[dict], fields: list[str]) -> list[str]:
    lines = ["| field | non-null | null % |", "|---|---|---|"]
    for f in fields:
        nn = sum(1 for r in rows if r.get(f) not in (None, "", []))
        lines.append(f"| {f} | {nn}/{len(rows)} | {100 * (1 - nn / len(rows)):.0f}% |")
    return lines


def report(projects: list[dict], listings: list[dict], raw_counts: dict,
           company: list[dict] = (), news: list[dict] = ()) -> str:
    rng = random.Random(7)
    L = ["# Data quality report", "",
         f"Scraped at: {projects[0]['scraped_at'] if projects else '-'}", "",
         "## Counts", "", "| source | raw rows | clean rows |", "|---|---|---|",
         f"| darglobal (projects) | {raw_counts['darglobal']} | {len(projects)} |",
         f"| darglobal (company pages) | {raw_counts.get('company', 0)} | {len(company)} |",
         f"| darglobal (news, Jan-Sep 2026) | {raw_counts.get('news', 0)} | {len(news)} |",
         f"| wasalt (listings) | {raw_counts['wasalt']} | {len(listings)} |", ""]
    if company:
        faq = sum(len(c.get("faq") or []) for c in company)
        L += ["## DarGlobal company pages", "",
              f"{len(company)} pages, {sum(c['n_chars'] for c in company):,} chars, {faq} FAQ pairs. "
              "Pages: " + ", ".join(c["page"] for c in company), ""]
    if news:
        L += ["## DarGlobal news (Q1-Q3 2026)", "",
              f"{len(news)} articles ({', '.join(f'{k} {v}' for k, v in Counter(n['kind'] for n in news).items())}), "
              f"{min(n['date'] for n in news)} to {max(n['date'] for n in news)}. By month: "
              + ", ".join(f"{k} {v}" for k, v in sorted(Counter(n['date'][:7] for n in news).items())), ""]
    L += ["## DarGlobal projects", ""]
    L += null_rates(projects, ["country", "city", "brand_partners", "property_types", "starting_price",
                               "status", "summary", "sections"])
    L += ["", "By country: " + ", ".join(f"{k} {v}" for k, v in Counter(p["country"] for p in projects).most_common()),
          "", f"Text per project: min {min(p['n_chars'] for p in projects)}, "
              f"median {sorted(p['n_chars'] for p in projects)[len(projects) // 2]}, "
              f"max {max(p['n_chars'] for p in projects)} chars", ""]
    L += ["## Wasalt listings", ""]
    L += ["", "Area status: " + ", ".join(f"{k} {v}" for k, v in Counter(x["area_status"] for x in listings).most_common()), ""]
    L += null_rates(listings, ["property_type", "purpose", "bedrooms", "area_sqm", "price", "city",
                               "district", "description"])
    L += ["", "By purpose: " + ", ".join(f"{k} {v}" for k, v in Counter(x["purpose"] for x in listings).items()),
          "", "By type: " + ", ".join(f"{k} {v}" for k, v in Counter(x["property_type"] for x in listings).most_common()),
          "", f"Detail pages fetched: {sum(x['detail_fetched'] for x in listings)}/{len(listings)}", ""]
    L += ["## Random rows (eyeball check)", ""]
    for p in rng.sample(projects, min(3, len(projects))):
        L.append(f"- **{p['name']}** | {p['city']}, {p['country']} | brands {p['brand_partners']} | "
                 f"types {p['property_types']} | {p['n_chars']} chars | {p['url']}")
    for x in rng.sample(listings, min(2, len(listings))):
        L.append(f"- **{x['title']}** | price {x['price']} | city {x['city']} | {x['url']}")
    return "\n".join(L) + "\n"


def main() -> None:
    raw_p = read_jsonl(RAW_DIR / "darglobal.jsonl")
    raw_l = read_jsonl(RAW_DIR / "wasalt.jsonl")
    projects, listings = clean_projects(raw_p), clean_listings(raw_l)
    raw_c = read_jsonl(RAW_DIR / "darglobal_company.jsonl") if (RAW_DIR / "darglobal_company.jsonl").exists() else []
    raw_n = read_jsonl(RAW_DIR / "darglobal_news.jsonl") if (RAW_DIR / "darglobal_news.jsonl").exists() else []
    camp_path = RAW_DIR / "darglobal_campaigns.jsonl"
    for c in read_jsonl(camp_path) if camp_path.exists() else []:
        # Campaign landing pages use the project template but are marketing pages -> company docs.
        raw_c.append({"page": c["url"].replace("https://darglobal.co.uk/", ""), "title": c["name"],
                      "url": c["url"], "sections": c["sections"], "scraped_at": c["scraped_at"]})
    company, news = clean_company(raw_c), clean_news(raw_n)
    write_jsonl(CLEAN_DIR / "company.jsonl", company)
    write_jsonl(CLEAN_DIR / "news.jsonl", news)
    write_jsonl(CLEAN_DIR / "projects.jsonl", projects)
    write_jsonl(CLEAN_DIR / "listings.jsonl", listings)
    rep = report(projects, listings, {"darglobal": len(raw_p), "wasalt": len(raw_l), "company": len(raw_c),
                                      "news": len(raw_n)}, company, news)
    Path(CLEAN_DIR / "data_report.md").write_text(rep, encoding="utf-8")
    print(rep)


if __name__ == "__main__":
    main()
