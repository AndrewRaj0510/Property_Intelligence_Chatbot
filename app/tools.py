"""Agent tools: typed (Pydantic) args, compact text output for the LLM, plus sources for citation checks.

| Tool            | Store                                   |
|-----------------|-----------------------------------------|
| search_listings | SQLite `listings` (parameterised SQL)   |
| search_projects | Qdrant doc_type=project (hybrid RRF)    |
| get_project     | SQLite `projects` + all Qdrant chunks   |
| company_info    | SQLite `company_facts` + Qdrant company |
| search_news     | SQLite `news` + Qdrant doc_type=news    |

The LLM never writes SQL. Missing fields are rendered as "not published in our data", never 0 / empty.
"""
import difflib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from app import config, retrieval

MISSING = "not published in our data"


@dataclass
class ToolOutput:
    text: str                                               # what the LLM sees
    sources: list[dict] = field(default_factory=list)       # {url, title, text} for finalize checks
    timings_ms: dict[str, float] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)                # hit counts etc. for tracing


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{config.SQLITE_PATH}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _v(x, suffix: str = "") -> str:
    return MISSING if x is None or x == "" else f"{x}{suffix}"


def _hits_output(hits: list[retrieval.Hit], timings: dict, empty_msg: str) -> ToolOutput:
    if not hits:
        return ToolOutput(empty_msg, [], timings, {"hits": 0})
    blocks, sources = [], []
    for i, h in enumerate(hits, 1):
        title = h.payload.get("project_name") or h.payload.get("title") or h.payload.get("section") or h.doc_type
        if h.doc_type == "listing":
            title = f"Wasalt #{h.id.split(':', 1)[-1]}: {h.payload.get('title') or title}"
        label = {"project": "NAME", "listing": "NAME", "news": "TITLE"}.get(h.doc_type)
        head = f"{label}: {title}\n" if label else ""
        blocks.append(f"[{i}] {head}{h.text}\nURL: {h.url}")
        sources.append({"url": h.url, "title": title, "text": h.text})
    return ToolOutput("\n\n".join(blocks), sources, timings, {"hits": len(hits)})


# ---------------------------------------------------------------- search_listings
class SearchListingsArgs(BaseModel):
    """Filter, sort and count Wasalt (Saudi Arabia) property listings. Use for structured questions:
    'apartments for rent', '3+ bedroom villas for sale', 'how many land plots'. Note: price, city and
    district are not available in the scraped Wasalt data; such filters are reported as unsupported."""
    purpose: Literal["sale", "rent"] | None = Field(None, description="sale or rent")
    property_type: Literal["apartment", "villa", "land", "floor", "building", "rest house"] | None = None
    min_beds: int | None = Field(None, ge=0, le=20)
    max_beds: int | None = Field(None, ge=0, le=20)
    city: str | None = Field(None, description="Saudi city, e.g. Riyadh")
    district: str | None = None
    min_price: float | None = Field(None, ge=0, description="SAR")
    max_price: float | None = Field(None, ge=0, description="SAR")
    sort_by: Literal["price_asc", "price_desc", "area_desc", "beds_desc", "newest"] = "newest"
    limit: int = Field(5, ge=1, le=10)


_SORT = {"price_asc": "price IS NULL, price ASC", "price_desc": "price IS NULL, price DESC",
         "area_desc": "area_sqm IS NULL, area_sqm DESC", "beds_desc": "bedrooms IS NULL, bedrooms DESC",
         "newest": "listed_lastmod DESC"}
_SORT_COLUMN = {"price_asc": "price", "price_desc": "price", "area_desc": "area_sqm"}


def listing_name(r: sqlite3.Row) -> str:
    """Unique, human-readable name. Wasalt detail pages (real titles) are blocked by Cloudflare, so the name
    is built from the URL facts plus the Wasalt listing number, which makes it unique and checkable."""
    bits = [r["title"]]
    if r["facing"]:
        bits.append(f"facing {r['facing']}")
    if r["street_width_m"]:
        bits.append(f"{r['street_width_m']} m wide street")
    if r["floors"]:
        bits.append(f"{r['floors']} floors")
    return f"Wasalt #{r['id'].split(':', 1)[-1]}: {', '.join(bits)}"


