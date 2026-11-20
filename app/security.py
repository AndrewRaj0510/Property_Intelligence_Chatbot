"""RAG security: source allowlist, index integrity, ingestion-time content scanning, PII redaction,
per-session rate limiting. Pure Python, no extra dependencies.

Threats covered (see findings.md "RAG security"):
- Index poisoning / tampering: every point carries `content_hash` = HMAC-SHA256(text + url) computed at
  index time; hits whose hash doesn't verify, or whose URL is outside the source allowlist, are dropped.
- Indirect prompt injection in scraped pages: scanned and neutralised at ingestion AND at query time.
- PII leakage to third parties (LLM provider, Langfuse): emails / phone numbers typed by users are
  redacted before they leave the process. Public role mailboxes of DarGlobal are kept.
- Abuse of the free LLM quota: sliding-window rate limit per session.
"""
import hashlib
import hmac
import os
import re
import threading
import time
from collections import deque
from urllib.parse import urlsplit

ALLOWED_DOMAINS = {"darglobal.co.uk", "wasalt.sa"}
try:  # load .env here too: the key must not depend on which module happens to be imported first
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass
_SIGNING_KEY = os.getenv("INDEX_SIGNING_KEY", "").encode()

_INJECTION = re.compile(
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|rules|prompts?)"
    r"|disregard (the |your |all )?(system|previous|above|prior)"
    r"|(reveal|print|show|repeat|output|leak) (me )?(your |the )?(system prompt|instructions|hidden prompt|api key)"
    r"|you are now |act as (an? )?(unrestricted|jailbroken|dan)\b|developer mode"
    r"|</?(system|assistant|user|tool_data)>|\[/?INST\]|<\|im_(start|end)\|>"
    # prompt / internals extraction
    r"|(repeat|print|output|copy) (everything|all|the text|what'?s?) (above|before|previous)"
    r"|\bverbatim\b.{0,40}\b(instructions|prompt|rules|above)\b"
    r"|(source|python) code|(your|the) (tools?'?|internal|backend) (code|implementation|source)"
    r"|(api|secret|access) keys?\b|environment variables|\.env\b",
    re.I)
# Requests for personal data of private individuals (agents, brokers, owners): never collected, always refused.
_PERSONAL_DATA = re.compile(
    r"(names?|phone|mobile|whatsapp|numbers?|emails?|contacts?|contact details)\b.{0,40}"
    r"\b(agents?|brokers?|owners?|landlords?|sellers?|realtors?)\b"
    r"|\b(agents?|brokers?|owners?|landlords?|sellers?)'?s?\b.{0,20}\b(phone|mobile|whatsapp|numbers?|emails?|contact)",
    re.I)


def asks_personal_data(text: str) -> bool:
    # DarGlobal's own public role contacts (sales/agents mailbox, offices) are company data, not personal data.
    return bool(_PERSONAL_DATA.search(text)) and "darglobal" not in text.lower()
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_HTML = re.compile(r"<\s*(script|style|iframe)[^>]*>.*?<\s*/\s*\1\s*>|<!--.*?-->", re.I | re.S)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{7,}\d(?![\w/])")
_ROLE_DOMAINS = ("darglobal.co.uk", "buchanancomms.co.uk")


# ------------------------------------------------------------------ allowlist + integrity
def is_allowed_url(url: str) -> bool:
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def content_hash(text: str, url: str) -> str:
    msg = f"{url}\n{text}".encode("utf-8")
    if _SIGNING_KEY:
        return "hmac:" + hmac.new(_SIGNING_KEY, msg, hashlib.sha256).hexdigest()
    return "sha256:" + hashlib.sha256(msg).hexdigest()


def verify_hash(text: str, url: str, stored: str | None) -> bool:
    return bool(stored) and hmac.compare_digest(stored, content_hash(text, url))


# ------------------------------------------------------------------ content scanning
def scan_text(text: str) -> tuple[str, dict]:
    """Neutralise hidden characters, embedded HTML/script and injection phrases. Returns (clean, flags)."""
    flags = {"control_chars": len(_CONTROL.findall(text)), "html": len(_HTML.findall(text))}
    text = _HTML.sub(" ", _CONTROL.sub("", text))
    text, flags["injection"] = _INJECTION.subn("[removed]", text)
    return text, {k: v for k, v in flags.items() if v}


def has_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def strip_control(text: str) -> str:
    return _CONTROL.sub("", text)


# ------------------------------------------------------------------ PII
def redact_pii(text: str) -> str:
    def email(m: re.Match) -> str:
        return m.group(0) if m.group(0).lower().endswith(_ROLE_DOMAINS) else "[email removed]"

    def phone(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[phone removed]" if len(digits) >= 9 else m.group(0)  # short digit runs (prices, sizes) kept
    text = _EMAIL.sub(email, text)
    return _PHONE.sub(phone, text)


# ------------------------------------------------------------------ output leak detection
# Used at runtime (finalize node blocks the answer) and by the eval (leak-free rate must be 100%).
_SECRET_ENV = ("OPENROUTER_API_KEY", "QDRANT_API_KEY", "QDRANT_READ_API_KEY", "LANGFUSE_SECRET_KEY",
               "LANGFUSE_PUBLIC_KEY", "INDEX_SIGNING_KEY")
_LEAK_PATTERNS = {
    "secret": re.compile(
        r"\bsk-or-[\w-]{10,}|\bsk-[A-Za-z0-9]{20,}|\b[ps]k-lf-[\w-]{8,}|\beyJ[\w-]{10,}\.[\w-]{10,}\.|"
        r"\b(?:" + "|".join(_SECRET_ENV) + r")\b|\bBearer\s+[\w.-]{16,}", re.I),
    "code": re.compile(
        r"Traceback \(most recent call last\)|File \"[^\"]+\.py\", line \d+|"
        r"\b(?:app|ingestion|scraper|scripts|eval)/\w+\.py\b|"
        r"```[a-z]*\s*\n\s*(?:import \w|from [\w.]+ import|def \w+\(|class \w+[(:])", re.I),
    "internal": re.compile(r"<\s*/?\s*tool_data\b|\bcontent_hash\b|\bchunk_id\b|\bparent_id\b", re.I),
}


def _secret_values() -> list[str]:
    vals = [os.getenv(n, "") for n in _SECRET_ENV]
    vals.append(urlsplit(os.getenv("QDRANT_URL", "")).hostname or "")  # private cluster host
    return [v for v in vals if len(v) >= 12]


def find_leaks(text: str) -> list[str]:
    """Categories of leaked material in `text`: secret (key patterns, env names, or any configured secret value
    verbatim), code (source, stack traces, repo paths), internal (tool wrappers, index field names),
    pii (personal email / phone that redact_pii would remove)."""
    found = [cat for cat, rx in _LEAK_PATTERNS.items() if rx.search(text)]
    if "secret" not in found and any(v in text for v in _secret_values()):
        found.insert(0, "secret")
    if redact_pii(text) != text:
        found.append("pii")
    return found


def mask_for_logs(*, data, **_):
    """Langfuse `mask` hook: redact PII from everything exported to Langfuse."""
    if isinstance(data, str):
        return redact_pii(data)
    if isinstance(data, dict):
        return {k: mask_for_logs(data=v) for k, v in data.items()}
    if isinstance(data, list):
        return [mask_for_logs(data=v) for v in data]
    return data


# ------------------------------------------------------------------ rate limiting
class RateLimiter:
    """Sliding window: at most `limit` turns per `window_s` per key. In-memory (single container)."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window}
            return True
