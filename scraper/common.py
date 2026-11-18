"""Polite HTTP client shared by the scrapers.

- Custom, identifiable User-Agent
- robots.txt check per host (cached)
- Global throttle (default 1 request/second)
- Retries with exponential backoff on network errors, 429/5xx and bot-challenge stubs
"""
from __future__ import annotations

import gzip
import json
import logging
import time
import urllib.robotparser
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; PropertyChatbotResearch/0.1; educational take-home project)"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RAW_DIR = DATA_DIR / "raw"

log = logging.getLogger("scraper")


class BlockedError(RuntimeError):
    """Raised when a host keeps serving a bot-challenge page instead of content."""


class PoliteClient:
    def __init__(self, min_interval: float = 1.0, max_retries: int = 4, timeout: float = 30.0):
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en",
            },
            follow_redirects=True,
            timeout=timeout,
        )

    # -- robots ---------------------------------------------------------
    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._throttle()
                r = self.client.get(base + "/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])
            self._robots[base] = rp
        return rp.can_fetch(USER_AGENT, url)

    # -- fetching -------------------------------------------------------
    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, url: str, is_valid: Callable[[httpx.Response], bool] | None = None,
            check_robots: bool = True) -> httpx.Response:
        if check_robots and not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        last_err: Exception | None = None
        server_errors = 0
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(min(2 ** attempt, 20))
            self._throttle()
            try:
                r = self.client.get(url)
            except httpx.HTTPError as e:
                last_err = e
                log.warning("network error %s (attempt %d): %s", url, attempt + 1, e)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_err = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                server_errors += r.status_code >= 500
                if server_errors >= 3:  # persistent server error: give up early
                    break
                continue
            if r.status_code == 403 and r.headers.get("cf-mitigated") == "challenge":
                raise BlockedError(f"Cloudflare challenge on {url}")
            if is_valid is not None and not is_valid(r):
                last_err = BlockedError(f"invalid/challenge response for {url} ({len(r.content)} bytes)")
                continue
            r.raise_for_status()
            return r
        raise last_err or RuntimeError(url)

    def get_sitemap_locs(self, url: str) -> list[tuple[str, str | None]]:
        """Return (loc, lastmod) pairs from a (optionally gzipped) sitemap."""
        import re

        r = self.get(url, check_robots=False)
        body = r.content
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        text = body.decode("utf-8", "replace")
        out = []
        for block in re.findall(r"<url>(.*?)</url>", text, re.S):
            loc = re.search(r"<loc>(.*?)</loc>", block)
            mod = re.search(r"<lastmod>(.*?)</lastmod>", block)
            if loc:
                out.append((loc.group(1).strip(), mod.group(1).strip() if mod else None))
        return out


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
