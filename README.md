# Property Intelligence Chatbot

An agentic RAG chatbot that answers natural-language questions about **DarGlobal** projects, the company and its news, and **Wasalt** (Saudi Arabia) property listings. Every answer is grounded in scraped public data and comes with a source link.

| | |
|---|---|
| **Live chatbot** | https://property-intelligence-chatbot.onrender.com |
| **Wake-up page** | https://andrewraj0510.github.io/Property_Intelligence_Chatbot/wake/ (use it if the app is asleep; it opens the chatbot once the free server has started, usually within a minute) |
| **Status** | Complete and deployed. Infrastructure cost: **$0** |

<p align="center">
  <img src="assets/screenshots/home.png" alt="Home screen with example questions" width="49%">
  <img src="assets/screenshots/project_answer.png" alt="Project answer with the time and cost card open" width="49%">
</p>

## Contents

- [Highlights](#highlights)
- [Assignment checklist](#assignment-checklist)
- [Architecture](#architecture)
- [The agent](#the-agent)
- [Screenshots](#screenshots)
- [Guardrails and security](#guardrails-and-security)
- [Tracing](#tracing)
- [Stack and why](#stack-and-why)
- [Response time](#response-time)
- [Cost](#cost)
- [Getting started](#getting-started)
- [Data](#data)
- [Evaluation](#evaluation)
- [Project structure](#project-structure)
- [What I learned](#what-i-learned)
- [Responsible scraping](#responsible-scraping)
- [Limitations and next steps](#limitations-and-next-steps)

## Highlights

| Result | Value |
|---|---|
| Answer correctness (36 test questions) | **100%** (36/36) |
| Correct tool chosen by the agent | 100% (27/27) |
| Answers with valid source links | 96% (27/28) |
| Correct refusals (off-topic, advice, attacks) | **100%** (8/8) |
| Answers with no leaked secrets or personal data | 100% (36/36) |
| Answer time, median / p95 | **5.2 s / 11.3 s** |
| LLM calls per question (average) | 1.7 |
| Actual cost | **$0** (free models, free plans) |
| Same usage on paid models | $1.24 per 1,000 questions |

## Assignment checklist

| Requirement | Where it was done |
|---|---|
| Scrape public data from DarGlobal and Wasalt | `scraper/darglobal.py` (41 projects), `scraper/darglobal_content.py` (24 company pages, 23 news articles), `scraper/wasalt.py` (300 listings from the public sitemap). Re-scraped every 3 days by GitHub Actions. |
| Build an AI chatbot on the data | LangGraph agent in `app/graph.py`, 6 typed tools in `app/tools.py`, guardrails in `app/guardrail.py` and `app/security.py`, Streamlit chat in `app/ui.py`. Data in SQLite and Qdrant Cloud. |
| Use a free OpenRouter model | `app/llm.py`: 3 free NVIDIA Nemotron models as a fallback list, plus the free `nvidia/nemotron-3.5-embed-1b:free` embedding model. `app/config.py` drops any model that is not `:free`. |
| Containerise with Docker | `Dockerfile` (Python 3.11 slim, non-root, health check) and `docker-compose.yml` (512 MB limit, same as the server). The app uses about 140 MB. |
| Deploy, not only source code | Render free web service (Frankfurt) from `render.yaml`. Every push to `main` deploys again. |
| Working URL | https://property-intelligence-chatbot.onrender.com, with a wake-up page and a cron job that pings the app every 10 minutes so it stays awake. |

## Architecture

```mermaid
flowchart LR
    USER(["👤 User"])
    WAKE["🔔 Wake-up page<br/>GitHub Pages"]
    CRON["⏰ cron-job.org<br/>ping every 10 min"]

    subgraph RENDER["🐳 Docker container · Render free tier · 512 MB"]
        direction LR
        UI["🖥️ Streamlit UI<br/>chat · sources · cost badge"]
        IG["🛡️ Input guardrail<br/>injection · PII · rate limit"]
        subgraph AGENT["🤖 LangGraph agent"]
            direction TB
            A["agent node<br/>LLM picks tools, writes answer"]
            T["tools node<br/>6 typed tools"]
            F["finalize node<br/>number · link · leak checks"]
        end
    end

    subgraph STORES["🗄️ Data stores"]
        direction TB
        DB[("SQLite · read-only<br/>baked into the image<br/>listings · projects · facts · news")]
        QD[("Qdrant Cloud<br/>734 chunks<br/>dense + BM25 · RRF")]
    end

    subgraph SERVICES["☁️ Free cloud services"]
        direction TB
        OR["🧠 OpenRouter<br/>Nemotron :free LLMs<br/>+ free embeddings"]
        LF["📈 Langfuse<br/>traces · cost · feedback"]
    end

    subgraph PIPE["⚙️ Offline data pipeline · GitHub Actions every 3 days"]
        direction LR
        DG["🌐 DarGlobal<br/>sitemap + pages"]
        WS["🌐 Wasalt<br/>public sitemap"]
        SC["Scrapers<br/>robots.txt · 1 req/s"]
        NM["Normalize<br/>dedupe · mark missing"]
        CH["Chunk<br/>contextual headers"]
        EM["Embed + BM25<br/>HMAC-sign chunks"]
    end

    USER -->|asks| UI
    USER -.->|if asleep| WAKE -.->|opens when awake| UI
    CRON -.->|keeps awake| UI
    UI --> IG --> A
    A -->|tool calls| T
    T -->|results| A
    A -->|draft| F
    F -.->|checked answer| UI
    T -->|exact filters, counts| DB
    T -->|hybrid search| QD
    A <-->|chat completions| OR
    F -.->|trace| LF

    DG --> SC
    WS --> SC
    SC --> NM --> CH --> EM
    NM -->|build| DB
    EM -->|upsert| QD

    classDef src fill:#E8F1FF,stroke:#3B82F6,color:#0B1F44
    classDef pipe fill:#FFF4E5,stroke:#F59E0B,color:#3D2600
    classDef app fill:#E9FBF0,stroke:#22C55E,color:#06371A
    classDef store fill:#E6FAFB,stroke:#06B6D4,color:#073B40
    classDef cloud fill:#F3EDFF,stroke:#8B5CF6,color:#24104F
    classDef ext fill:#F5F5F5,stroke:#9CA3AF,color:#111111
    class DG,WS src
    class SC,NM,CH,EM pipe
    class UI,IG,A,T,F app
    class DB,QD store
    class OR,LF cloud
    class USER,WAKE,CRON ext
    style RENDER fill:#F5FDF8,stroke:#22C55E,color:#06371A
    style AGENT fill:#DDF7E7,stroke:#16A34A,color:#06371A
    style STORES fill:#F3FDFD,stroke:#06B6D4,color:#073B40
    style SERVICES fill:#FAF7FF,stroke:#8B5CF6,color:#24104F
    style PIPE fill:#FFFBF3,stroke:#F59E0B,color:#3D2600
```

Property data is half **structured** (purpose, bedrooms, type, country) and half **unstructured** (amenities, design partners, location). Vector search alone cannot count or filter reliably, and SQL alone cannot understand meaning, so the agent has both:

- **SQLite** answers exact questions: "How many villas for sale are there?", "List the projects in Muscat", "What is the London office address?". The file is built by the pipeline, baked into the Docker image and opened read-only.
- **Qdrant** answers questions about meaning: "Which projects have interiors by Pagani?", "Oceanfront villas in Oman". All four data types share one collection, kept apart by a `doc_type` field.

**How search works**

- **Hybrid:** each search runs a dense (meaning) search and a BM25 (keyword) search, takes the top 20 from each and merges them with Reciprocal Rank Fusion. Dense search catches different wording; BM25 catches exact names like "Pagani" or "DG1".
- **Filters first:** country, city, brand, date and type filters are applied inside the search, before ranking.
- **Variety:** at most 2 chunks per project, so one project cannot fill all results.
- **Small to big:** if 2 or more chunks of the same project match, the project's summary card is added.
- **Fallback:** if the free embedding model does not answer within 6 seconds, search falls back to keyword search, so the user still gets an answer.

**Data pipeline:** scrape (sitemaps, robots.txt, 1 req/s) → normalize (one schema, dedupe, mark every missing field) → SQLite + 734 chunks with contextual headers → dense + BM25 vectors, each chunk HMAC-signed → Qdrant. GitHub Actions runs it every 3 days and opens a pull request; if any data type drops by more than 20%, the job stops and keeps the old data.

## The agent

One LangGraph `StateGraph` with three nodes (`app/graph.py`). Only the agent node calls the LLM.

```mermaid
flowchart TD
    Q(["💬 User question"]) --> G{"Input guardrail<br/>(code)"}
    G -->|"injection, prompt or<br/>personal-data request"| B["Fixed reply<br/>0 LLM calls · 0 tokens"]
    G -->|ok| A["agent node (LLM)<br/>system prompt + last 6 turns<br/>+ tool list + question"]
    A -->|"asks for tools"| T["tools node (code)<br/>validate args · run tools in parallel<br/>scan + wrap results as tool_data"]
    T -->|"results · max 3 rounds"| A
    A -.->|"facts but no tool used"| RA["Asked again:<br/>must use a tool"]
    RA -.-> A
    A -->|"writes answer"| F["finalize node (code)<br/>numbers in data? links from tools?<br/>no leaks · add sources"]
    F --> OUT(["✅ Answer + sources + time and cost badge"])

    classDef llm fill:#F3EDFF,stroke:#8B5CF6,color:#24104F
    classDef code fill:#E9FBF0,stroke:#22C55E,color:#06371A
    classDef io fill:#E8F1FF,stroke:#3B82F6,color:#0B1F44
    classDef stop fill:#FFECEC,stroke:#EF4444,color:#4A0B0B
    class A llm
    class G,T,F code
    class Q,OUT io
    class B,RA stop
```

| Node | LLM? | What it does |
|---|---|---|
| `agent` | yes | Sends the question, the last 6 turns and the tool schemas to the free LLM, which either calls tools (several at once if needed) or writes the answer. The last allowed round forces an answer, so a turn never uses more than 4 LLM calls; most use 2. |
| `tools` | no | Validates the arguments (Pydantic), runs the requested tools in parallel, scans the results for injected instructions and wraps them in `<tool_data>` so the LLM treats them as data. |
| `finalize` | no | Number check, link check, leak and prompt-leak check, markup stripping, sources appended. |

### The six tools

| Tool | Reads from | Used for | Example |
|---|---|---|---|
| `search_listings` | SQLite | Filter, sort and count Wasalt listings | How many villas for sale are there? |
| `list_projects` | SQLite | The complete list or count of DarGlobal projects, with exact filters (country, city, brand, property type, status) | List all DarGlobal projects in Oman |
| `search_projects` | Qdrant | Projects by meaning (views, amenities, design); best matches, not a full list | Which projects have Fendi Casa interiors? |
| `get_project` | SQLite + Qdrant | Everything about one named project | Tell me about Trump Tower Jeddah |
| `company_info` | SQLite facts, then Qdrant | Offices, contacts, partners, FAQ, how to buy | Where is DarGlobal's London office? |
| `search_news` | SQLite + Qdrant | Press and blog posts, Jan to Sep 2026 | What did DarGlobal announce this year? |

The LLM never writes SQL or database filters. It only fills typed fields (for example `purpose = sale`, `min_beds = 3`) and code builds a safe, parameterised query. If a filter cannot work on the data (price or city on Wasalt), the tool says so and the agent explains it.

**Why `list_projects` exists.** "List all projects in Oman" first went to the search tool, which returns only the best 6 matches, so the answer showed 7 of 13 projects. Search can never promise a full list, so `list_projects` reads the projects table and returns every match with a count. It also maps what users type onto stored values: UAE / Emirates / Dubai, KSA / Saudi, GCC / Middle East, Marbella (Benahavis and Casares), RAK (Al Marjan Island), villas / flats / land / off-plan. A value that is not in the data (for example Egypt) gets a clear message instead of 0.

**Why one agent and not many.** Free models are slow and rate-limited. Every extra agent means extra LLM calls, more waiting and more ways to fail. One agent with clear tools answered all 36 test questions correctly; the "reviewer" work is done by deterministic code in `finalize` at no cost.

**Chat memory.** The last 6 questions and answers are kept, so follow-ups like "Which of those has a golf course?" work. Only the question and answer text is kept (no old tool results), each old message is cut to 1,200 characters, memory is treated as untrusted (hidden characters removed, emails and phones redacted), and blocked turns are dropped so an injection cannot come back later. Memory lives only in the open session; "New conversation" clears it. Follow-ups that narrow a list call the tool again with both filters instead of filtering the shortened memory.

## Screenshots

| | |
|---|---|
| **Wasalt listings** (SQL tool, total count, missing fields explained)<br><img src="assets/screenshots/wasalt_listings.png" alt="Wasalt listings answer" width="100%"> | **Search by meaning** (hybrid search, Sources box open)<br><img src="assets/screenshots/semantic_search.png" alt="Projects with Pagani interiors" width="100%"> |
| **Company contacts** (exact facts from SQLite)<br><img src="assets/screenshots/company_contact.png" alt="Company contact answer" width="100%"> | **Follow-up** ("those" means the Dubai projects)<br><img src="assets/screenshots/follow_up.png" alt="Follow-up question using chat memory" width="100%"> |
| **Price not in the data** (says so, gives links, no guessing)<br><img src="assets/screenshots/missing_price.png" alt="Missing price answer" width="100%"> | **Off-topic** (mortgage rates: points to an official source)<br><img src="assets/screenshots/out_of_scope.png" alt="Out of scope answer" width="100%"> |
| **Prompt injection** (blocked before the LLM, 0.0 s, 0 tokens)<br><img src="assets/screenshots/injection_blocked.png" alt="Prompt injection blocked" width="100%"> | **Wake-up page** (while the free server starts)<br><img src="assets/screenshots/wake_page.png" alt="Wake-up page" width="100%"> |

### Things to try

| Topic | Sample questions |
|---|---|
| Projects | Which DarGlobal projects are in Dubai? / Which DarGlobal projects are sold out? |
| Full lists and counts | List all DarGlobal projects in Oman. / How many projects does DarGlobal have in Saudi Arabia? / Any DarGlobal projects in Marbella? |
| One project | Tell me about the Trump Tower Jeddah project. / What unit types does DG1 have? |
| Search by meaning | Which projects have interiors designed by Pagani? / Which projects have golf views? |
| Wasalt listings | Show me 3-bedroom apartments for sale on Wasalt. / How many apartments for rent are there? |
| Company | How can I contact DarGlobal? / Who are DarGlobal's brand partners? / Can I buy with crypto? |
| News (Jan to Sep 2026) | What were DarGlobal's half-year 2026 results? / Tell me about the Azure Oceanfront Villas launch. |
| Follow-up | After "Which DarGlobal projects are in Dubai?" ask "Which of those has a golf course?" |

Prices are not in the data (DarGlobal prices are "on request", Wasalt listing pages are behind a bot check), so price questions get "not published in our data" plus the listing link, never a made-up number.

## Guardrails and security

**Against hallucination**, at four levels:

| Level | Guardrail |
|---|---|
| 1. Data | Every missing field is written as "not published in our data", never 0 or blank. Wasalt areas guessed from the URL say "about", "estimated" or "unclear". |
| 2. Tools | A filter that cannot be applied returns `unsupported_filters`; a value not in the data returns a clear message instead of 0. |
| 3. Prompt | Answer only from tool results, cite the source link after each fact, never state a number not in the data, no investment, legal or mortgage advice. |
| 4. Finalize (code) | Every number must appear in the tool data or the question, otherwise that sentence is removed (or the whole answer is replaced with a safe message and the source link). Every link must come from the tools; each URL is checked on its own, so a link cut off at the token limit cannot break the valid ones. |

One more check runs before the answer is written: if the model answers a factual question without using a tool and the answer contains an email, a link or a number, it is asked again and must use a tool. If the tools find nothing, the answer is "I couldn't find that in the DarGlobal / Wasalt data I have".

**LLM security**

- **Before the LLM** (`guardrail.check_input`): control characters stripped, 1,000-character cap, prompt injection and prompt / code / key extraction blocked, requests for agents' personal data blocked, user-typed emails and phones redacted. Blocked turns cost 0 tokens.
- **Indirect injection:** tool results are scanned, injection phrases replaced with `[removed]`, and results wrapped as data the prompt says is never an instruction.
- **Leaks:** every answer is scanned for API keys and configured secret values, code, stack traces, file paths, index internals and the system prompt; a hit replaces the answer.
- **Exfiltration:** markdown images, raw HTML and non-https links are removed.
- **Bounded loop:** at most 3 tool rounds and 4 LLM calls per question.

**RAG security**

- Hidden characters, scripts, HTML comments and injection phrases are removed at ingestion.
- Domain allowlist (`darglobal.co.uk`, `wasalt.sa`, https only) at ingestion and at query time; look-alike domains are rejected.
- Each chunk carries an HMAC-SHA256 signature, verified at query time; tampered chunks are dropped.
- The running app has only a read-only Qdrant key; the write key is used only by the pipeline.

**App and infrastructure**

- Keys live only in Render environment variables; `.env` is git-ignored.
- SQLite opened read-only, parameterised SQL only.
- Container runs as non-root and holds no scrapers, tests, raw data or eval files.
- Friendly error messages; stack traces never reach the browser.
- Rate limits: 8 questions per minute per session and per salted IP hash, 30 per minute globally. The raw IP is never stored.
- The wake-up page only redirects to `*.onrender.com`, so it cannot be used as an open redirect.
- **Zero spend:** `app/config.py` drops any model that is not `:free`.

## Tracing

Each turn is one Langfuse trace with child observations for every LLM call, tool call and the finalize check: model, tokens, actual and equivalent paid cost, latency per step (LLM, embedding, Qdrant, SQLite), tools used, tool rounds and search mode. Scores: `unsupported_number`, `unknown_url`, `input_blocked` and `user_feedback` (thumbs buttons). Emails and phones are masked before export, and export runs in the background, so Langfuse can never slow down or break a turn.

In the UI, each answer has a small time and cost badge; hovering shows time per step, tokens, LLM calls, actual cost and equivalent paid cost.

## Stack and why

| Layer | Choice | Why |
|---|---|---|
| Chat LLM | OpenRouter free models as a fallback list: NVIDIA Nemotron 3 Ultra, Nemotron 3.5 Super VL, Nemotron 3.5 Lightning | Required by the brief. All three passed a real tool-calling test on 18 Nov (free Gemma models failed with rate limits). If one is busy, the next is tried. |
| LLM settings | Temperature 0.1, 2,500-token answer cap, reasoning on for picking tools and off for writing the answer, 20 s timeout per model | Answers repeat the data. The cap fits the full 41-project list; only generated tokens cost time. See [Response time](#response-time). |
| Embeddings | `nvidia/nemotron-3.5-embed-1b:free` (2,048 dims) | Free and remote, so no embedding model sits in the 512 MB container. |
| Sparse vectors | BM25 computed locally (`app/sparse.py`) | No model download, almost no memory, very strong on brand and place names. |
| Vector store | Qdrant Cloud, free tier (734 chunks) | Dense + sparse + filters in one query, fusion built in. |
| Structured store | SQLite in the image, read-only | Exact filters, counts and sorting with no network call. |
| Orchestration | LangGraph | Clear, traceable, bounded steps. |
| UI | Streamlit | Python only; chat, buttons and expanders out of the box. |
| Observability | Langfuse Cloud (Hobby) | Traces, tokens, cost and feedback per question. |
| Scraping | httpx, selectolax, BeautifulSoup | DarGlobal pages carry their data as embedded JSON, so no headless browser is needed. |
| Hosting | Docker on Render (free, Frankfurt), cron-job.org keep-alive, wake page on GitHub Pages | Free, deploys on every push; Frankfurt is the closest free region to the Gulf. |
| Data refresh | GitHub Actions every 3 days, opens a pull request | Free, reviewed before it reaches the app. |

## Response time

A normal answer takes about 5 seconds (median 5.2 s, p95 11.3 s over the 36-question eval). Almost all of it is the LLM: in the project answer shown above, 4.0 s total = 3.2 s for the two LLM calls + 0.8 s search (including the embedding) + 0.01 s SQLite. The LLM part takes time because:

- **Two calls, one after the other:** one to pick the tools, one to write the answer, which can only start after the tools return.
- **Long prompts:** the system prompt and tool list are about 3,500 tokens per call, so a question uses about 7,500 to 8,000 input tokens in total.
- **Large models on shared free servers:** Nemotron 3 Ultra has about 550B parameters and runs with no speed guarantee, so times vary during the day.
- **Thinking when picking tools:** kept on because without it the model sometimes skipped the tools and made up facts; it adds about 1 to 3 s.

It used to be slower: on 19 Nov most answers took 2 to 23 s and a few took 84 to 142 s. The causes were thinking on the answer step (4 to 11 s, and it used up the answer tokens so answers were cut off), waiting a full 30 s on a stalled free provider, and tools running one after another. The fixes were reasoning only on the tool-picking step, a 20 s wait per model, tools running in parallel, a query embedding cache, and loading the price list in the background. Median went from 7.1 s to 5.2 s and p95 from 20.2 s to 11.3 s, with quality unchanged at 36/36.

Page load: about 1.5 s to first paint and 6 to 7 s until fully ready when the server is awake; up to about a minute after it has slept (use the wake page). Blocked questions return in under 0.1 s.

## Cost

Every turn records a `cost` record (also sent to Langfuse): tokens, LLM calls, embedding calls, actual cost, and the **equivalent paid cost** (the same tokens at OpenRouter's paid rate for the same model).

| Per question (36-question eval, 20 Nov) | Average | p95 |
|---|---|---|
| Tokens (input + output) | 7,590 | 11,171 |
| LLM calls | 1.7 | 2 |
| Actual cost | $0.00 | $0.00 |
| Equivalent paid cost | $0.0012 | $0.0018 |

Per 1,000 questions: **$0 actual, $1.24 equivalent paid**. Blocked inputs cost 0 tokens.

**Infrastructure: $0 spent.** A one-time $10 OpenRouter credit was added only to lift the free-model cap from 50 to 1,000 requests per day; it is not spent, because the app can call only `:free` models.

| Item | Plan used | Cost | Nearest paid option |
|---|---|---|---|
| OpenRouter chat + embeddings | `:free` models | $0 | $1.24 per 1,000 questions |
| Render | Free web service (512 MB) | $0 | Starter, $7 / month |
| Langfuse Cloud | Hobby | $0 | Core, $29 / month |
| Qdrant Cloud | Free cluster (1 GB) | $0 | Usage based (not counted) |
| cron-job.org, GitHub Pages, GitHub Actions | Free | $0 | |

**Saved:** at least $36 a month plus $1.24 per 1,000 questions (about $48 a month at 10,000 questions).

**Token optimization:** only the last 6 turns, old answers cut to 1,200 characters and no old tool results in memory; tool output capped at 6,000 characters (14,000 for `list_projects` so a full list is never cut); 4 to 6 search chunks with at most 2 per project; exact facts from a small SQLite table; no thinking on the answer step (saved up to 460 tokens); blocked inputs and code-based checks use no LLM; at most 4 LLM calls per question; compact tool descriptions; code grading in the eval instead of an LLM judge.

**Prompt caching:** the free NVIDIA models offer no manual cache control, so the prompt is kept cache-friendly for providers that cache automatically: a fixed system prompt (only the snapshot date, no timestamps or user data), a tool list built once at start-up, and a fixed order (system prompt, tools, memory, then the new question). `cached_prompt_tokens` is recorded on every call. Other caches: agent libraries loaded once per server (`st.cache_resource`), the price list fetched once in the background, a query embedding cache (last 512 questions), and embedding caches for index builds and the eval.

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
| `LLM_REASONING`, `LLM_TIMEOUT_S`, `EMBED_QUERY_TIMEOUT_S` | Optional tuning: `auto` / `on` / `off` (default `auto`), seconds per model attempt (default 20), query embedding timeout before keyword fallback (default 6) |

### Check your setup

```bash
python scripts/smoke_test.py              # OpenRouter tool calling (each model), embeddings, Qdrant, Langfuse
python scripts/smoke_test.py --list-free  # list free OpenRouter models that support tool calling
```

The free model list on OpenRouter changes often; rerun the smoke test before relying on a model.

### Run the chatbot

```bash
streamlit run app/ui.py                 # local, http://localhost:8501
docker compose up --build               # same app in the production container (512 MB limit)
```

Answers are fully checked by the guardrail before display and then streamed word by word, so the user never sees text that gets replaced afterward. The agent libraries load in a background thread, so the page renders before they finish importing.

### Deploy (Render free tier)

1. Render > New > Blueprint > this repo (uses `render.yaml`: Docker, free plan, health check `/_stcore/health`, auto-deploy on push).
2. Fill the secret env vars in the dashboard (same names as `.env.example`; prefer `QDRANT_READ_API_KEY` over the read-write key).
3. Keep-alive: a cron-job.org job hitting `https://<service>.onrender.com/_stcore/health` every 10 minutes.
4. Wake page: `wake/` is published on GitHub Pages; it polls the app and redirects once the server is awake (only to `*.onrender.com`).

The image contains only `app/`, `.streamlit/` and `data/listings.db`; vectors live in Qdrant Cloud.

## Data

Rebuild the snapshot (about 2 minutes, 1 request/second):

```bash
python -m scraper.darglobal           # -> data/raw/darglobal.jsonl
python -m scraper.darglobal_content   # -> company pages + news (Jan-Sep 2026)
python -m scraper.wasalt              # -> data/raw/wasalt.jsonl   (add --details to also try listing pages)
python -m ingestion.normalize         # -> data/clean/{projects,company,news,listings}.jsonl + data/clean/data_report.md
python -m ingestion.build_sqlite      # -> data/listings.db
python -m ingestion.chunking          # -> data/chunks.jsonl (734 chunks)
python -m ingestion.build_qdrant      # -> Qdrant collection (dense + BM25, HMAC-signed)
```

Or run everything in one go: `python scripts/refresh_data.py`. The same script runs every 3 days in GitHub Actions (`.github/workflows/refresh-data.yml`) and opens a pull request with the refreshed data instead of pushing to `main`. If any document type drops by more than 20%, it stops and keeps the previous data. The app footer shows the current snapshot date.

| Source | How it is collected | Clean rows | What is available |
|---|---|---|---|
| DarGlobal | `sitemap.xml` -> each project page -> embedded Next.js / Strapi JSON (`__NEXT_DATA__`) | 41 projects in 6 countries (Oman 13, UAE 11, Saudi Arabia 10, Spain 3, Qatar 2, UK 2), incl. 7 "One of One" properties | Name, city and country (100%), brand partner, property types (98%), status (46%), and text sections (about, features, amenities, why invest, location), median 2,600 characters per project |
| DarGlobal company | About, partners + 11 partner pages, contact, FAQ, why-invest, investor relations, hospitality, development management, golf communities, commercial, + 3 campaign pages (e.g. buying with crypto) | 24 pages | 31 FAQ question-answer pairs, office addresses, role email addresses, partner overviews, board and company facts |
| DarGlobal news | Press, blog and insights articles, kept only if published 1 Jan - 30 Sep 2026 (date from the CMS) | 23 articles (11 press, 12 blog) | Title, date, body; e.g. HY 2026 results, Trump Plaza Jeddah, Azure Oceanfront Villas launch |
| Wasalt | English product sitemap on `cdn.wasalt.sa`, random sample of 300 (195 sale, 105 rent) | 300 listings | Purpose, property type (100%), bedrooms (74%), area (76%, each with a certainty flag), URL |

Data quality notes:

- **No DarGlobal starting prices.** The CMS price field is empty for every project and prices are "on request", so `starting_price` is null.
- **Wasalt listing pages sit behind a Cloudflare challenge** for non-browser clients (HTTP 403, `cf-mitigated: challenge`). The scraper does not try to get around it. Listing fields come from the URL slug only, so price, city, district and description are missing. In a real project I would ask Wasalt for an API or data feed.
- Wasalt slugs drop the decimal point in areas (158.88 sqm appears as `15888`). Each listing keeps the raw number and an `area_status`: `from_url` (plausible as is), `estimated` (decimal inferred for apartments, floors, villas) or `ambiguous` (e.g. land: 162987 could be 1,629.87 or 162,987 sqm, so `area_sqm` is null). `area_text` states the certainty in words so the chatbot cannot present an estimate as fact.
- Wasalt listings have no public name, so each is named by its Wasalt number plus the facts from its URL, for example "Wasalt #5924240: Villa, for sale, 300 sqm, facing west".
- Every listing lists its `missing_fields` (price, city, district, description), so the chatbot says the value is not in the data and links the listing page instead of guessing.
- The Wasalt sample is frozen in `data/raw/wasalt_sample_manifest.txt`; re-runs reuse the same 300 listings (`--resample` draws a new one). Listings that leave the sitemap are flagged `in_current_sitemap: false`.
- DarGlobal pages are not hard-coded: every sitemap page is classified from its CMS JSON (project, campaign landing page, One of One collection, other). If a page that was scraped before fails later, the last good copy is kept and flagged `stale`.

The full report, with null rates and sample rows, is in `data/clean/data_report.md`.

## Evaluation

Golden set: `eval/golden_set.jsonl`, 36 questions: 5 structured (SQL), 9 semantic, 2 mixed (named project), 4 company, 4 news, 3 out-of-scope or advice, 1 prompt injection, 2 multi-turn follow-ups, and 6 security probes (API key / env dump, source code request, prompt extraction, agent phone numbers, echoing the user's email, raw tool output with index internals). 19 have document-level relevance labels for the retrieval eval.

```bash
python eval/retrieval_eval.py              # Hit@5 / MRR@10: keyword vs semantic vs hybrid (no LLM; query embeddings cached)
python eval/e2e_eval.py --tag baseline     # full agent: correctness, tools, citations, refusals, latency, cost
python eval/e2e_eval.py --tag baseline --report-only   # rebuild the report from saved results
python scripts/manual_eval.py              # 10 questions in one chat, including a follow-up
python scripts/edge_eval.py                # 14 list and table questions, checks every expected project name
PYTHONPATH=. python tests/test_security.py       # 24 security tests
PYTHONPATH=. python tests/test_eval_grading.py   # 10 grader tests
PYTHONPATH=. python -m pytest tests/test_list_projects.py   # 17 list-tool tests (needs pytest)
```

Grading is deterministic (no LLM judge, so it costs no free-tier quota):

- **Correct:** the answer contains the expected facts (`must_contain` / `must_contain_any`). Out-of-scope, advice and injection questions must be refused or blocked without leaking the prompt.
- **Citation valid:** every URL in the answer was returned by a tool during that turn, so made-up links fail.
- **Leak-free:** every answer is scanned with `security.find_leaks` (the same detector the app runs in `finalize`) for API keys or secret values, source code, stack traces, repo paths, index internals, personal emails or phones, and the verbatim system prompt. Any leak fails the answer.
- **Provider failures** (free-tier 429s) are counted separately and excluded from quality scores.

### Results

End-to-end, 36 questions, free models only:

| Metric | Baseline | After fixes | After speed changes (20 Nov) |
|---|---|---|---|
| Answer correctness | 86% (31/36) | 100% (36/36) | **100% (36/36)** |
| Correct tool chosen | 100% (27/27) | 100% (27/27) | 100% (27/27) |
| Citation present and valid | 96% (27/28) | 96% (27/28) | 96% (27/28) |
| Correct refusal | 62% (5/8) | 100% (8/8) | **100% (8/8)** |
| Leak-free answers | 100% (36/36) | 100% (36/36) | 100% (36/36) |
| Latency p50 / p95 | 6.3 s / 15.9 s | 7.1 s / 20.2 s | **5.2 s / 11.3 s** |

- **Baseline to after fixes:** the number check now removes only the sentence with an unsupported number instead of the whole answer (2 correct answers had been replaced), and source code, prompt and agent phone requests are blocked before the LLM with 0 calls.
- **After speed changes:** reasoning only when picking tools, 20 s per model, parallel tools, caches, and the new `list_projects` tool. Four questions written before `list_projects` existed now list it as an expected tool, and the grader learned the chatbot's own "I couldn't find that in the data" refusal.
- `scripts/manual_eval.py`: 10/10 correct and cited. `scripts/edge_eval.py`: 14/14 (all projects in Oman, a narrowing follow-up, counts, aliases like Marbella and KSA, a country with no projects, a typo, an Arabic question, the full 41-project list), median 5.0 s.

Detailed tables: `eval/results/e2e_baseline.md`, `e2e_after_fix.md`, `e2e_latency_fix.md`.

Retrieval, 19 labelled queries, `doc_type` pre-filter, one hit per document:

| Mode | Hit@5 | MRR@10 | p50 latency (ms) |
|---|---|---|---|
| keyword (BM25) | 0.95 | 0.93 | 257 |
| semantic (dense) | not measured | not measured | |
| hybrid (RRF) | not measured | not measured | |

Semantic and hybrid could not be scored: on 19-20 Nov the free embedding model timed out or returned 503 "Service is overloaded" for most queries. The eval counts a failed embedding as a miss rather than silently scoring keyword results as hybrid. In production the same outage makes hybrid fall back to keyword within about 7 s, so answers stay correct. Keyword's one miss is "Where is DarGlobal's London office?" (rank 7); in the agent, `company_info` reads the office facts from SQL, so the answer does not depend on this ranking.

**Eval data hygiene:** saved answers are PII-redacted, trace IDs are not stored, error messages print only the exception type, and raw per-question results (`eval/results/*.jsonl`) and the embedding cache are git-ignored. `eval/summary.py` writes only aggregate numbers to `app/eval_summary.json` (fixed schema, no questions or answers).

## Project structure

```
scraper/
  common.py             # polite HTTP client: robots.txt, 1 req/s throttle, retries, challenge detection
  darglobal.py          # DarGlobal project pages -> data/raw/darglobal.jsonl
  darglobal_content.py  # DarGlobal company pages + Jan-Sep 2026 news
  wasalt.py             # Wasalt sitemap sample -> data/raw/wasalt.jsonl
ingestion/
  normalize.py          # clean schema, dedupe, data quality report
  build_sqlite.py       # data/listings.db (listings, projects, company_facts, news)
  chunking.py           # section chunks with contextual headers, FAQ pairs, listing cards
  build_qdrant.py       # dense + BM25 vectors, payload indexes, HMAC signatures
app/
  config.py             # env vars, limits, reasoning mode, zero-spend guard
  llm.py                # OpenRouter client: model fallback, timeouts, usage and cost capture
  embeddings.py         # query embeddings with timeout and cache (hybrid falls back to keyword)
  sparse.py             # local BM25 sparse vectors
  retrieval.py          # keyword / semantic / hybrid (RRF), filters, diversity, small-to-big
  tools.py              # the 6 typed agent tools
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
  manual_eval.py        # 10-question chat with a follow-up
  edge_eval.py          # 14 list and table questions
  sanity_retrieval.py
eval/
  golden_set.jsonl      # 36 eval questions with labels (incl. 6 security probes)
  retrieval_eval.py     # Hit@5 / MRR: keyword vs semantic vs hybrid
  e2e_eval.py           # end-to-end agent eval + cost report -> eval/results/
  summary.py            # aggregate-only summary -> app/eval_summary.json
tests/
  test_security.py      # 24 security tests
  test_eval_grading.py  # 10 grader tests
  test_list_projects.py # 17 list-tool tests
assets/screenshots/     # README screenshots
wake/index.html         # wake-up page (GitHub Pages)
.github/workflows/refresh-data.yml   # scheduled data refresh (every 3 days)
Dockerfile, docker-compose.yml, render.yaml
requirements.in / requirements.txt   # direct / pinned dependencies
.env.example            # environment variable template
```

## What I learned

- **Free-tier quota is the real constraint, not money.** A 50 requests/day cap is shared by chat, embeddings and the eval itself. That led to blocking inputs before any LLM call, deterministic grading instead of an LLM judge, and caching query embeddings.
- **Search cannot promise a complete list.** "List all projects in Oman" returned 7 of 13 through top-k search. Lists and counts belong in SQL, which is why `list_projects` exists.
- **Reasoning helps choosing, hurts writing.** Thinking on the answer step was slow and ate the token budget; thinking off everywhere made the model skip tools and invent an email address. On for the tool step, off for the answer step, got both speed and accuracy.
- **Silent fallbacks can corrupt an evaluation.** When embeddings fail, hybrid falls back to keyword. That is right for users but wrong for an eval: the first run reported hybrid with exactly the keyword score. The eval now treats a degraded hybrid search as an error.
- **BM25 is strong on this corpus.** Queries name brands and places ("Pagani", "Fendi Casa", "Muscat"), so exact terms dominate; that is why the default is hybrid with RRF rather than dense-only.
- **Guardrails need to be precise, not just safe.** Replacing a whole answer for one bad sentence made 2 correct answers useless; trimming at sentence level kept both safety and usefulness.
- **Deterministic pre-LLM blocks beat prompt rules for security.** A fixed input guardrail is consistent, testable and free.

## Responsible scraping

- robots.txt is respected (Wasalt `/search` is disallowed and is not crawled; listings come from published sitemaps)
- About 1 request per second, identifiable User-Agent, capped volume
- No personal data (agent names, phone numbers) is collected; only role mailboxes (crm@, marketing@ ...) are kept
- Bot challenges (Cloudflare, Incapsula) are not bypassed; on a challenge the scraper retries a few times politely, then stops

## Limitations and next steps

- Data is a dated snapshot (refreshed every 3 days), not live inventory or prices
- Wasalt price, city and description are not in the data (bot-protected listing pages)
- Free models can be slow (median about 5 s) and are sometimes busy
- English works best; a simple Arabic question worked in testing, but Arabic search is not tuned
- Not investment, legal or mortgage advice
- Next: a Wasalt API or data feed, a second embedding provider, a reranker if tests show the need, and Arabic search
