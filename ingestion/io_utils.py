"""Shared helpers for reading the clean JSONL files."""
import json

from app.config import CLEAN_DIR


def load(name: str) -> list[dict]:
    with open(CLEAN_DIR / f"{name}.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
