# Property Intelligence Chatbot

An agentic RAG chatbot that answers natural-language questions about **DarGlobal** projects and **Wasalt** (Saudi Arabia) property listings, grounded in scraped public data, with a source link for every answer.

**Live chatbot:** https://property-intelligence-chatbot.onrender.com
(if it is asleep, open the [wake-up page](https://andrewraj0510.github.io/Property_Intelligence_Chatbot/wake/), which opens the app once the free server has started, usually within a minute)

**Status:** complete and deployed. Scraping, indexing, agent, guardrails, UI, Docker, Render deploy, Langfuse tracing and the evaluation are all done. Infrastructure cost: $0.

## How it works

Property data is half **structured** (purpose, bedrooms, type) and half **unstructured** (amenities, design partners, lifestyle). Vector search alone cannot reliably count or filter, so the agent has two kinds of tools:

| Tool | Backed by | Answers |
|---|---|---|
| `search_listings` | SQLite, parameterised queries from typed arguments (the LLM never writes SQL) | Filters, sorting, counts over Wasalt listings |
| `search_projects` | Qdrant hybrid search: dense embeddings + BM25, fused with Reciprocal Rank Fusion | Projects by amenities, brands, locations, descriptions |
| `get_project` | SQLite + Qdrant | "Tell me about project X" |
| `company_info` | SQLite company facts, then Qdrant | Offices, contacts, partners, FAQ |
| `search_news` | SQLite + Qdrant, date filters | DarGlobal news, Jan to Sep 2026 |

A LangGraph agent picks the tools (at most 3 rounds, about 2 LLM calls per question). A deterministic finalize step (no LLM) then removes any number or link that is not in the tool data, blocks leaks, and adds sources; if no evidence was found, it answers "I couldn't find that in the DarGlobal / Wasalt data I have" instead of guessing.

### The agent

One LangGraph `StateGraph` with three nodes (`app/graph.py`):

| Node | LLM? | What it does |
|---|---|---|
| `agent` | yes | Sends the question, the last 6 turns and the tool schemas to the free LLM, which either calls tools (several at once if needed) or writes the answer. The last allowed round forces an answer, so a turn never uses more than 4 LLM calls. |
| `tools` | no | Validates the arguments (Pydantic), runs the tools, scans the results for injected instructions and wraps them in `<tool_data>` so the LLM treats them as data. |
| `finalize` | no | Number check, link check, leak and prompt-leak check, markup stripping, sources appended. |

One agent instead of several keeps a turn at about 2 LLM calls, which matters on free-tier rate limits and latency; the "reviewer" work is done by deterministic code in `finalize` at no cost.

### Guardrails and security (summary)

- **Before the LLM** (`guardrail.check_input`): control characters stripped, 1,000-character cap, prompt injection and prompt/code extraction blocked, requests for agents' personal data blocked, user-typed emails and phone numbers redacted. Blocked turns cost 0 tokens.
- **No invented numbers:** missing fields are written into the data as "not published in our data"; every number in an answer must appear in the tool data or the question, otherwise that sentence is removed (or the answer is replaced with a safe message and the source link).
- **Links:** only https links returned by the tools (allowlisted `darglobal.co.uk` / `wasalt.sa`) survive; each URL is checked on its own, so a link cut off at the token limit cannot break the valid ones.
- **Leaks:** every answer is scanned for API keys and configured secret values, code, stack traces, index internals and the system prompt.
- **RAG:** ingestion scan for hidden text and injection phrases, domain allowlist at ingestion and query time, HMAC-SHA256 signature per chunk verified at query time, read-only Qdrant key at runtime, no LLM-written SQL or filters.
- **Abuse:** 8 turns per minute per session and per hashed client IP, 30 per minute globally; the raw IP is never stored.
- **Zero spend:** `app/config.py` drops any model that is not `:free`.

### Tracing

Each turn is one Langfuse trace with child observations for every LLM call, tool call and the finalize check: model, tokens, actual and equivalent paid cost, latency per step (LLM, embedding, Qdrant, SQLite), tools used, and scores (`unsupported_number`, `unknown_url`, `input_blocked`, `user_feedback` from the thumbs buttons). Emails and phones are masked before export, and export runs in the background so Langfuse can never slow down or break a turn.

## Stack

| Layer | Choice |
|---|---|
| LLM | OpenRouter free models with a fallback list (NVIDIA Nemotron 3 Ultra, Nemotron 3.5 Super VL, Nemotron 3.5 Lightning); temperature 0.1, 1,200-token answer cap |
| Embeddings | `nvidia/nemotron-3.5-embed-1b:free` via OpenRouter (2,048 dims); BM25 sparse vectors computed locally in `app/sparse.py` |
| Vector store | Qdrant Cloud (free tier, 734 chunks) |
| Structured store | SQLite, baked into the Docker image, opened read-only |
| Orchestration | LangGraph |
| UI | Streamlit |
| Observability | Langfuse Cloud (Hobby) |
| Hosting | Docker on Render (free tier, Frankfurt), cron-job.org keep-alive, wake page on GitHub Pages |
| Data refresh | GitHub Actions every 3 days, opens a pull request |

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
| `QDRANT_URL` | Qdrant Cloud cluster |
| `QDRANT_READ_API_KEY` | Read-only Qdrant key used by the running app |
| `QDRANT_API_KEY` | Read-write Qdrant key, only needed to build the index (not set in production) |
| `INDEX_SIGNING_KEY` | Secret for the HMAC signature on each chunk; same value at ingestion and runtime |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Langfuse tracing (optional) |
| `RATE_LIMIT_TURNS`, `RATE_LIMIT_WINDOW_S`, `RATE_LIMIT_GLOBAL_TURNS` | Rate limits (optional; defaults 8 per 60 s per user, 30 per minute global) |

### Check your setup

```bash
python scripts/smoke_test.py              # OpenRouter tool calling (each model), embeddings, Qdrant, Langfuse
python scripts/smoke_test.py --list-free  # list free OpenRouter models that support tool calling
```

The free model list on OpenRouter changes often; rerun the smoke test before relying on a model.

## Run the chatbot

```bash
streamlit run app/ui.py                 # local, http://localhost:8501
docker compose up --build               # same app in the production container (512 MB limit)
```

The UI has example question chips, a sources expander under each answer, a small latency and cost badge (hover it for time per step, tokens, LLM calls, actual cost and equivalent paid cost), thumbs up/down (sent to Langfuse as a `user_feedback` score on that turn's trace), a "New conversation" button, an "About this chatbot and evaluation results" box, and a "data as of" footer. Answers are fully checked by the guardrail before display and then streamed word by word, so the user never sees text that gets replaced afterward. The agent libraries load in a background thread, so the page renders before they finish importing.

Typical times on the live app (20 Nov 2026): page ready in about 6 to 7 s when the server is awake, up to about a minute after it has slept (use the wake page); answers take 7.1 s median and 20.2 s at p95.

### Deploy (Render free tier)

1. Render > New > Blueprint > this repo (uses `render.yaml`: Docker, free plan, health check `/_stcore/health`, auto-deploy on push).
2. Fill the secret env vars in the dashboard (same names as `.env.example`; prefer `QDRANT_READ_API_KEY` over the read-write key).
3. Keep-alive: a cron-job.org job hitting `https://<service>.onrender.com/_stcore/health` every 10 minutes.
4. Wake page: `wake/` is published on GitHub Pages (https://andrewraj0510.github.io/Property_Intelligence_Chatbot/wake/); it polls the app and redirects once the server is awake. It only redirects to `*.onrender.com`, so it cannot be used as an open redirect.

The image contains only `app/`, `.streamlit/` and `data/listings.db`; vectors live in Qdrant Cloud. The imported app uses about 140 MB of RAM.

## Data

Rebuild the snapshot (about 2 minutes, 1 request/second):

```bash
python -m scraper.darglobal      # -> data/raw/darglobal.jsonl
python -m scraper.darglobal_content   # -> company pages + news (Jan-Sep 2026)
python -m scraper.wasalt         # -> data/raw/wasalt.jsonl   (add --details to also try listing pages)
python -m ingestion.normalize    # -> data/clean/{projects,company,news,listings}.jsonl + data/clean/data_report.md
python -m ingestion.build_sqlite # -> data/listings.db
python -m ingestion.chunking     # -> data/chunks.jsonl (734 chunks)
python -m ingestion.build_qdrant # -> Qdrant collection (dense + BM25, HMAC-signed)
```

Or run everything in one go: `python scripts/refresh_data.py`. The same script runs every 3 days in GitHub Actions (`.github/workflows/refresh-data.yml`) and opens a pull request with the refreshed data instead of pushing to `main`. If any document type drops by more than 20%, it stops and keeps the previous data.

| Source | How it is collected | Clean rows | What is available |
|---|---|---|---|
| DarGlobal | `sitemap.xml` -> each project page -> embedded Next.js / Strapi JSON (`__NEXT_DATA__`) | 41 projects in 6 countries (Oman 13, UAE 11, Saudi Arabia 10, Spain 3, Qatar 2, UK 2), incl. 7 "One of One" properties | Name, city and country (100%), brand partner, property types (98%), status (46%), and text sections (about, features, amenities, why invest, location), median 2,600 characters per project |
| DarGlobal company | About, partners + 11 partner pages, contact, FAQ, why-invest, investor relations, hospitality, development management, golf communities, commercial, + 3 campaign pages (e.g. buying with crypto) | 24 pages | 31 FAQ question-answer pairs, office addresses, role email addresses, partner overviews, board and company facts |
| DarGlobal news | Press, blog and insights articles, kept only if published 1 Jan - 30 Sep 2026 (date from the CMS) | 23 articles (11 press, 12 blog) | Title, date, body; e.g. HY 2026 results, Trump Plaza Jeddah, Azure Oceanfront Villas launch |
| Wasalt | English product sitemap on `cdn.wasalt.sa`, random sample of 300 (195 sale, 105 rent) | 300 listings | Purpose, property type (100%), bedrooms (74%), area (76%, each with a certainty flag), URL |

Data quality notes:

- **No DarGlobal starting prices.** The CMS price field is empty for every project and prices are "on request", so `starting_price` is null.
- **Wasalt listing pages sit behind a Cloudflare challenge** for non-browser clients (HTTP 403, `cf-mitigated: challenge`). The scraper does not try to get around it. Listing fields come from the URL slug only, so price, city, district and description are missing.
- Wasalt slugs drop the decimal point in areas (158.88 sqm appears as `15888`). Each listing keeps the raw number and an `area_status`: `from_url` (plausible as is), `estimated` (decimal inferred for apartments, floors, villas) or `ambiguous` (e.g. land: 162987 could be 1,629.87 or 162,987 sqm, so `area_sqm` is null). `area_text` states the certainty in words so the chatbot cannot present an estimate as fact.
- Every listing lists its `missing_fields` (price, city, district, description), so the chatbot says the value is not in the data and links the listing page instead of guessing.
- The Wasalt sample is frozen in `data/raw/wasalt_sample_manifest.txt`; re-runs reuse the same 300 listings (`--resample` draws a new one). Listings that leave the sitemap are flagged `in_current_sitemap: false`.
- DarGlobal pages are not hard-coded: every sitemap page is classified from its CMS JSON (project, campaign landing page, One of One collection, other). If a page that was scraped before fails later, the last good copy is kept and flagged `stale`.
- `/pre-launch-trump-jeddah-tower` returns HTTP 500 and `/davinci-tower-by-pagani` returns 404 (removed by the site on 18 Nov although still in its sitemap); Da Vinci Tower is covered only through the Pagani partner page. One project listed under two URLs is deduplicated.

The full report, with null rates and sample rows, is in `data/clean/data_report.md`.

## Project structure

```
scraper/
  common.py             # polite HTTP client: robots.txt, 1 req/s throttle, retries, challenge detection
  darglobal.py          # DarGlobal project pages -> data/raw/darglobal.jsonl
  darglobal_content.py  # DarGlobal company pages + Q1-Q3 2026 news
  wasalt.py             # Wasalt sitemap sample -> data/raw/wasalt.jsonl
ingestion/
  normalize.py          # clean schema, dedupe, data quality report
  build_sqlite.py       # data/listings.db (listings, projects, company_facts, news)
  chunking.py           # section chunks with contextual headers, FAQ pairs, listing cards
  build_qdrant.py       # dense + BM25 vectors, payload indexes, HMAC signatures
app/
  config.py             # env vars, limits, zero-spend guard
  llm.py                # OpenRouter client: model fallback, timeouts, usage and cost capture
  embeddings.py         # query embeddings with timeout (hybrid falls back to keyword)
  sparse.py             # local BM25 sparse vectors
  retrieval.py          # keyword / semantic / hybrid (RRF), filters, diversity, small-to-big
  tools.py              # the 5 typed agent tools
  prompts.py            # system prompt
  graph.py              # LangGraph agent: agent -> tools -> finalize, cost record per turn
  guardrail.py          # input checks, number and link checks, markup stripping
  security.py           # allowlist, HMAC, injection scan, PII redaction, leak detector, rate limiter
  observability.py      # Langfuse (masked, background export)
  ui.py                 # Streamlit chat
data/                   # raw + clean snapshot, listings.db, chunks (committed)
scripts/
  smoke_test.py         # connectivity checks (OpenRouter, Qdrant, Langfuse)
  refresh_data.py       # full scrape -> rebuild pipeline with a sanity gate
  manual_eval.py, sanity_retrieval.py
eval/
  golden_set.jsonl      # 36 eval questions with labels (incl. 6 security probes)
  summary.py            # aggregate-only bridge to app/eval_summary.json (UI)
  retrieval_eval.py     # Hit@5 / MRR: keyword vs semantic vs hybrid
  e2e_eval.py           # end-to-end agent eval + cost report -> eval/results/
tests/
  test_security.py      # 24 security tests
  test_eval_grading.py  # 10 grader tests
wake/index.html         # wake-up page (GitHub Pages)
.github/workflows/refresh-data.yml   # scheduled data refresh (every 3 days)
Dockerfile, docker-compose.yml, render.yaml
requirements.in / requirements.txt   # direct / pinned dependencies
.env.example            # environment variable template
```

## Evaluation

Golden set: `eval/golden_set.jsonl`, 36 questions. 5 structured (SQL), 9 semantic, 2 mixed (named project), 4 company, 4 news, 3 out-of-scope or advice, 1 prompt injection, 2 multi-turn follow-ups, and 6 security probes (API key / env dump, source code request, prompt extraction, agent phone numbers, echoing the user's email, raw tool output with index internals). 19 of them have document-level relevance labels for the retrieval eval.

```bash
python eval/retrieval_eval.py              # Hit@5 / MRR@10: keyword vs semantic vs hybrid (no LLM; query embeddings cached)
python eval/e2e_eval.py --tag baseline     # full agent: correctness, tools, citations, refusals, latency, cost
python eval/e2e_eval.py --tag baseline --report-only   # rebuild the report from saved results
PYTHONPATH=. python tests/test_eval_grading.py   # offline tests for the grader
PYTHONPATH=. python tests/test_security.py       # security tests
```

How answers are graded, deterministically (no LLM judge, so grading costs no free-tier quota):
- **Correct**: the answer contains the expected facts (`must_contain` / `must_contain_any`). For out-of-scope, advice and injection questions, the answer must refuse or be blocked, and must not leak the prompt.
- **Citation valid**: every URL in the answer was returned by a tool during that turn, so made-up links fail.
- **Leak-free**: every answer is scanned with `security.find_leaks` for API keys or configured secret values, source code, stack traces, repo paths, index internals (`chunk_id`, `content_hash`, tool wrappers), personal emails or phones, and verbatim system prompt. Any leak fails the answer, even if its facts are right. Target: 100%.
- **Provider failures** (free-tier 429s) are counted and reported separately. They are excluded from the quality scores so that a rate limit is not scored as a wrong answer.

### Security in the eval and app

- **Same detector in production and eval.** The finalize node runs `find_leaks` on every answer. Secrets, code or internals replace the answer with a refusal; personal contact details are redacted. Leaks are logged to Langfuse as guardrail metadata. The eval scores the same thing, so the runtime defence is tested directly.
- **Eval outputs don't leak data.** Saved answers are PII-redacted, trace IDs are not stored, and error messages print only the exception type. Raw per-question results (`eval/results/*.jsonl`) and the embedding cache are git-ignored.
- **App and eval are connected through aggregates only.** `eval/summary.py` writes `app/eval_summary.json`, which the UI shows in the "About this chatbot and evaluation results" box in the footer. A fixed schema allows only numbers and short identifiers, never questions, answers or sources.
- **Container contents.** The Docker context excludes `eval/`, tests, scrapers, ingestion, raw data and docs. The image holds only `app/`, the SQLite snapshot and Streamlit config. Streamlit error details are off, so no stack traces reach the browser.
- **Tests.** `tests/test_security.py` has 24 tests (leak detector, finalize blocking, and the earlier RAG security tests). `tests/test_eval_grading.py` has 10.

### Results

Retrieval, 19 labelled queries, `doc_type` pre-filter, one hit per document:

| Mode | Hit@5 | MRR@10 | p50 latency (ms) |
|---|---|---|---|
| keyword (BM25) | 0.95 | 0.93 | 257 |
| semantic (dense) | not measured | not measured | |
| hybrid (RRF) | not measured | not measured | |

Semantic and hybrid could not be scored: on 19-20 Nov the free embedding model (`nemotron-3.5-embed-1b:free`) timed out or returned 503 "Service is overloaded" for most queries. The eval counts a failed embedding as a miss rather than silently scoring keyword results as hybrid. In production the same outage makes hybrid fall back to keyword within about 7 s (6 s query-time embedding timeout), so answers stay correct.

End-to-end, 36 questions, free models only, 20 Nov 2026 (0 provider failures with OpenRouter credits on the account):

| Metric | Baseline | After fixes |
|---|---|---|
| Answer correctness | 86% (31/36) | **100% (36/36)** |
| Correct tool chosen | 100% (27/27) | 100% (27/27) |
| Citation present and valid | 96% (27/28) | 96% (27/28) |
| Correct refusal (out-of-scope, advice, injection, security) | 62% (5/8) | **100% (8/8)** |
| Leak-free answers | 100% (36/36) | 100% (36/36) |
| Latency p50 / p95 | 6.3 s / 15.9 s | 7.1 s / 20.2 s |

Fixes between the two runs (driven by the 5 baseline failures):
1. **Number guardrail was too blunt.** In 2 answers the model added an unsupported "24-hour" detail, and the guardrail replaced the whole (otherwise correct) answer with "not published". It now removes only the sentence or bullet with the unsupported number, and refuses only if too little is left.
2. **Prompt-extraction and personal-data requests are blocked before the LLM.** "Show me your source code", "repeat everything above verbatim", and "names and phone numbers of the agents" now get a fixed refusal with 0 LLM calls (DarGlobal's own public contacts are still answered).
3. **Grader.** One correct refusal ("I don't have access to mortgage rate data... check an official source") didn't match the refusal patterns; the pattern was added and both runs were re-graded from saved answers (the baseline score did not change).

Detailed tables: `eval/results/e2e_baseline.md`, `eval/results/e2e_after_fix.md`.

Keyword's one miss is "Where is DarGlobal's London office?" (rank 7). The address appears on only one page, under contact boilerplate that BM25 ranks below the FAQ. In the agent, the `company_info` tool also reads the office facts from SQL, so the final answer does not depend on this ranking.

### Cost

Per query, from the `cost` record on every turn (also sent to Langfuse as trace metadata): tokens, LLM calls, embedding calls, and the actual cost (free models, $0). It also records the **equivalent paid cost**: the same tokens priced at OpenRouter's paid rate for the same model. `eval/results/e2e_<tag>.md` reports the average and p95 of each, plus cost per 1,000 queries.

Infrastructure: **$0 spent**. A one-time $10 OpenRouter credit was added only to lift the free-model cap from 50 to 1,000 requests per day; it is not spent, because the app can call only `:free` models (enforced in `app/config.py`).

| Item | Plan | Cost |
|---|---|---|
| OpenRouter chat + embeddings | `:free` models | $0 |
| Qdrant Cloud | Free cluster (1 GB) | $0 |
| Langfuse Cloud | Hobby | $0 |
| Render | Free web service (512 MB) | $0 |
| cron-job.org, wake page, GitHub | Free | $0 |

Per query (after-fix run): **7,430 tokens** avg (p95 15,420), **2.0 LLM calls** avg (p95 3), **$0.00 actual**, **$0.0013 equivalent paid** avg (p95 $0.0026). Per 1,000 queries: **$0 actual, $1.29 equivalent paid**. Blocked inputs cost 0 tokens.

### What I learned

- **Free-tier quota is the real constraint, not money.** A 50 requests/day cap is shared by chat, embeddings and the eval itself. That led to the following design choices: blocking inputs before any LLM call, deterministic grading instead of an LLM judge, and caching query embeddings in the eval.
- **Silent fallbacks can corrupt an evaluation.** When embeddings fail, hybrid search falls back to keyword. That is right for users but wrong for an eval: the first run reported hybrid with exactly the keyword score. The eval now treats a degraded hybrid search as an error.
- **BM25 is strong on this corpus.** Queries name brands and places ("Pagani", "Fendi Casa", "Muscat"), so exact terms dominate. That is why the default is hybrid with RRF rather than dense-only search.
- **Guardrails need to be precise, not just safe.** The no-invented-numbers check was correct every time, but replacing a whole answer for one bad sentence made 2 correct answers useless. Trimming at sentence level kept both safety and usefulness.
- **Deterministic pre-LLM blocks beat prompt rules for security.** The model refused extraction requests anyway, but in its own words; a fixed input guardrail is consistent, testable, and free.
- **Structured questions belong in SQL.** Counts and filters ("how many villas for sale") are exact in SQLite, while vector search can only approximate them.

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
