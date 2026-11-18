"""Scrape DarGlobal company pages and news -> data/raw/darglobal_company.jsonl, darglobal_news.jsonl

Company pages: about, partners (+ each partner page), contact, FAQ, why-invest, hospitality, etc.
News: /press, /blog, /insights articles, kept only if published between --from and --to
(default 2026-01-01 .. 2026-09-30, i.e. Q1-Q3 2026).

Each article is located inside the page's CMS JSON as the object whose `slug` matches the URL,
so "related articles" boxes on the page are not mixed in. Publish date comes from that object
(`date` for press/blog, `published` for insights), falling back to schema.org `datePublished`.

No personal data: named people are not extracted as fields; contact facts are limited to
company emails, phone numbers and office addresses published on the contact page.

Usage: .venv/Scripts/python -m scraper.darglobal_content [--only company|news] [--from 2026-01-01] [--to 2026-09-30]
"""
from __future__ import annotations

import argparse
import logging
import re
from datetime import datetime, timezone

from scraper.common import RAW_DIR, PoliteClient, setup_logging, write_jsonl
from scraper.darglobal import BASE, clean_text, collect, dedupe, next_data

log = logging.getLogger("scraper.darglobal_content")

COMPANY_PAGES = {
    "about": "About DarGlobal",
    "partners": "Brand partners",
    "get-in-touch": "Contact",
    "faq": "FAQ",
    "why-invest": "Why invest",
    "hospitality": "Hospitality",
    "development-management": "Development management",
    "luxury-golf-communities": "Luxury golf communities",
    "investor": "Investor relations",
    "commercial": "Commercial",
}
NEWS_KINDS = ("press", "blog", "insights")
DROP_KEYS = {"globalData", "isMobile", "locale", "socialLinks", "thankyouPageData", "otherProjectData",
             "pressYears", "meta"}
# Embedded lists of articles on partner pages would duplicate news and add noise.
NOISE_KEY_RE = re.compile(r"article|blog|press|news|collection|otherProject", re.I)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\s()-]{7,}\d")


def find_by_slug(o, slug: str):
    """Depth-first search for the dict (or its `attributes`) whose slug equals `slug`."""
    if isinstance(o, dict):
        if str(o.get("slug", "")).strip("/").lower() == slug.lower():
            return o
        attrs = o.get("attributes")
        if isinstance(attrs, dict) and str(attrs.get("slug", "")).strip("/").lower() == slug.lower():
            return attrs
        for k, v in o.items():
            if k in DROP_KEYS:
                continue
            hit = find_by_slug(v, slug)
            if hit is not None:
                return hit
    elif isinstance(o, list):
        for v in o:
            hit = find_by_slug(v, slug)
            if hit is not None:
                return hit
    return None


def find_key(o, keys: set[str]):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in keys and isinstance(v, str) and DATE_RE.match(v):
                return v[:10]
        for v in o.values():
            hit = find_key(v, keys)
            if hit:
                return hit
    elif isinstance(o, list):
        for v in o:
            hit = find_key(v, keys)
            if hit:
                return hit
    return None


def article_date(article: dict, props: dict) -> str | None:
    for k in ("date", "published", "publishDate", "publishedDate"):
        v = article.get(k)
        if isinstance(v, str) and DATE_RE.match(v):
            return v[:10]
    return find_key(article, {"datePublished"}) or find_key(props, {"datePublished"})


def page_text(o) -> list[str]:
    out: list[str] = []
    collect(o, out)
    return dedupe(out)