def _render_listing(r: sqlite3.Row) -> str:
    price = MISSING if r["price"] is None else f"{r['price']:,.0f} {r['currency'] or ''}".strip()
    if r["price"] is not None and r["rent_period"]:
        price += f" / {r['rent_period']}"
    return (f"NAME: {listing_name(r)} | purpose: {r['purpose']} | type: {r['property_type']} | bedrooms: {_v(r['bedrooms'])} | "
            f"bathrooms: {_v(r['bathrooms'])} | area: {r['area_text'] or MISSING} | price: {price} | "
            f"city: {_v(r['city'])} | district: {_v(r['district'])} | listed: {_v(r['listed_lastmod'])}\nURL: {r['url']}")


def search_listings(args: SearchListingsArgs) -> ToolOutput:
    t0 = time.perf_counter()
    con = _db()
    # A filter on a column with no data at all would silently return nothing: report it instead.
    have = dict(con.execute("SELECT COUNT(price) AS price, COUNT(city) AS city, COUNT(district) AS district, "
                            "COUNT(area_sqm) AS area FROM listings").fetchone())
    unsupported: list[str] = []
    where, params = [], []
    for name, col, op, val in [("purpose", "purpose", "=", args.purpose),
                               ("property_type", "property_type", "=", args.property_type),
                               ("min_beds", "bedrooms", ">=", args.min_beds),
                               ("max_beds", "bedrooms", "<=", args.max_beds),
                               ("city", "city", "=", args.city), ("district", "district", "=", args.district),
                               ("min_price", "price", ">=", args.min_price),
                               ("max_price", "price", "<=", args.max_price)]:
        if val is None:
            continue
        if col in ("price", "city", "district") and not have[col]:
            unsupported.append(name)
            continue
        if op == "=":
            where.append(f"LOWER({col}) = LOWER(?)")
        else:
            where.append(f"{col} {op} ?")
        params.append(val)
    sort = args.sort_by
    if sort in _SORT_COLUMN and not have["price" if "price" in sort else "area"]:
        unsupported.append(f"sort_by={sort}")
        sort = "newest"
    wsql = f"WHERE {' AND '.join(where)}" if where else ""
    total = con.execute(f"SELECT COUNT(*) FROM listings {wsql}", params).fetchone()[0]
    rows = con.execute(f"SELECT * FROM listings {wsql} ORDER BY {_SORT[sort]} LIMIT ?", [*params, args.limit]).fetchall()
    con.close()

    head = [f"Matching Wasalt listings in our sample: {total} (showing {len(rows)})."]
    if unsupported:
        head.append(json.dumps({"unsupported_filters": unsupported,
                                "reason": "price, city and district are not available in the scraped Wasalt data; "
                                          "results ignore these filters"}))
    body = [f"[{i}] {_render_listing(r)}" for i, r in enumerate(rows, 1)]
    sources = [{"url": r["url"], "title": listing_name(r), "text": _render_listing(r)} for r in rows]
    text = "\n".join(head) + ("\n\n" + "\n\n".join(body) if body else "\nNo listings match.")
    sources.insert(0, {"url": "", "title": "listing count", "text": head[0]})
    return ToolOutput(text, sources, {"sqlite": (time.perf_counter() - t0) * 1000},
                      {"total": total, "unsupported_filters": unsupported})


# ---------------------------------------------------------------- shared normalisation (projects)
# Users say "UAE", "KSA", "Marbella", "villas", "off-plan"... the data says "United Arab Emirates", "Saudi Arabia",
# "Benahavis"/"Casares", "villa", "Under Development". Everything is mapped onto the stored values here.
_COUNTRY = {"uae": "United Arab Emirates", "emirates": "United Arab Emirates", "united arab emirates": "United Arab Emirates",
            "dubai": "United Arab Emirates", "ras al khaimah": "United Arab Emirates", "rak": "United Arab Emirates",
            "ksa": "Saudi Arabia", "saudi": "Saudi Arabia", "saudi arabia": "Saudi Arabia", "kingdom of saudi arabia": "Saudi Arabia",
            "uk": "United Kingdom", "united kingdom": "United Kingdom", "england": "United Kingdom", "britain": "United Kingdom",
            "great britain": "United Kingdom", "oman": "Oman", "sultanate of oman": "Oman", "qatar": "Qatar", "spain": "Spain"}
