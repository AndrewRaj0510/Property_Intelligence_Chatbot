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
_SIGNING_KEY = os.getenv("INDEX_SIGNING_KEY", "").encode()

_INJECTION = re.compile(
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|rules|prompts?)"
    r"|disregard (the |your |all )?(system|previous|above|prior)"
    r"|(reveal|print|show|repeat|output|leak) (me )?(your |the )?(system prompt|instructions|hidden prompt|api key)"
    r"|you are now |act as (an? )?(unrestricted|jailbroken|dan)\b|developer mode"
    r"|</?(system|assistant|user|tool_data)>|\[/?INST\]|<\|im_(start|end)\|>",
    re.I)
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