def sections_from(o) -> list[dict]:
    """Group text by top-level CMS component (each becomes one section)."""
    if isinstance(o, list) and len(o) == 1:
        o = o[0]
    if isinstance(o, dict) and isinstance(o.get("attributes"), dict):
        o = o["attributes"]
    if isinstance(o, dict) and isinstance(o.get("data"), (dict, list)):
        return sections_from(o["data"])
    secs = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in DROP_KEYS or k == "SEO" or v is None:
                continue
            texts = page_text(v) if isinstance(v, (dict, list)) else page_text({k: v})
            if texts and sum(map(len, texts)) >= 20:
                head = texts[0] if len(texts[0]) < 90 else re.sub(r"(?<!^)(?=[A-Z])", " ", k).title()
                secs.append({"heading": head, "key": k, "text": "\n".join(texts)})
    elif isinstance(o, list):
        for i, v in enumerate(o):
            texts = page_text(v)
            if texts:
                head = texts[0] if len(texts[0]) < 90 else f"Part {i + 1}"
                secs.append({"heading": head, "key": str(i), "text": "\n".join(texts)})
    return secs


def rich_text(o) -> str:
    """Flatten Strapi rich-text blocks ([{type: paragraph, children: [{text}]}]) or plain strings."""
    if isinstance(o, str):
        return clean_text(o)
    parts: list[str] = []

    def walk(n):
        if isinstance(n, dict):
            if isinstance(n.get("text"), str):
                parts.append(n["text"])
            for c in n.get("children") or []:
                walk(c)
            if n.get("type") in ("paragraph", "list-item", "heading"):
                parts.append("\n")
        elif isinstance(n, list):
            for c in n:
                walk(c)

    walk(o)
    return "\n".join(clean_text(x) for x in "".join(parts).split("\n") if clean_text(x))


def faq_pairs(props) -> list[dict]:
    pairs = []

    def walk(o, topic=None):
        if isinstance(o, dict):
            if isinstance(o.get("faqs"), list):
                for f in o["faqs"]:
                    walk(f, clean_text(o.get("title") or "") or topic)
                return
            q = o.get("question") or o.get("Question")
            a = o.get("answer") or o.get("Answer")
            if isinstance(q, str) and a:
                ans = rich_text(a)
                if ans:
                    pairs.append({"topic": topic, "question": clean_text(q), "answer": ans})
                return
            for k, v in o.items():
                if k not in DROP_KEYS:
                    walk(v, topic)
        elif isinstance(o, list):
            for v in o:
                walk(v, topic)

    walk(props)
    seen, out = set(), []
    for p in pairs:
        if p["question"].lower() not in seen:
            seen.add(p["question"].lower())
            out.append(p)
    return out


def fetch_props(client: PoliteClient, url: str) -> tuple[str, dict] | None:
    try:
        r = client.get(url, is_valid=lambda r: "__NEXT_DATA__" in r.text)
    except Exception as e:  # noqa: BLE001
        log.warning("FAILED %s: %s", url, e)
        return None
    nd = next_data(r.content.decode("utf-8", "replace")) or {}
    return str(r.url).rstrip("/"), nd.get("props", {}).get("pageProps", {})


ROLE_MAILBOXES = {"crm", "marketing", "ir", "hr", "agents", "darglobal", "info", "sales", "media", "press",
                  "enquiries", "support", "careers", "investors"}


def redact_personal_emails(text: str) -> str:
    """Keep role mailboxes (crm@, marketing@ ...); redact addresses that name a person."""
    def sub(m):
        return m.group(0) if m.group(0).split("@")[0].lower() in ROLE_MAILBOXES else "[personal email removed]"
    return EMAIL_RE.sub(sub, text)