_REGION = {"gcc": ["United Arab Emirates", "Saudi Arabia", "Oman", "Qatar"],
           "middle east": ["United Arab Emirates", "Saudi Arabia", "Oman", "Qatar"],
           "gulf": ["United Arab Emirates", "Saudi Arabia", "Oman", "Qatar"],
           "europe": ["Spain", "United Kingdom"]}
_CITY = {"marbella": ["Benahavis", "Casares"], "costa del sol": ["Benahavis", "Casares"], "benahavis": ["Benahavis"],
         "casares": ["Casares"], "ras al khaimah": ["Al Marjan Island"], "rak": ["Al Marjan Island"],
         "al marjan": ["Al Marjan Island"], "al marjan island": ["Al Marjan Island"], "aida": ["Muscat"],
         "muscat": ["Muscat"], "riyadh": ["Riyadh", "Wadi Safar"], "wadi safar": ["Wadi Safar"], "diriyah": ["Wadi Safar"],
         "jeddah": ["Jeddah"], "jedda": ["Jeddah"], "doha": ["Doha"], "simaisma": ["Simaisma"], "dubai": ["Dubai"],
         "london": ["London"]}
_PTYPE = {"flat": "apartment", "apartment": "apartment", "apt": "apartment", "residence": "apartment",
          "villa": "villa", "house": "villa", "home": "villa", "penthouse": "penthouse", "townhouse": "townhouse",
          "town house": "townhouse", "mansion": "mansion", "plot": "plot", "land": "plot", "studio": "studio",
          "hotel room": "hotel room", "hotel apartment": "hotel room", "serviced apartment": "hotel room"}
_STATUS = {"ready": ["Ready", "Completed"], "completed": ["Completed", "Ready"], "ready to move": ["Ready", "Completed"],
           "handed over": ["Completed", "Ready"], "off plan": ["Under Development"], "off-plan": ["Under Development"],
           "under construction": ["Under Development"], "under development": ["Under Development"],
           "new launch": ["Under Development"], "sold out": ["Sold Out"], "sold": ["Sold Out"]}


def _norm(v: str | None) -> str:
    return " ".join((v or "").lower().replace("_", " ").split())


def _singular(v: str) -> str:
    if v.endswith("ies"):
        return v[:-3] + "y"
    return v[:-1] if v.endswith("s") and not v.endswith("ss") else v


_SPLIT = re.compile(r"\s*(?:/|,|;|&|\||\bor\b|\band\b)\s*", re.I)


def _parts(v: str | None) -> list[str]:
    """Models often send combined values ("Saudi Arabia/KSA", "villa or townhouse"): split and map each part."""
    return [p for p in (_norm(x) for x in _SPLIT.split(v or "")) if p]


def _uniq(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(xs))


def norm_countries(v: str | None) -> list[str] | None:
    out: list[str] = []
    for p in _parts(v):
        out += _REGION.get(p) or [_COUNTRY.get(p, p)]
    return _uniq(out) or None


def norm_cities(v: str | None) -> list[str] | None:
    out: list[str] = []
    for p in _parts(v):
        out += _CITY.get(p, [p])
    return _uniq(out) or None


def norm_ptypes(v: str | None) -> list[str] | None:
    return _uniq([_PTYPE.get(_singular(p), _singular(p)) for p in _parts(v)]) or None


def norm_statuses(v: str | None) -> list[str] | None:
    out: list[str] = []
    for p in _parts(v):
        out += _STATUS.get(p, [p])
    return _uniq(out) or None


def norm_ptype(v: str | None) -> str | None:  # search_projects keeps a single type
    t = norm_ptypes(v)
    return t[0] if t else None


