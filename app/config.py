"""Central configuration: env vars, paths, model names."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CLEAN_DIR = DATA_DIR / "clean"
SQLITE_PATH = DATA_DIR / "listings.db"
CHUNKS_PATH = DATA_DIR / "chunks.jsonl"
EMBED_CACHE_PATH = DATA_DIR / ".cache" / "embeddings.npz"

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODELS = [m.strip() for m in os.getenv("OPENROUTER_MODELS", "").split(",") if m.strip()]
EMBED_MODEL = os.getenv("OPENROUTER_EMBED_MODEL") or "nvidia/nemotron-3.5-embed-1b:free"
EMBED_DIM = int(os.getenv("EMBED_DIM", "2048"))
EMBED_QUERY_TIMEOUT_S = float(os.getenv("EMBED_QUERY_TIMEOUT_S", "6"))  # query-time; then keyword fallback

QDRANT_URL = os.getenv("QDRANT_URL", "")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")            # read-write: ingestion only
# Runtime uses a read-only key when set (Qdrant Cloud: API key with read-only access). Falls back to the main key.
QDRANT_READ_API_KEY = os.getenv("QDRANT_READ_API_KEY", "") or QDRANT_API_KEY
QDRANT_TIMEOUT_S = int(os.getenv("QDRANT_TIMEOUT_S", "10"))
COLLECTION = os.getenv("QDRANT_COLLECTION", "property_kb")

def _snapshot_date() -> str:
    """Env override, else the date written by scripts/refresh_data.py, else the first snapshot."""
    if os.getenv("DATA_SNAPSHOT_DATE"):
        return os.environ["DATA_SNAPSHOT_DATE"]
    try:
        import json
        return json.loads((DATA_DIR / "index_stats.json").read_text())["snapshot_date"]
    except (OSError, ValueError, KeyError):
        return "18-19 Nov 2026"


SNAPSHOT_DATE = _snapshot_date()
MAX_TOOL_ROUNDS = 3
HISTORY_TURNS = 6
HISTORY_MSG_CHARS = 1200      # older assistant answers are truncated in history
TOOL_OUTPUT_CHARS = 6000      # per tool result sent to the LLM
TOOL_OUTPUT_CHARS_LIST = 14000  # list_projects returns COMPLETE lists (41 projects ~ 8k chars compact): never truncate
MAX_ANSWER_TOKENS = 2500     # 700 cut 10-row tables; 1200 cut the 41-project list. Only generated tokens cost time
# Reasoning ("thinking") per step. Measured 20 Nov:
# - tool-decision step: reasoning OFF + chat history -> model skipped tools and invented facts
#   (answered ir@darglobal.com instead of ir@darglobal.co.uk). ON -> correct tool 6/6. Costs ~1-3 s.
# - answer step (after tool results): reasoning ON burned 270-460 of 700 output tokens on hidden thinking
#   (answers cut off) and took 4-11 s; OFF -> 2.4 s, complete answers.
# "auto" = on for the first call of a turn (choose tools), off once tool results are in (write the answer).
LLM_REASONING = os.getenv("LLM_REASONING", "auto").lower()  # auto | on | off
LLM_TIMEOUT_S = int(os.getenv("LLM_TIMEOUT_S", "20"))  # per model attempt, then fall back to the next model
RATE_LIMIT_TURNS = int(os.getenv("RATE_LIMIT_TURNS", "8"))      # per session ...
RATE_LIMIT_WINDOW_S = int(os.getenv("RATE_LIMIT_WINDOW_S", "60"))  # ... per this many seconds
RATE_LIMIT_GLOBAL_TURNS = int(os.getenv("RATE_LIMIT_GLOBAL_TURNS", "30"))  # all sessions, per minute
LANGFUSE_ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))

# Zero-spend guard: only OpenRouter `:free` models may ever be called (credits exist on the account only to lift
# the free daily limit). A non-free model in the env is dropped here and never reaches the API.
ALLOW_PAID_MODELS = os.getenv("ALLOW_PAID_MODELS", "") == "1"
if not ALLOW_PAID_MODELS:
    OPENROUTER_MODELS = [m for m in OPENROUTER_MODELS if m.endswith(":free")]
    if not EMBED_MODEL.endswith(":free"):
        EMBED_MODEL = "nvidia/nemotron-3.5-embed-1b:free"
