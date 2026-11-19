"""Dense embeddings via OpenRouter (OpenAI-compatible), with retries and token accounting."""
import time

from openai import OpenAI

from app import config

_client: OpenAI | None = None
USAGE = {"calls": 0, "tokens": 0}  # running totals for cost capture


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=config.OPENROUTER_BASE, api_key=config.OPENROUTER_API_KEY, timeout=60)
    return _client


def embed(texts: list[str], retries: int = 8) -> list[list[float]]:
    """Embed a batch of texts. Retries with exponential backoff on rate limits / transient errors."""
    delay = 2.0
    for attempt in range(retries):
        try:
            r = client().embeddings.create(model=config.EMBED_MODEL, input=texts)
            USAGE["calls"] += 1
            USAGE["tokens"] += getattr(r.usage, "total_tokens", 0) or getattr(r.usage, "prompt_tokens", 0) or 0
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


def embed_query(text: str) -> list[float]:
    # Few retries at query time: a user is waiting; retrieval falls back to keyword search on failure.
    return embed([text], retries=2)[0]
