"""Phase 0 smoke test: OpenRouter (tool calling per model), embeddings, Qdrant, Langfuse.

Usage:
    python scripts/smoke_test.py               # test everything
    python scripts/smoke_test.py --list-free   # list free OpenRouter models that support tools
"""
import json
import os
import sys

import httpx
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
OR_BASE = "https://openrouter.ai/api/v1"

TOOL = {
    "type": "function",
    "function": {
        "name": "search_listings",
        "description": "Search property listings by city and max price.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "max_price": {"type": "number"},
            },
            "required": ["city"],
        },
    },
}


def list_free_tool_models() -> None:
    models = httpx.get(f"{OR_BASE}/models", timeout=30).json()["data"]
    for m in models:
        if m["id"].endswith(":free") and "tools" in (m.get("supported_parameters") or []):
            print(f"{m['id']:60} ctx={m.get('context_length')}")


def check_openrouter() -> bool:
    key = os.getenv("OPENROUTER_API_KEY")
    models = [m.strip() for m in os.getenv("OPENROUTER_MODELS", "").split(",") if m.strip()]
    if not key or not models:
        print("[OpenRouter] SKIP: OPENROUTER_API_KEY or OPENROUTER_MODELS not set")
        return False
    client = OpenAI(base_url=OR_BASE, api_key=key, timeout=60)
    ok = True
    for model in models:
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Find apartments in Riyadh under 80000 SAR."}],
                tools=[TOOL],
            )
            calls = r.choices[0].message.tool_calls
            if calls:
                print(f"[OpenRouter] OK   {model}: {calls[0].function.name}({calls[0].function.arguments})")
            else:
                ok = False
                print(f"[OpenRouter] FAIL {model}: no tool call returned")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[OpenRouter] FAIL {model}: {type(e).__name__}: {e}")
    return ok


def check_embeddings() -> bool:
    key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_EMBED_MODEL", "nvidia/nemotron-3.5-embed-1b:free")
    if not key:
        print("[Embeddings] SKIP: OPENROUTER_API_KEY not set")
        return False
    try:
        r = OpenAI(base_url=OR_BASE, api_key=key, timeout=60).embeddings.create(
            model=model, input=["3-bed apartment for rent in Riyadh", "Missoni branded villas in Dubai"]
        )
        dims = [len(d.embedding) for d in r.data]
        print(f"[Embeddings] OK   {model}: {len(dims)} vectors, dim={dims[0]}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[Embeddings] FAIL {model}: {type(e).__name__}: {e}")
        return False


def check_qdrant() -> bool:
    url, key = os.getenv("QDRANT_URL"), os.getenv("QDRANT_API_KEY")
    if not url or not key:
        print("[Qdrant] SKIP: QDRANT_URL or QDRANT_API_KEY not set")
        return False
    from qdrant_client import QdrantClient

    try:
        cols = QdrantClient(url=url, api_key=key, timeout=30).get_collections().collections
        print(f"[Qdrant] OK   collections={[c.name for c in cols]}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[Qdrant] FAIL {type(e).__name__}: {e}")
        return False


def check_langfuse() -> bool:
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        print("[Langfuse] SKIP: keys not set")
        return False
    from langfuse import Langfuse

    try:
        ok = Langfuse().auth_check()
        print(f"[Langfuse] {'OK  ' if ok else 'FAIL'} auth_check={ok}")
        return bool(ok)
    except Exception as e:  # noqa: BLE001
        print(f"[Langfuse] FAIL {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    if "--list-free" in sys.argv:
        list_free_tool_models()
        sys.exit(0)
    results = {"openrouter": check_openrouter(), "embeddings": check_embeddings(), "qdrant": check_qdrant(), "langfuse": check_langfuse()}
    print(json.dumps(results))
    sys.exit(0 if all(results.values()) else 1)
