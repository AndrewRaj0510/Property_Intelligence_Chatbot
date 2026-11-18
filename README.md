# Property Intelligence Chatbot

An agentic RAG chatbot that answers natural-language questions about **DarGlobal** projects and **Wasalt** (Saudi Arabia) property listings, grounded in scraped public data, with a source link for every answer.

> **Status:** work in progress. Phase 0 (setup) is complete. Scraping, indexing, agent, UI and deployment are next.

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

## Project structure

```
scripts/
  smoke_test.py      # Phase 0 connectivity checks
requirements.in      # direct dependencies
requirements.txt     # pinned dependencies (pip freeze)
.env.example         # environment variable template
```

Coming next: `scraper/`, `ingestion/`, `app/`, `eval/`, `Dockerfile`.

## Responsible scraping

- robots.txt is respected (Wasalt `/search` is disallowed and is not crawled; listings come from published sitemaps)
- About 1 request per second, identifiable User-Agent, capped volume
- No personal data (agent names, phone numbers) is collected

## Limitations

- Data is a dated snapshot, not live inventory or prices
- English-first; Arabic queries may be weaker
- Free models can be slow or rate-limited
- Not investment, legal or mortgage advice