def scrape_company(client: PoliteClient, locs: list[str], now: str) -> list[dict]:
    urls = [f"{BASE}/{p}" for p in COMPANY_PAGES]
    urls += sorted(u for u in locs if u.startswith(BASE + "/partners/"))
    rows = []
    for i, url in enumerate(urls, 1):
        got = fetch_props(client, url)
        if not got:
            continue
        final_url, props = got
        path = url.replace(BASE + "/", "")
        body = {k: v for k, v in props.items() if k not in DROP_KEYS and not NOISE_KEY_RE.search(k)}
        pd = body.get("pageData")
        if isinstance(pd, dict) and "privacy policy" in str((pd.get("attributes") or {}).get("title", "")).lower():
            body.pop("pageData")  # site-wide privacy policy injected into every page
        if path == "faq":
            row_faq = faq_pairs(body)
            secs = [{"heading": f["question"], "key": "faq", "text": f"Q: {f['question']}\nA: {f['answer']}"}
                    for f in row_faq]
        else:
            secs = []
            for k, v in body.items():
                secs += [s for s in sections_from(v) if not NOISE_KEY_RE.search(s["key"])]
        for s in secs:
            s["text"] = redact_personal_emails(s["text"])
        secs = [s for s in secs if s["key"].lower() != "localizations"]
        text = " ".join(s["text"] for s in secs)
        row = {
            "source": "darglobal", "doc_type": "company", "page": path,
            "title": COMPANY_PAGES.get(path) or "Partner: " + path.split("/", 1)[-1].replace("-", " ").title(),
            "url": final_url, "sections": secs, "scraped_at": now,
        }
        if path == "faq":
            row["faq"] = row_faq
        if path == "get-in-touch":
            row["contact"] = {
                "emails": sorted({e for e in EMAIL_RE.findall(text)}),
                "phones": sorted({re.sub(r"\s+", " ", p).strip() for p in PHONE_RE.findall(text)}),
            }
        rows.append(row)
        log.info("[company %d/%d] %s: %d sections, %d chars", i, len(urls), path, len(secs), len(text))
    return rows


def scrape_news(client: PoliteClient, locs: list[str], start: str, end: str, now: str) -> list[dict]:
    urls = [u for u in locs if re.match(rf"{re.escape(BASE)}/({'|'.join(NEWS_KINDS)})/[^/]+$", u)]
    rows, out_of_range, undated = [], 0, []
    for i, url in enumerate(urls, 1):
        got = fetch_props(client, url)
        if not got:
            continue
        final_url, props = got
        kind, slug = url.replace(BASE + "/", "").split("/", 1)
        art = find_by_slug(props, slug)
        if art is None:
            undated.append(url)
            continue
        date = article_date(art, props)
        if not date:
            undated.append(url)
            continue
        if not (start <= date <= end):
            out_of_range += 1
            continue
        title = clean_text(next((art[k] for k in ("heading", "title", "Title", "Heading") if isinstance(art.get(k), str)), slug))
        secs = sections_from({k: v for k, v in art.items() if k not in {"heading", "title", "slug", "uniqueSlug"}})
        rows.append({
            "source": "darglobal", "doc_type": "news", "kind": kind, "title": title, "date": date,
            "url": final_url, "sections": secs, "scraped_at": now,
        })
        log.info("[news %d/%d] KEEP %s %s | %s", i, len(urls), date, kind, title[:70])
    log.info("news: kept %d, out of range %d, undated %d", len(rows), out_of_range, len(undated))
    if undated:
        log.info("undated: %s", undated)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["company", "news"])
    ap.add_argument("--from", dest="start", default="2026-01-01")
    ap.add_argument("--to", dest="end", default="2026-09-30")
    args = ap.parse_args()
    setup_logging()
    client = PoliteClient(min_interval=1.0, max_retries=6)
    locs = [l.rstrip("/") for l, _ in client.get_sitemap_locs(BASE + "/sitemap.xml")]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.only in (None, "company"):
        n = write_jsonl(RAW_DIR / "darglobal_company.jsonl", scrape_company(client, locs, now))
        log.info("wrote %d company pages", n)
    if args.only in (None, "news"):
        n = write_jsonl(RAW_DIR / "darglobal_news.jsonl", scrape_news(client, locs, args.start, args.end, now))
        log.info("wrote %d news articles (%s .. %s)", n, args.start, args.end)


if __name__ == "__main__":
    main()