# ---------------------------------------------------------------- list_projects
class ListProjectsArgs(BaseModel):
    """COMPLETE list / count of DarGlobal projects from the database, with exact filters. Use for 'list all',
    'which projects are in X', 'how many', 'all Trump projects', 'projects that have villas', 'completed /
    off-plan projects'. Returns every match (not a top-k search). For questions about amenities, views or
    design use search_projects instead (or both)."""
    country: str | None = Field(None, description="ONE country or region, e.g. 'Saudi Arabia', 'UAE', 'Oman', 'Qatar', 'Spain', 'United Kingdom', 'GCC', 'Europe'")
    city: str | None = Field(None, description="e.g. Dubai, Riyadh, Jeddah, Muscat, Doha, London, Marbella, Ras Al Khaimah")
    brand: str | None = Field(None, description="brand partner, e.g. Trump, Missoni, Pagani, Aston Martin, Fendi, W")
    branded_only: bool | None = Field(None, description="true = only branded projects, false = only non-branded")
    property_type: str | None = Field(None, description="ONE of: apartment, villa, penthouse, townhouse, mansion, plot, studio, hotel room")
    status: str | None = Field(None, description="ONE of: 'Under Development' (= off-plan), 'Completed' (= ready), 'Sold Out'")
    collection: str | None = Field(None, description="e.g. 'One of One'")
    name_contains: str | None = Field(None, description="substring of the project name")
    min_price: float | None = Field(None, ge=0)
    max_price: float | None = Field(None, ge=0)


def list_projects(args: ListProjectsArgs) -> ToolOutput:
    t0 = time.perf_counter()
    con = _db()
    rows = con.execute("SELECT * FROM projects ORDER BY country, city, name").fetchall()
    con.close()
    applied, unsupported = [], []
    countries, cities = norm_countries(args.country), norm_cities(args.city)
    ptypes, statuses, brand = norm_ptypes(args.property_type), norm_statuses(args.status), _norm(args.brand)
    # Values the data doesn't contain are reported explicitly, so "Egypt" never comes back as a bare "0 projects".
    known = {"country": {(r["country"] or "").lower() for r in rows}, "city": {(r["city"] or "").lower() for r in rows},
             "status": {(r["status"] or "").lower() for r in rows},
             "property_type": {t.lower() for r in rows for t in json.loads(r["property_types"] or "[]")}}
    unknown = {f: [v for v in vals if v.lower() not in known[f]]
               for f, vals in [("country", countries or []), ("city", cities or []), ("status", statuses or []),
                               ("property_type", ptypes or [])]}
    unknown = {f: v for f, v in unknown.items() if v}

    def keep(r: sqlite3.Row) -> bool:
        brands = [b.lower() for b in json.loads(r["brand_partner"] or "[]")]
        types = [t.lower() for t in json.loads(r["property_types"] or "[]")]
        if countries and (r["country"] or "").lower() not in {c.lower() for c in countries}:
            return False
        if cities and (r["city"] or "").lower() not in {c.lower() for c in cities}:
            return False
        if brand and not any(brand == b or brand in b.split() or b.startswith(brand) for b in brands):
            return False
        if args.branded_only is not None and bool(brands) != args.branded_only:
            return False
        if ptypes and not any(t in types for t in ptypes):
            return False
        if statuses and (r["status"] or "").lower() not in {s.lower() for s in statuses}:
            return False
        if args.collection and _norm(args.collection) not in _norm(r["collection"]):
            return False
        if args.name_contains and _norm(args.name_contains) not in _norm(r["name"]):
            return False
        return True

    for name, val in [("country", countries), ("city", cities), ("brand", args.brand), ("branded_only", args.branded_only),
                      ("property_type", ptypes), ("status", statuses), ("collection", args.collection),
                      ("name_contains", args.name_contains)]:
        if val is not None and val != "":
            applied.append(f"{name}={val}")
    if args.min_price is not None or args.max_price is not None:
        unsupported = [n for n in ("min_price", "max_price") if getattr(args, n) is not None]
    matches = [r for r in rows if keep(r)]

    head = [f"DarGlobal projects matching [{', '.join(applied) or 'no filter'}]: {len(matches)} of {len(rows)} total. "
            f"This is the COMPLETE list."]
    if unsupported:
        head.append(json.dumps({"unsupported_filters": unsupported,
                                "reason": "no DarGlobal project publishes a starting price in the scraped data; "
                                          "price filters were not applied"}))
    if unknown:
        head.append(f"NOTE: these filter values do not exist in DarGlobal's project data: {json.dumps(unknown)}. "
                    "Tell the user DarGlobal has no projects matching that value (per our data) and list what exists.")
    if not matches:
        vals = lambda col: sorted({r[col] for r in rows if r[col]})  # noqa: E731
        head.append(f"No match. Values that exist: countries={vals('country')}; cities={vals('city')}; "
                    f"statuses={vals('status')}; brands={sorted({b for r in rows for b in json.loads(r['brand_partner'] or '[]')})}; "
                    f"property types={sorted({t for r in rows for t in json.loads(r['property_types'] or '[]')})}.")
    lines, sources = [], []
    compact = len(matches) > 15  # long lists: one short line per project so all of them fit the context
    if compact:
        head.append("Long list: show it compactly (e.g. grouped by country, one line per project: name + URL).")
    for i, r in enumerate(matches, 1):
        brands = ", ".join(json.loads(r["brand_partner"] or "[]")) or "none"
        types = ", ".join(json.loads(r["property_types"] or "[]")) or MISSING
        if compact:
            line = (f"[{i}] NAME: {r['name']} | {_v(r['city'])}, {_v(r['country'])} | brand: {brands} | "
                    f"status: {_v(r['status'])} | URL: {r['url']}")
        else:
            line = (f"[{i}] NAME: {r['name']} | {_v(r['city'])}, {_v(r['country'])} | brand: {brands} | types: {types} | "
                    f"status: {_v(r['status'])} | starting price: {r['starting_price_raw'] or MISSING}"
                    + (f" | collection: {r['collection']}" if r["collection"] else "") + f"\nURL: {r['url']}")
        lines.append(line)
        sources.append({"url": r["url"], "title": r["name"], "text": line})
    sources.insert(0, {"url": "", "title": "project count", "text": head[0]})
    return ToolOutput("\n".join(head) + ("\n\n" + "\n".join(lines) if lines else ""), sources,
                      {"sqlite": (time.perf_counter() - t0) * 1000}, {"total": len(matches), "applied": applied})


