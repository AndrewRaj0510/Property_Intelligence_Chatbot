"""Dense embeddings via OpenRouter (OpenAI-compatible), with retries and token accounting."""
import contextvars
import functools
import threading
import time

from openai import OpenAI

from app import config

_client: OpenAI | None = None
USAGE = {"calls": 0, "tokens": 0}  # process-wide running totals (ingestion stats)
_lock = threading.Lock()
# Per-turn counter: run_turn sets a fresh dict; LangGraph copies the context into node threads, so concurrent
# Streamlit sessions no longer mix up each other's embedding tokens.
TURN_USAGE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("embed_turn_usage", default=None)


def _record(tokens: int) -> None:
    with _lock:
        USAGE["calls"] += 1
        USAGE["tokens"] += tokens
    turn = TURN_USAGE.get()
    if turn is not None:
        turn["calls"] = turn.get("calls", 0) + 1
        turn["tokens"] = turn.get("tokens", 0) + tokens


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=config.OPENROUTER_BASE, api_key=config.OPENROUTER_API_KEY, timeout=60,
                         max_retries=0)  # our own backoff loop below; SDK retries stacked on top added ~6 s per failed query
    return _client


def embed(texts: list[str], retries: int = 8, timeout: float | None = None) -> list[list[float]]:
    """Embed a batch of texts. Retries with exponential backoff on rate limits / transient errors."""
    delay = 2.0
    for attempt in range(retries):
        try:
            c = client() if timeout is None else client().with_options(timeout=timeout)
            r = c.embeddings.create(model=config.EMBED_MODEL, input=texts)
            _record(getattr(r.usage, "total_tokens", 0) or getattr(r.usage, "prompt_tokens", 0) or 0)
            vecs = [d.embedding for d in sorted(r.data, key=lambda d: d.index)]
            if len(vecs) != len(texts):
                raise RuntimeError(f"expected {len(texts)} vectors, got {len(vecs)}")
            return vecs
        except Exception:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60)
    raise RuntimeError("unreachable")


@functools.lru_cache(maxsize=512)
def _embed_query_cached(text: str) -> tuple[float, ...]:
    return tuple(embed([text], retries=1, timeout=config.EMBED_QUERY_TIMEOUT_S)[0])


def embed_query(text: str) -> list[float]:
    """Few retries at query time: a user is waiting; retrieval falls back to keyword search on failure.
    Short timeout: if the embedding provider hangs, fall back to keyword search fast.
    LRU-cached (failures are not cached): repeated / example questions skip the embedding round trip."""
    return list(_embed_query_cached(" ".join(text.split()).lower()))
