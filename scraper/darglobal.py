"""Scrape DarGlobal project pages -> data/raw/darglobal.jsonl

Discovery: darglobal.co.uk/sitemap.xml (the /projects listing page is behind Incapsula and
not reliably fetchable). Every candidate page is fetched and classified from its embedded
Next.js JSON (__NEXT_DATA__, a Strapi CMS payload), with no hard-coded page names:
  project   - project template with a canonical URL (real project pages)
  campaign  - project template without a canonical URL (marketing landing pages such as
              "Buy Property with Crypto"); saved to darglobal_campaigns.jsonl, used as company docs
  one-of-one collection - any /one-of-one/* page carries the whole collection; read once
  other     - skipped

Extraction: page layouts differ between projects, so text is collected generically from
the CMS JSON, grouped by top-level section (About, Key Features, Amenities, ...), skipping
media, links and styling fields. A few structured fields are pulled from known keys.

Usage: .venv/Scripts/python -m scraper.darglobal [--limit N]
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
from datetime import datetime, timezone

from scraper.common import RAW_DIR, PoliteClient, read_jsonl, setup_logging, write_jsonl

BASE = "https://darglobal.co.uk"
log = logging.getLogger("scraper.darglobal")

# Sitemap sections handled elsewhere (news/company scraper) or that are listing/category pages.
# Individual pages are NOT hard-coded: every other sitemap page is fetched and classified by the
# shape of its CMS JSON (see classify()).
SKIP_PREFIXES = ("blog", "press", "insights", "partners", "projects", "landing-page", "landingpage",
                 "pay-online", "campaigns")

# Keys whose values are never useful text.
SKIP_KEYS = {
    "id", "url", "href", "link", "cta_link", "mime", "hash", "ext", "provider", "provider_metadata",
    "previewUrl", "formats", "width", "height", "size", "createdAt", "updatedAt", "publishedAt", "locale",
    "slug", "vuid", "versionNumber", "versionComment", "ButtonTheme", "target", "events", "event",
    "bgColor", "formbgColor", "isTexture", "logoImgHeight", "pageLayoutStyle", "lat", "long",
    "canonicalURL", "metaRobots", "metaViewport", "structuredData", "metaImage", "metaSocial",
    "alternativeText", "caption", "localizations", "versions", "icon", "value_icon", "Duration_icon",
    "type", "format", "level", "contentPosition",
}
# Whole sections that are navigation, media, or other projects (would leak other projects' text).
SKIP_SECTIONS = {
    "OtherProject", "ProgressUpdate", "PhotoGallery_images", "LiveFeed", "View360", "downloadbrochure",
    "bookunit", "textures", "formIcon", "HeroBanner", "HeroBannerMobile", "desktopThumbnail",
    "mobileThumbnail", "image", "Image", "images", "logo", "Logo", "video", "Video", "localizations",
    "versions", "galleryList",
}

BRANDS = [
    "Trump", "Missoni", "Pagani", "Aston Martin", "Elie Saab", "Lamborghini", "Fendi Casa", "Mouawad",
    "Marriott", "W Hotels", "W Residences", "Paramount", "Dolce & Gabbana", "Bentley", "Mulliner",
]
PROPERTY_TYPES = {
    "apartment": r"\bapartments?\b|\bresidences?\b|\bflats?\b",
    "villa": r"\bvillas?\b",
    "penthouse": r"\bpenthouses?\b",
    "townhouse": r"\btownhouses?\b",
    "mansion": r"\bmansions?\b",
    "studio": r"\bstudios?\b",
    "plot": r"\bplots?\b",
    "hotel room": r"\bhotel rooms?\b|\bhotel apartments?\b|\bserviced\b",
}
COUNTRIES = {
    "United Arab Emirates": ["uae", "dubai", "abu dhabi", "united arab emirates", "rak", "ras al khaimah",
                             "marjan"],
    "Saudi Arabia": ["saudi", "riyadh", "jeddah", "wadi safar", "diriyah", "ksa"],
    "Oman": ["oman", "muscat", "aida", "sohar"],
    "Qatar": ["qatar", "doha", "lusail", "lumaia", "qetaifan"],
    "United Kingdom": ["albert hall", "park lane", "london", "united kingdom", "uk", "mayfair", "knightsbridge"],
    "Spain": ["cortesin", "spain", "marbella", "estepona", "costa del sol", "benahavis", "benahavís", "malaga"],
    "Maldives": ["maldives"],
    "Bosnia and Herzegovina": ["bosnia", "sarajevo"],
}
# Sub-brands / communities whose city is not in the page location field.
CITY_HINTS = {"amaya": "Jeddah", "trump plaza": "Jeddah", "rayana": "Wadi Safar", "wadi safar": "Wadi Safar",
              "aida": "Muscat", "simaisma": "Simaisma", "albert hall": "London", "cortesin": "Casares",
              "marjan": "Al Marjan Island"}
CITIES = ["Dubai", "Abu Dhabi", "Ras Al Khaimah", "Al Marjan Island", "Riyadh", "Jeddah", "Wadi Safar", "Muscat", "Doha", "London", "Marbella",
          "Estepona", "Benahavis", "Benahavís", "Casares", "Maldives", "Simaisma"]

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_text(s: str) -> str:
    s = html.unescape(TAG_RE.sub(" ", s)).replace(" ", " ")
    return WS_RE.sub(" ", s).strip()


def is_texty(key: str, s: str) -> bool:
    if len(s) < 2 or not re.search(r"[A-Za-z]", s):
        return False
    if s.startswith(("http://", "https://", "/", "#")) or re.fullmatch(r"#[0-9a-fA-F]{3,8}", s):
        return False
    if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", s):  # icon slugs like "infinity-pool"
        return False
    if re.fullmatch(r"[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+", s):  # untranslated i18n keys like "why-invest.overview"
        return False
    if re.search(r"\.(jpe?g|png|webp|svg|mp4|gif|pdf)$", s, re.I):
        return False
    return True


def collect(o, out: list[str], key: str = "") -> None:
    """Depth-first text collection; name/value pairs are rendered as 'name: value'."""
    if isinstance(o, dict):
        if "mime" in o or ("data" in o and len(o) == 1 and _is_media(o["data"])):
            return
        nm, val = o.get("name"), o.get("value")
        if isinstance(nm, str) and isinstance(val, str) and nm.strip() and "mime" not in o:
            # amenity lists store an icon slug in `value` ("gym", "infinity-pool"): keep the name only
            if re.fullmatch(r"[a-z0-9_-]+", val.strip()):
                out.append(clean_text(nm))
            elif is_texty("value", val):
                out.append(f"{clean_text(nm)}: {clean_text(val)}")
            return
        for k, v in o.items():
            if k in SKIP_KEYS or k in SKIP_SECTIONS:
                continue
            collect(v, out, k)
    elif isinstance(o, list):
        for v in o:
            collect(v, out, key)
    elif isinstance(o, str) and key not in SKIP_KEYS:
        s = clean_text(o)
        if is_texty(key, s):
            out.append(s)


def _is_media(d) -> bool:
    if isinstance(d, dict):
        return "attributes" in d and isinstance(d["attributes"], dict) and "mime" in d["attributes"]
    if isinstance(d, list):
        return all(_is_media(x) for x in d) if d else True
    return d is None


def dedupe(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def section_title(key: str, body) -> str:
    if isinstance(body, dict):
        for k in ("title", "Title", "Heading", "heading"):
            if isinstance(body.get(k), str) and body[k].strip():
                return clean_text(body[k])
        hdr = body.get("header")
        if isinstance(hdr, dict) and isinstance(hdr.get("title"), str):
            return clean_text(hdr["title"])
    return re.sub(r"(?<!^)(?=[A-Z])", " ", key).replace("_", " ").strip().title()


def guess_country(text: str) -> str | None:
    """Pick the country whose keywords occur most often (text is space-padded, lowercase)."""
    scores = {c: sum(text.count(f" {k} ") for k in kws) for c, kws in COUNTRIES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else None


def find_project_attrs(page_props: dict) -> dict | None:
    """Return the CMS attributes dict for a project page, or None if not a project page."""
    pdd = page_props.get("projectDetailsData")
    if isinstance(pdd, dict) and isinstance(pdd.get("attributes"), dict):
        a = pdd["attributes"]
        if isinstance(a.get("ProjectDetails"), dict):
            return {"title": a.get("title"), "projectCode": a.get("projectCode"), **a["ProjectDetails"]}
    pd = page_props.get("pageData")
    if isinstance(pd, dict) and isinstance(pd.get("data"), list) and pd["data"]:
        a = pd["data"][0].get("attributes") or {}
        if any(k in a for k in ("ProjectBanner", "amenities", "Amenities", "propertyCard", "properties",
                                "whyInvest", "Location", "AboutProject", "aboutProject")):
            return a
    return None


def parse_price(s: str | None) -> tuple[float | None, str | None]:
    if not s:
        return None, None
    t = s.upper().replace(",", "")
    cur = next((c for c in ("AED", "SAR", "QAR", "OMR", "GBP", "EUR", "USD") if c in t), None)
    if cur is None:
        cur = {"£": "GBP", "€": "EUR", "$": "USD"}.get(next((ch for ch in s if ch in "£€$"), ""), None)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(M|MN|MILLION|K)?\b", t)
    if not m:
        return None, cur
    val = float(m.group(1))
    mult = {"M": 1e6, "MN": 1e6, "MILLION": 1e6, "K": 1e3}.get(m.group(2) or "", 1)
    return val * mult, cur


def extract(url: str, page_props: dict, attrs: dict) -> dict:
    banner = attrs.get("ProjectBanner") or {}
    seo = attrs.get("SEO") or {}
    name = clean_text(attrs.get("title") or banner.get("Heading") or seo.get("metaTitle") or url.rsplit("/", 1)[-1])

    sections = []
    for key, body in attrs.items():
        if key in SKIP_KEYS or key in SKIP_SECTIONS or key in {"title", "projectCode", "SEO"} or body is None:
            continue
        if not isinstance(body, (dict, list)):
            continue
        texts: list[str] = []
        collect(body, texts, key)
        texts = dedupe(texts)
        if not texts or sum(len(t) for t in texts) < 20:
            continue
        sections.append({"heading": section_title(key, body), "key": key, "text": "\n".join(texts)})

    full = " ".join(s["text"] for s in sections) + " " + (seo.get("metaDescription") or "")
    low = (" " + full.lower() + " " + url.lower().replace("-", " ") + " ")

    location = banner.get("Location") or ""
    path = url.replace(BASE, "")
    norm = lambda t: " " + re.sub(r"[^a-z0-9]+", " ", t.lower()) + " "  # noqa: E731
    strong = norm(f"{location} {name} {seo.get('metaTitle') or ''} {path}")
    words = norm(low)
    country = guess_country(strong) or guess_country(words)
    city = next((c for c in CITIES if f" {c.lower()} " in norm(location)), None)
    city = city or next((v for k, v in CITY_HINTS.items() if f" {k} " in strong), None)
    city = city or next((c for c in CITIES if f" {c.lower()} " in strong), None)
    city = city or next((c for c in CITIES if f" {c.lower()} " in words), None)

    # Brand partners only from identity fields; body text mentions neighbouring projects' brands.
    brands = [b for b in BRANDS if norm(b) in strong or f" {b.lower()} " in norm(seo.get("metaDescription") or "")]

    ptypes = [t for t, rx in PROPERTY_TYPES.items() if re.search(rx, low)]
    status = None
    m = re.search(r"Status:\s*([^\n]+)", full)
    if m:
        status = m.group(1).strip()
    elif "off-plan" in low or "off plan" in low:
        status = "Off-plan"

    price_raw = banner.get("StartingPrice")
    price, currency = parse_price(price_raw)

    return {
        "source": "darglobal",
        "url": url,
        "name": name,
        "project_code": attrs.get("projectCode"),
        "location_raw": clean_text(location) or None,
        "country": country,
        "city": city,
        "brand_partners": brands,
        "property_types": ptypes,
        "starting_price_raw": clean_text(price_raw) if price_raw else None,
        "starting_price": price,
        "currency": currency,
        "status": status,
        "meta_title": seo.get("metaTitle"),
        "meta_description": seo.get("metaDescription"),
        "sections": sections,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def extract_one_of_one(page_props: dict) -> list[dict]:
    """The 1of1 collection (unique single properties) is embedded on every /one-of-one page."""
    rows = []
    for item in page_props.get("collectionsData") or []:
        a = item.get("attributes") or {}
        slug = (a.get("slug") or "").strip("/")
        if not slug or a.get("showOnLive") is False:
            continue
        seo = (a.get("SEO") or [{}])[0] if isinstance(a.get("SEO"), list) else (a.get("SEO") or {})
        attrs = {
            "title": a.get("title"),
            "ProjectBanner": {"Location": a.get("location"), "Heading": a.get("title")},
            "SEO": seo,
            "Overview": {"title": "Overview", "description": a.get("description")},
            "content": a.get("content"),
        }
        row = extract(f"{BASE}/one-of-one/{slug}", page_props, attrs)
        row["collection"] = "One of One"
        rows.append(row)
    return rows


def candidate_urls(client: PoliteClient) -> list[str]:
    urls = []
    for loc, _ in client.get_sitemap_locs(BASE + "/sitemap.xml"):
        path = loc.replace(BASE, "").strip("/")
        if path and path.split("/")[0] not in SKIP_PREFIXES:
            urls.append(BASE + "/" + path)
    # Visit /one-of-one/* pages first so the collection is captured early and the rest are skipped.
    return sorted(set(urls), key=lambda u: ("/one-of-one/" not in u, u))


def next_data(text: str) -> dict | None:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    return json.loads(m.group(1)) if m else None


CTA_RE = re.compile(r"^(buy|invest|exclusive|discover|book|win|register)\b", re.I)


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def official_names(props: dict) -> set[str]:
    """Project names from the site's own ProjectList (embedded in every page; incomplete)."""
    pl = (((props.get("globalData") or {}).get("attributes") or {}).get("ProjectList")) or []
    out = set()
    for x in pl:
        for k in ("name", "value"):
            if x.get(k):
                out.add(_norm(x[k]))
                out.add(_norm(x[k].split(",")[0]))
    return out