# ---------------------------------------------------------------- search_projects
class SearchProjectsArgs(BaseModel):
    """Semantic + keyword search over DarGlobal project pages (amenities, views, design, location,
    branded residences, golf, beachfront...). Returns the most relevant passages, NOT a complete list:
    for 'list all / how many / which projects are in X' use list_projects."""
    query: str = Field(..., min_length=2)
    country: str | None = Field(None, description="e.g. UAE, Saudi Arabia, Oman, Qatar, Spain, United Kingdom")
    city: str | None = None
    brand: str | None = Field(None, description="brand partner, e.g. Trump, Missoni, Pagani, Elie Saab")
    property_type: str | None = Field(None, description="e.g. villa, apartment, penthouse")
    k: int = Field(6, ge=1, le=10)


def search_projects(args: SearchProjectsArgs, mode: retrieval.Mode = "hybrid") -> ToolOutput:
    brand = _norm(args.brand) or None
    if brand:  # "fendi" -> "fendi casa", "w" -> W Hotels / W Residences: map onto the stored brand names
        con = _db()
        all_brands = {b for (bp,) in con.execute("SELECT brand_partner FROM projects") for b in json.loads(bp or "[]")}
        con.close()
        brand = [b for b in all_brands if brand == b.lower() or brand in b.lower().split() or b.lower().startswith(brand)] or brand
    res = retrieval.search(args.query, mode=mode, k=args.k, doc_type="project", country=norm_countries(args.country),
                           city=norm_cities(args.city), brand=brand, property_type=norm_ptype(args.property_type))
    out = _hits_output(res.hits, res.timings_ms, "No DarGlobal project matched these filters.")
    if not res.hits and any([args.country, args.city, args.brand, args.property_type]):
        out.text += " Try again without some filters."
    return out


