"""Turn clean JSONL into retrieval chunks (data/chunks.jsonl).

Strategy (architecture.md section 5):
- project: 1 summary card + 1 chunk per CMS section, each with a contextual header
- listing: 1 templated card (missing fields stated explicitly, never blank or 0)
- company: 1 chunk per page section; FAQ page -> 1 chunk per question-answer pair
- news: title + date + body, split at ~400 tokens
Sections > MAX_TOKENS are split on line boundaries; sections < MIN_TOKENS are merged into the next.
Usage: python -m ingestion.chunking
"""
import json
import re

import tiktoken

from app import security
from app.config import CHUNKS_PATH
from ingestion.io_utils import load

MAX_TOKENS, NEWS_MAX_TOKENS, MIN_TOKENS = 500, 400, 30
_enc = tiktoken.get_encoding("cl100k_base")
# Layout tokens the CMS leaks into text ("left", "center", ...) carry no meaning.
_NOISE_LINES = {"left", "right", "center", "golden", "orange", "learn more", "overview", "content"}
ND = "not published in our data"


def ntokens(text: str) -> int:
    return len(_enc.encode(text))


def clean_text(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln and ln.lower() not in _NOISE_LINES)


def split_long(text: str, max_tokens: int) -> list[str]:
    """Split on line boundaries (then sentences) so each part stays under max_tokens."""
    if ntokens(text) <= max_tokens:
        return [text]
    units: list[str] = []
    for line in text.splitlines():
        if ntokens(line) <= max_tokens:
            units.append(line)
        else:
            units.extend(s for s in re.split(r"(?<=[.!?])\s+", line) if s)
    parts, cur = [], []
    for u in units:
        if cur and ntokens("\n".join(cur + [u])) > max_tokens:
            parts.append("\n".join(cur))
            cur = []
        cur.append(u)
    if cur:
        parts.append("\n".join(cur))
    return parts


