"""OpenRouter chat client: model fallback array, timeout, 429 handling, usage + cost capture.

Every call returns an `LLMResult` with token usage, actual cost (OpenRouter `usage.cost`, $0 on `:free`)
and the *equivalent* cost at the paid price of the same model (from OpenRouter `/models` pricing).
"""
import threading
import time
from dataclasses import dataclass, field

import httpx
from openai import APIStatusError, APITimeoutError, OpenAI, RateLimitError

from app import config

TIMEOUT_S = config.LLM_TIMEOUT_S
BUSY_MESSAGE = "The free AI model is busy right now (rate limit). Please try again in a minute."

_client: OpenAI | None = None
_pricing: dict[str, tuple[float, float]] | None = None  # model id -> (usd per prompt token, per completion token)


class LLMBusyError(RuntimeError):
    """All models rate-limited / unavailable. Message is safe to show to the user."""


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=config.OPENROUTER_BASE, api_key=config.OPENROUTER_API_KEY,
                         timeout=TIMEOUT_S, max_retries=0)
    return _client


def pricing() -> dict[str, tuple[float, float]]:
    """OpenRouter public model prices, fetched once. Empty dict if the endpoint is unreachable."""
    global _pricing
    if _pricing is None:
        _pricing = {}
        try:
            r = httpx.get(f"{config.OPENROUTER_BASE}/models", timeout=5)
            for m in r.json().get("data", []):
                p = m.get("pricing") or {}
                _pricing[m["id"]] = (float(p.get("prompt") or 0), float(p.get("completion") or 0))
        except Exception:  # noqa: BLE001 - cost estimate is best-effort
            pass
    return _pricing


def equivalent_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """USD cost of the same tokens on the paid variant of `model` (drops the `:free` suffix)."""
    prices = pricing()
    paid = prices.get(model.removesuffix(":free"))
    if paid is None or paid == (0.0, 0.0):
        return None
    return prompt_tokens * paid[0] + completion_tokens * paid[1]


@dataclass
class LLMResult:
    message: dict                      # assistant message in OpenAI dict format
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0              # actual (OpenRouter usage.cost)
    equivalent_cost_usd: float | None = None
    latency_ms: float = 0.0
    raw_usage: dict = field(default_factory=dict)
    reasoning_tokens: int = 0
    finish_reason: str | None = None


def chat(messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.1,
         tool_choice: str = "auto", max_tokens: int = config.MAX_ANSWER_TOKENS,
         reasoning: bool = False) -> LLMResult:
    """One chat completion. OpenRouter tries `models` in order server-side; we also retry once client-side
    on transient errors with the remaining models."""
    models = config.OPENROUTER_MODELS or ["nvidia/nemotron-3.5-super-vl:free"]
    last_err: Exception | None = None
    for start in range(len(models)):
        order = models[start:]
        extra: dict = {"usage": {"include": True}, "reasoning": {"enabled": reasoning}}
        if len(order) > 1:
            extra["models"] = order
        kwargs: dict = {"model": order[0], "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                        "extra_body": extra}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        t0 = time.perf_counter()
        try:
            r = client().chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError) as e:
            last_err = e
            continue
        except APIStatusError as e:
            if e.status_code in (402, 404, 429, 500, 502, 503, 504):
                last_err = e
                continue
            raise
        latency = (time.perf_counter() - t0) * 1000
        if not r.choices:  # OpenRouter sometimes returns an error object with 200
            last_err = RuntimeError(f"empty response: {getattr(r, 'error', None)}")
            continue
        msg = r.choices[0].message
        out: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            out["tool_calls"] = [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                                 for tc in msg.tool_calls]
        u = r.usage.model_dump() if r.usage else {}
        pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        model_used = r.model or order[0]
        return LLMResult(message=out, model=model_used, prompt_tokens=pt, completion_tokens=ct,
                         cost_usd=float(u.get("cost") or 0.0),
                         equivalent_cost_usd=equivalent_cost(model_used, pt, ct),
                         latency_ms=latency, raw_usage=u, finish_reason=r.choices[0].finish_reason,
                         reasoning_tokens=int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0))
    raise LLMBusyError(BUSY_MESSAGE) from last_err


# Warm the price table in the background so the first user turn never waits for it.
threading.Thread(target=pricing, daemon=True).start()