# ---------------------------------------------------------------- get_project
class GetProjectArgs(BaseModel):
    """Full details of ONE DarGlobal project by name (e.g. 'DG1', 'Trump Tower Jeddah', 'Aida').
    Use for 'tell me about X' or when a specific project's price/status/amenities is asked."""
    name: str = Field(..., min_length=2)


def _match_project(con: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    rows = con.execute("SELECT * FROM projects").fetchall()
    q = name.lower().strip()
    for r in rows:
        if r["name"].lower() == q:
            return r
    contains = [r for r in rows if q in r["name"].lower() or r["name"].lower() in q]
    if contains:
        return min(contains, key=lambda r: abs(len(r["name"]) - len(q)))
    names = {r["name"].lower(): r for r in rows}
    best = difflib.get_close_matches(q, list(names), n=1, cutoff=0.5)
    return names[best[0]] if best else None


def get_project(args: GetProjectArgs) -> ToolOutput:
    t0 = time.perf_counter()
    con = _db()
    r = _match_project(con, args.name)
    names = [x[0] for x in con.execute("SELECT name FROM projects ORDER BY name")]
    con.close()
    t = {"sqlite": (time.perf_counter() - t0) * 1000}
    if r is None:
        return ToolOutput(f"No DarGlobal project named '{args.name}'. Known projects: {', '.join(names)}", [], t)
    price = r["starting_price_raw"] or (f"{r['starting_price']:,.0f} {r['currency']}" if r["starting_price"] else MISSING)
    card = (f"NAME: {r['name']} | collection: {_v(r['collection'])} | {_v(r['city'])}, {_v(r['country'])} | "
            f"brand partner(s): {', '.join(json.loads(r['brand_partner'] or '[]')) or MISSING} | "
            f"property types: {', '.join(json.loads(r['property_types'] or '[]')) or MISSING} | "
            f"starting price: {price} | status: {_v(r['status'])}\nURL: {r['url']}")
    sources = [{"url": r["url"], "title": r["name"], "text": card}]
    parts = [card]
    t0 = time.perf_counter()
    try:
        chunks = [h for h in retrieval.get_chunks(r["id"]) if h.payload.get("chunk_kind") != "summary"]
    except Exception:  # noqa: BLE001 - Qdrant down: the SQLite card still answers basics
        chunks = []
    t["qdrant"] = (time.perf_counter() - t0) * 1000
    for h in chunks:
        parts.append(h.text)
        sources.append({"url": h.url, "title": r["name"], "text": h.text})
    return ToolOutput("\n\n".join(parts) + f"\nURL: {r['url']}", sources, t, {"project_id": r["id"], "chunks": len(chunks)})


# ---------------------------------------------------------------- company_info
class CompanyInfoArgs(BaseModel):
    """DarGlobal company information: who they are, offices and addresses, contact emails, brand partners,
    countries of operation, number of projects, FAQ (how to buy, payment plans, golden visa), hospitality."""
    query: str = Field(..., min_length=2)


def company_info(args: CompanyInfoArgs, mode: retrieval.Mode = "hybrid") -> ToolOutput:
    t0 = time.perf_counter()
    con = _db()
    facts = con.execute("SELECT key, value, url FROM company_facts WHERE key NOT IN ('offices', 'contact_emails') "
                        "ORDER BY key").fetchall()
    con.close()
    t = {"sqlite": (time.perf_counter() - t0) * 1000}
    fact_text = "\n".join(f"- {k}: {v} (URL: {u})" for k, v, u in facts)
    sources = [{"url": u, "title": k, "text": f"{k}: {v}"} for k, v, u in facts]
    try:
        res = retrieval.search(args.query, mode=mode, k=4, doc_type="company")
        hits_out = _hits_output(res.hits, res.timings_ms, "No matching company page section.")
        t.update(res.timings_ms)
    except Exception:  # noqa: BLE001
        hits_out = ToolOutput("Company page search unavailable.")
    text = f"EXACT COMPANY FACTS (database):\n{fact_text}\n\nRELEVANT COMPANY PAGE SECTIONS:\n{hits_out.text}"
    return ToolOutput(text, sources + hits_out.sources, t, {"hits": hits_out.meta.get("hits", 0)})


# ---------------------------------------------------------------- search_news
class SearchNewsArgs(BaseModel):
    """DarGlobal press releases, news and blog posts published 1 Jan - 30 Sep 2026. Use for announcements,
    launches, financial results, partnerships, contractor or construction appointments, awards, sales milestones,
    'latest news'. Project pages do not contain these: check here too. Dates are ISO (YYYY-MM-DD)."""
    query: str = Field(..., min_length=2)
    date_from: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    latest_first: bool = Field(False, description="true when the user asks for the latest / most recent news")


def search_news(args: SearchNewsArgs, mode: retrieval.Mode = "hybrid") -> ToolOutput:
    t0 = time.perf_counter()
    con = _db()
    q, p = "SELECT kind, title, date, url FROM news", []
    if args.date_from or args.date_to:
        q += " WHERE date >= ? AND date <= ?"
        p = [args.date_from or "0000-00-00", args.date_to or "9999-12-31"]
    recent = con.execute(q + " ORDER BY date DESC LIMIT 8", p).fetchall()
    con.close()
    t = {"sqlite": (time.perf_counter() - t0) * 1000}
    index = "\n".join(f"- {d} [{k}] {ti} (URL: {u})" for k, ti, d, u in recent)
    sources = [{"url": u, "title": ti, "text": f"{d} {k} {ti}"} for k, ti, d, u in recent]
    df = f"{args.date_from}T00:00:00Z" if args.date_from else None
    dt = f"{args.date_to}T23:59:59Z" if args.date_to else None
    res = retrieval.search(args.query, mode=mode, k=5, doc_type="news", date_from=df, date_to=dt)
    hits = res.hits
    if args.latest_first:
        hits = sorted(hits, key=lambda h: h.payload.get("date", ""), reverse=True)
    t.update(res.timings_ms)
    ho = _hits_output(hits, t, "No matching article text.")
    text = f"MOST RECENT ARTICLES IN RANGE (database):\n{index or 'none'}\n\nMATCHING ARTICLE TEXT:\n{ho.text}"
    return ToolOutput(text, sources + ho.sources, t, {"hits": len(hits)})


# ---------------------------------------------------------------- registry
TOOLS: dict[str, tuple[type[BaseModel], Callable[..., ToolOutput]]] = {
    "search_listings": (SearchListingsArgs, search_listings),
    "list_projects": (ListProjectsArgs, list_projects),
    "search_projects": (SearchProjectsArgs, search_projects),
    "get_project": (GetProjectArgs, get_project),
    "company_info": (CompanyInfoArgs, company_info),
    "search_news": (SearchNewsArgs, search_news),
}
_MODE_AWARE = {"search_projects", "company_info", "search_news"}


_SCHEMAS: list[dict] | None = None


def openai_schemas() -> list[dict]:
    """Built once and reused so the prompt prefix (system + tools) is byte-identical every call (cache-friendly)."""
    global _SCHEMAS
    if _SCHEMAS is not None:
        return _SCHEMAS
    out = []
    for name, (model, _) in TOOLS.items():
        schema = model.model_json_schema()
        desc = " ".join((schema.pop("description", "") or "").split())
        schema.pop("title", None)
        out.append({"type": "function", "function": {"name": name, "description": desc, "parameters": schema}})
    _SCHEMAS = out
    return out


def run_tool(name: str, raw_args: str | dict, mode: retrieval.Mode = "hybrid") -> ToolOutput:
    """Validate args and run. Errors come back as text so the agent can correct itself."""
    if name not in TOOLS:
        return ToolOutput(f"ERROR: unknown tool '{name}'. Available: {', '.join(TOOLS)}")
    model, fn = TOOLS[name]
    try:
        data = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        data = {k: v for k, v in data.items() if v is not None and v != ""}
        args = model.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        return ToolOutput(f"ERROR: invalid arguments for {name}: {e}")
    try:
        return fn(args, mode=mode) if name in _MODE_AWARE else fn(args)
    except Exception as e:  # noqa: BLE001
        return ToolOutput(f"ERROR: {name} failed ({type(e).__name__}). Tell the user this data source is temporarily unavailable.")
