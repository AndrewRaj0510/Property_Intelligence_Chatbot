"""Local BM25 sparse encoder (no model download, no extra dependency).

Documents get BM25 term-frequency saturation weights; queries get weight 1 per term.
The collection's sparse vector uses Qdrant's `Modifier.IDF`, so Qdrant applies IDF
from its own corpus statistics at query time: score = sum(idf(t) * tf_weight(t, d)).
Term ids are a stable 31-bit CRC32 hash of the token.
"""
import re
import zlib
from collections import Counter

K1, B = 1.2, 0.75
_TOKEN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")
STOPWORDS = set(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with "
    "what which who whom how do does did i me my you your we our us they their them there these those "
    "can could would should any all some about into than then so if not no yes please tell show find give "
    "list me get".split()
)


def _stem(tok: str) -> str:
    # Light plural folding: villas -> villa, properties -> property. Keeps names like "Missoni" intact.
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    toks = (t.replace("'", "") for t in _TOKEN.findall(text.lower()))
    return [_stem(t) for t in toks if t and t not in STOPWORDS]


def _term_id(tok: str) -> int:
    return zlib.crc32(tok.encode("utf-8")) & 0x7FFFFFFF


def _to_sparse(weights: dict[int, float]) -> tuple[list[int], list[float]]:
    items = sorted(weights.items())
    return [i for i, _ in items], [w for _, w in items]


def encode_doc(text: str, avgdl: float) -> tuple[list[int], list[float]]:
    toks = tokenize(text)
    dl = len(toks) or 1
    weights: dict[int, float] = {}
    for tok, tf in Counter(toks).items():
        tid = _term_id(tok)
        weights[tid] = weights.get(tid, 0.0) + tf * (K1 + 1) / (tf + K1 * (1 - B + B * dl / avgdl))
    return _to_sparse(weights)


def encode_query(text: str) -> tuple[list[int], list[float]]:
    return _to_sparse({_term_id(t): 1.0 for t in set(tokenize(text))})