def merge_small(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Merge (heading, text) sections under MIN_TOKENS into the next one (or the previous one at the end)."""
    out: list[tuple[str, str]] = []
    carry: tuple[str, str] | None = None
    for h, t in sections:
        if carry:
            h, t = f"{carry[0]} / {h}", f"{carry[1]}\n{t}"
            carry = None
        if ntokens(t) < MIN_TOKENS:
            carry = (h, t)
            continue
        out.append((h, t))
    if carry:
        if out:
            h, t = out.pop()
            out.append((f"{h} / {carry[0]}", f"{t}\n{carry[1]}"))
        else:
            out.append(carry)
    return out


def prep_sections(sections: list[dict], max_tokens: int = MAX_TOKENS) -> list[tuple[str, str]]:
    seen, prepared = set(), []
    for s in sections:
        text = clean_text(s["text"])
        if not text or text in seen:
            continue
        seen.add(text)
        prepared.append(((s.get("heading") or "Section").strip(), text))
    return [(h, part) for h, t in merge_small(prepared) for part in split_long(t, max_tokens)]


def _base(doc_type: str, parent_id: str, url: str, **meta) -> dict:
    return {"doc_type": doc_type, "parent_id": parent_id, "url": url, **meta}


def project_chunks(p: dict) -> list[dict]:
    loc = ", ".join(x for x in (p["city"], p["country"]) if x) or "location not published"
    brand = ", ".join(p["brand_partners"]) or "-"
    meta = dict(source="darglobal", project_name=p["name"], country=p["country"], city=p["city"],
                brand=p["brand_partners"], property_type=p["property_types"], collection=p["collection"])
    summary = "\n".join([
        f"[Project: {p['name']} | {loc} | Brand: {brand} | Summary]",
        f"{p['name']} is a DarGlobal project in {loc}.",
        f"Collection: {p['collection'] or '-'}",
        f"Brand partner(s): {brand}",
        f"Property types: {', '.join(p['property_types']) or ND}",
        f"Status: {p['status'] or ND}",
        f"Starting price: {p['starting_price_raw'] or ND}",
        f"Description: {p['summary'] or '-'}",
        f"URL: {p['url']}",
    ])
    chunks = [{**_base("project", p["id"], p["url"], **meta), "id": f"{p['id']}#summary",
               "section": "Summary", "chunk_kind": "summary", "text": summary}]
    for i, (h, t) in enumerate(prep_sections(p["sections"])):
        header = f"[Project: {p['name']} | {loc} | Brand: {brand} | Section: {h}]"
        chunks.append({**_base("project", p["id"], p["url"], **meta), "id": f"{p['id']}#s{i}",
                       "section": h, "chunk_kind": "section", "text": f"{header}\n{t}"})
    return chunks


def listing_chunk(r: dict) -> dict:
    if r["price"] is not None:
        period = f" / {r['rent_period']}" if r["rent_period"] else ""
        price = f"{r['price']:,.0f} {r['currency'] or ''}{period}".strip()
    else:
        price = ND
    loc = ", ".join(x for x in (r["district"], r["city"]) if x) or ND
    lines = [
        f"[Listing | Wasalt | {r['country']}]",
        r["title"],
        f"Purpose: for {r['purpose']}" if r["purpose"] else f"Purpose: {ND}",
        f"Property type: {r['property_type'] or ND}",
        f"Bedrooms: {r['bedrooms'] if r['bedrooms'] is not None else ND}",
        f"Bathrooms: {r['bathrooms'] if r['bathrooms'] is not None else ND}",
        f"Area: {r['area_text']}",
        f"Price: {price}",
        f"Location: {loc}",
    ]
    if r["description"]:
        lines.append(f"Description: {r['description']}")
    lines.append(f"URL: {r['url']}")
    meta = dict(source="wasalt", country=r["country"], city=r["city"],
                property_type=[r["property_type"]] if r["property_type"] else [], purpose=r["purpose"],
                bedrooms=r["bedrooms"], missing_fields=r["missing_fields"])
    return {**_base("listing", r["id"], r["url"], **meta),
            "id": r["id"], "section": "Listing", "chunk_kind": "listing", "text": "\n".join(lines)}


def company_chunks(page: dict) -> list[dict]:
    label = page["title"] or page["page"]
    meta = dict(source="darglobal", page=page["page"])
    out = []
    if page.get("faq"):
        for i, qa in enumerate(page["faq"]):
            text = f"[DarGlobal | FAQ | {qa.get('topic') or 'General'}]\nQ: {qa['question']}\nA: {qa['answer']}"
            out.append({**_base("company", page["id"], page["url"], **meta), "id": f"{page['id']}#faq{i}",
                        "section": qa["question"], "chunk_kind": "faq", "text": text})
        return out
    for i, (h, t) in enumerate(prep_sections(page["sections"])):
        out.append({**_base("company", page["id"], page["url"], **meta), "id": f"{page['id']}#s{i}",
                    "section": h, "chunk_kind": "section", "text": f"[DarGlobal | {label} | {h}]\n{t}"})
    return out


def news_chunks(n: dict) -> list[dict]:
    body = "\n".join(clean_text(s["text"]) for s in n["sections"] if s["text"].strip())
    header = f"[News | {n['date']} | {n['kind']}]\n{n['title']}"
    parts = split_long(body, NEWS_MAX_TOKENS) if body else [""]
    meta = dict(source="darglobal", date=n["date"], kind=n["kind"], title=n["title"])
    return [{**_base("news", n["id"], n["url"], **meta), "id": f"{n['id']}#p{i}", "section": n["title"],
             "chunk_kind": "news", "text": f"{header}\n{part}".strip()}
            for i, part in enumerate(parts)]


SECURITY_REPORT: dict = {}


def build_chunks() -> list[dict]:
    chunks: list[dict] = []
    for p in load("projects"):
        chunks += project_chunks(p)
    for r in load("listings"):
        chunks.append(listing_chunk(r))
    for c in load("company"):
        chunks += company_chunks(c)
    for n in load("news"):
        chunks += news_chunks(n)
    # RAG security at ingestion: only allowlisted https sources; neutralise hidden text / injection phrases.
    kept, report = [], {"dropped_domain": [], "flagged": {}}
    for c in chunks:
        if not security.is_allowed_url(c["url"]):
            report["dropped_domain"].append(c["id"])
            continue
        c["text"], flags = security.scan_text(c["text"])
        if flags:
            report["flagged"][c["id"]] = flags
        kept.append(c)
    chunks = kept
    SECURITY_REPORT.update(report)
    ids = [c["id"] for c in chunks]
    assert len(ids) == len(set(ids)), "duplicate chunk ids"
    for c in chunks:
        c["n_tokens"] = ntokens(c["text"])
    return chunks


def main() -> None:
    chunks = build_chunks()
    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    by_type: dict[str, list[int]] = {}
    for c in chunks:
        by_type.setdefault(c["doc_type"], []).append(c["n_tokens"])
    for t, toks in by_type.items():
        toks.sort()
        print(f"{t:8} chunks={len(toks):4} tokens median={toks[len(toks) // 2]:4} max={toks[-1]:4} total={sum(toks)}")
    print(f"wrote {len(chunks)} chunks -> {CHUNKS_PATH}")
    print(f"security: dropped (domain) {len(SECURITY_REPORT['dropped_domain'])}, "
          f"flagged {len(SECURITY_REPORT['flagged'])} {SECURITY_REPORT['flagged'] or ''}")


if __name__ == "__main__":
    main()