def classify(url: str, props: dict) -> str:
    """No single CMS field separates projects from marketing pages, so combine signals.
    campaign = not in the official ProjectList AND no canonical URL AND (call-to-action title
    or fewer than 3 content sections). Everything else on the project template is a project."""
    if props.get("collectionsData"):
        return "one_of_one"
    attrs = find_project_attrs(props)
    if not attrs:
        return "other"
    pdd = props.get("projectDetailsData")
    if not isinstance(pdd, dict):
        return "project"  # community template (Rayana, Amaya, Trump Plaza): always projects
    a = pdd.get("attributes") or {}
    seo = (a.get("ProjectDetails") or {}).get("SEO") or {}
    title = a.get("title") or ""
    if _norm(title) in official_names(props) or seo.get("canonicalURL"):
        return "project"
    n_sections = sum(1 for k, v in (a.get("ProjectDetails") or {}).items()
                     if isinstance(v, dict) and k not in SKIP_SECTIONS and k != "SEO")
    if CTA_RE.match(title.strip()) or n_sections < 3:
        return "campaign"
    return "project"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    setup_logging()

    client = PoliteClient(min_interval=1.0, max_retries=6)
    urls = candidate_urls(client)
    if args.limit:
        urls = urls[: args.limit]
    log.info("%d candidate pages from sitemap", len(urls))

    rows, campaigns, failed, skipped = [], [], [], []
    have_one_of_one = False
    for i, url in enumerate(urls, 1):
        if have_one_of_one and "/one-of-one/" in url:
            continue
        try:
            r = client.get(url, is_valid=lambda r: "__NEXT_DATA__" in r.text)
        except Exception as e:  # noqa: BLE001
            log.warning("[%d/%d] FAILED %s: %s", i, len(urls), url, e)
            failed.append(url)
            continue
        nd = next_data(r.content.decode("utf-8", "replace"))
        props = (nd or {}).get("props", {}).get("pageProps", {})
        kind = classify(url, props)
        final_url = str(r.url).rstrip("/")
        if kind == "one_of_one":
            ones = extract_one_of_one(props)
            rows.extend(ones)
            have_one_of_one = True
            log.info("[%d/%d] One of One collection: %d properties", i, len(urls), len(ones))
        elif kind in ("project", "campaign"):
            row = extract(final_url, props, find_project_attrs(props))
            row["collection"] = None
            (rows if kind == "project" else campaigns).append(row)
            log.info("[%d/%d] %s: %s | %s | %d sections", i, len(urls), kind, row["name"], row["country"],
                     len(row["sections"]))
        else:
            skipped.append(url)

    # Pages that fail now (404 / 5xx) but were scraped before: keep the last good copy, flagged stale,
    # instead of silently dropping a project from the dataset.
    prev_path = RAW_DIR / "darglobal.jsonl"
    if prev_path.exists() and failed:
        prev = {r["url"]: r for r in read_jsonl(prev_path)}
        for url in failed:
            old = prev.get(url)
            if old:
                old = {**old, "stale": True, "stale_reason": "page unavailable on re-scrape",
                       "last_seen": old.get("last_seen") or old["scraped_at"]}
                rows.append(old)
                log.warning("carried over last good copy of %s (scraped %s)", url, old["last_seen"])
    n = write_jsonl(RAW_DIR / "darglobal.jsonl", rows)
    write_jsonl(RAW_DIR / "darglobal_campaigns.jsonl", campaigns)
    log.info("wrote %d projects, %d campaign pages (skipped %d other pages, %d failed)",
             n, len(campaigns), len(skipped), len(failed))
    if failed:
        log.info("failed: %s", failed)


if __name__ == "__main__":
    main()
