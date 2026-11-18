# Property Intelligence Chatbot

An agentic RAG chatbot that answers natural-language questions about **DarGlobal** projects and **Wasalt** (Saudi Arabia) property listings, grounded in scraped public data, with a source link for every answer.

> **Status:** work in progress. Phase 0 (setup) and Phase 1 (data) are complete. Indexing, agent, UI and deployment are next.

## How it works (planned)

Property data is half **structured** (price, bedrooms, city) and half **unstructured** (amenities, design partners, lifestyle). Vector search alone cannot reliably answer *"cheapest 3-bed in Riyadh under 2M SAR"*, so the agent has two kinds of tools:

| Tool | Backed by | Answers |
|---|---|---|
| `search_listings` | SQLite, parameterised queries from typed arguments (the LLM never writes SQL) | Filters, price ranges, sorting, counts |
| `search_knowledge` | Qdrant hybrid search: dense embeddings + BM25, fused with Reciprocal Rank Fusion | Amenities, brands, locations, descriptions |
| `get_project` | SQLite + Qdrant | "Tell me about project X" |

A LangGraph agent picks the tools (at most 3 rounds), and a final step checks that every factual answer cites a source URL; if no evidence was found, it answers "I don't have that information" instead of guessing.

## Stack

| Layer | Choice |
|---|---|
| LLM | OpenRouter free models with a fallback list |
| Embeddings | `nvidia/nemotron-3.5-embed-1b:free` via OpenRouter |
| Vector store | Qdrant Cloud (free tier) |
| Structured store | SQLite, baked into the Docker image |
| Orchestration | LangGraph |
| UI | Streamlit |
| Observability | Langfuse Cloud |
| Hosting | Docker on Render (free tier) |

## Getting started

Requires Python 3.11.

```bash
git clone https://github.com/AndrewRaj0510/Property_Intelligence_Chatbot.git
cd Property_Intelligence_Chatbot

python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in your keys
```

### Environment variables

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `OPENROUTER_MODELS` | Comma-separated chat models, primary first; must be `:free` and support tool calling |
| `OPENROUTER_EMBED_MODEL` | Embedding model |
| `QDRANT_URL`, `QDRANT_API_KEY` | Qdrant Cloud cluster |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Langfuse tracing |

### Check your setup

```bash
python scripts/smoke_test.py              # OpenRouter tool calling (each model), embeddings, Qdrant, Langfuse
python scripts/smoke_test.py --list-free  # list free OpenRouter models that support tool calling
```

The free model list on OpenRouter changes often; rerun the smoke test before relying on a model.

## Data

Rebuild the snapshot (about 2 minutes, 1 request/second):

```bash
python -m scraper.darglobal      # -> data/raw/darglobal.jsonl
python -m scraper.darglobal_content   # -> company pages + news (Jan-Sep 2026)
python -m scraper.wasalt         # -> data/raw/wasalt.jsonl   (add --details to also try listing pages)
python -m ingestion.normalize    # -> data/clean/{projects,company,news,listings}.jsonl + data/clean/data_report.md
```

| Source | How it is collected | Clean rows | What is available |
|---|---|---|---|
| DarGlobal | `sitemap.xml` -> each project page -> embedded Next.js / Strapi JSON (`__NEXT_DATA__`) | 41 projects in 6 countries (Oman 13, UAE 11, Saudi Arabia 10, Spain 3, Qatar 2, UK 2), incl. 7 "One of One" properties | Name, city and country (100%), brand partner, property types (98%), status (46%), and text sections (about, features, amenities, why invest, location), median 2,600 characters per project |
| DarGlobal company | About, partners + 11 partner pages, contact, FAQ, why-invest, investor relations, hospitality, development management, golf communities, commercial | 21 pages | 31 FAQ question-answer pairs, office addresses, role email addresses, partner overviews, board and company facts |
| DarGlobal news | Press, blog and insights articles, kept only if published 1 Jan - 30 Sep 2026 (date from the CMS) | 23 articles (11 press, 12 blog) | Title, date, body; e.g. HY 2026 results, Trump Plaza Jeddah, Azure Oceanfront Villas launch |
| Wasalt | English product sitemap on `cdn.wasalt.sa`, random sample of 300 (195 sale, 105 rent) | 300 listings | Purpose, property type (100%), bedrooms (74%), area (43%), URL |

Data quality notes:

- **No DarGlobal starting prices.** The CMS price field is empty for every project and prices are "on request", so `starting_price` is null.
- **Wasalt listing pages sit behind a Cloudflare challenge** for non-browser clients (HTTP 403, `cf-mitigated: challenge`). The scraper does not try to get around it. Listing fields come from the URL slug only, so price, city, district and description are missing.
- Wasalt slugs drop the decimal point in areas (158.88 sqm appears as `15888`). Areas above a plausible size for the property type are set to null.
- One DarGlobal page (`/pre-launch-trump-jeddah-tower`) returned HTTP 500 on every retry and is skipped. One project listed under two URLs is deduplicated.

The full report, with null rates and sample rows, is in `data/clean/data_report.md`.

## Project structure

```
scraper/
  common.py          # polite HTTP client: robots.txt, 1 req/s throttle, retries, challenge detection
  darglobal.py       # DarGlobal project pages -> data/raw/darglobal.jsonl
  darglobal_content.py  # DarGlobal company pages + Q1-Q3 2026 news
  wasalt.py          # Wasalt sitemap sample -> data/raw/wasalt.jsonl
ingestion/
  normalize.py       # clean schema, dedupe, data quality report
data/                # raw + clean snapshot (committed)
scripts/
  smoke_test.py      # Phase 0 connectivity checks
requirements.in      # direct dependencies
requirements.txt     # pinned dependencies (pip freeze)
.env.example         # environment variable template
```

Coming next: `ingestion/` (SQLite, Qdrant), `app/`, `eval/`, `Dockerfile`.

## Responsible scraping

- robots.txt is respected (Wasalt `/search` is disallowed and is not crawled; listings come from published sitemaps)
- About 1 request per second, identifiable User-Agent, capped volume
- No personal data (agent names, phone numbers) is collected
- Personal email addresses on the contact page are redacted; only role mailboxes (crm@, marketing@ ...) are kept
- Bot challenges (Cloudflare, Incapsula) are not bypassed; on a challenge the scraper retries a few times politely, then stops

## Limitations

- Data is a dated snapshot, not live inventory or prices
- English-first; Arabic queries may be weaker
- Free models can be slow or rate-limited
- Not investment, legal or mortgage advice
