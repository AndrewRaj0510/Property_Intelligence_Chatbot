"""Build data/listings.db from data/clean/*.jsonl (structured store for exact filters and facts).

Tables: listings, projects, company_facts, news. Rebuilt from scratch on every run.
Usage: python -m ingestion.build_sqlite
"""
import json
import re
import sqlite3

from app.config import SQLITE_PATH
from ingestion.io_utils import load

SCHEMA = """
CREATE TABLE listings (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, purpose TEXT, property_type TEXT,
  price REAL, currency TEXT, rent_period TEXT, bedrooms INTEGER, bathrooms INTEGER,
  area_sqm REAL, area_status TEXT, area_text TEXT, city TEXT, district TEXT, country TEXT,
  description TEXT, missing_fields TEXT, listed_lastmod TEXT, url TEXT NOT NULL, scraped_at TEXT NOT NULL
);
CREATE INDEX ix_listings_filter ON listings(city, property_type, purpose, price, bedrooms);
CREATE INDEX ix_listings_type ON listings(property_type);
CREATE INDEX ix_listings_price ON listings(price);
CREATE INDEX ix_listings_beds ON listings(bedrooms);

CREATE TABLE projects (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, name TEXT NOT NULL, collection TEXT,
  country TEXT, city TEXT, brand_partner TEXT, property_types TEXT,
  starting_price REAL, currency TEXT, starting_price_raw TEXT, status TEXT, summary TEXT,
  url TEXT NOT NULL, scraped_at TEXT NOT NULL
);
CREATE INDEX ix_projects_loc ON projects(country, city);
CREATE INDEX ix_projects_price ON projects(starting_price);

CREATE TABLE company_facts (key TEXT PRIMARY KEY, value TEXT NOT NULL, url TEXT NOT NULL);

CREATE TABLE news (id TEXT PRIMARY KEY, kind TEXT, title TEXT, date TEXT, url TEXT);
CREATE INDEX ix_news_date ON news(date);
"""

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def company_facts(company: list[dict], projects: list[dict]) -> list[tuple[str, str, str]]:
    facts: dict[str, tuple[str, str]] = {}
    for page in company:
        url = page["url"]
        for sec in page["sections"]:
            if sec.get("key") == "OurOffices":
                # Alternating "CITY, COUNTRY" header lines (upper case) and address lines.
                offices, current = [], None
                for line in sec["text"].splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.isupper() and "," in line:
                        current = {"office": line, "address": []}
                        offices.append(current)
                    elif current is not None:
                        current["address"].append(line)
                for o in offices:
                    addr = "; ".join(o["address"])
                    facts[f"office_{o['office'].split(',')[0].strip().lower().replace(' ', '_')}"] = (f"{o['office']}: {addr}", url)
                facts["offices"] = (json.dumps([f"{o['office']}: {'; '.join(o['address'])}" for o in offices], ensure_ascii=False), url)
            elif sec.get("key") == "DivisionContacts":
                lines = [ln.strip() for ln in sec["text"].splitlines() if ln.strip()]
                for label, value in zip(lines, lines[1:]):
                    if _EMAIL.fullmatch(value) and not _EMAIL.search(label):
                        facts[f"contact_email_{label.lower().replace(' & ', '_').replace(' ', '_')}"] = (value, url)
        if page.get("contact"):
            facts["contact_emails"] = (json.dumps(page["contact"].get("emails", [])), url)
    brands = sorted({b for p in projects for b in p["brand_partners"]})
    facts["brand_partners"] = (json.dumps(brands), "https://darglobal.co.uk/partners")
    countries = sorted({p["country"] for p in projects if p["country"]})
    facts["countries_of_operation"] = (json.dumps(countries), "https://darglobal.co.uk/projects")
    facts["project_count"] = (str(len(projects)), "https://darglobal.co.uk/projects")
    return [(k, v, u) for k, (v, u) in sorted(facts.items())]


def main() -> None:
    projects, listings = load("projects"), load("listings")
    company, news = load("company"), load("news")
    SQLITE_PATH.unlink(missing_ok=True)
    con = sqlite3.connect(SQLITE_PATH)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["id"], r["source"], r["title"], r["purpose"], r["property_type"], r["price"], r["currency"],
          r["rent_period"], r["bedrooms"], r["bathrooms"], r["area_sqm"], r["area_status"], r["area_text"],
          r["city"], r["district"], r["country"], r["description"], json.dumps(r["missing_fields"]),
          r["listed_lastmod"], r["url"], r["scraped_at"]) for r in listings],
    )
    con.executemany(
        "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["id"], r["source"], r["name"], r["collection"], r["country"], r["city"],
          json.dumps(r["brand_partners"]), json.dumps(r["property_types"]), r["starting_price"], r["currency"],
          r["starting_price_raw"], r["status"], r["summary"], r["url"], r["scraped_at"]) for r in projects],
    )
    facts = company_facts(company, projects)
    con.executemany("INSERT INTO company_facts VALUES (?,?,?)", facts)
    con.executemany("INSERT INTO news VALUES (?,?,?,?,?)",
                    [(r["id"], r["kind"], r["title"], r["date"], r["url"]) for r in news])
    con.commit()
    for t in ("listings", "projects", "company_facts", "news"):
        print(f"{t:14} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    con.close()
    print(f"wrote {SQLITE_PATH}")


if __name__ == "__main__":
    main()
