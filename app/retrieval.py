"""Retrieval over the Qdrant `property_kb` collection.

Three modes (kept for evaluation, architecture.md section 6):
- keyword:  BM25 sparse vector only
- semantic: dense vector only
- hybrid:   both prefetched (top 20 each), fused with Reciprocal Rank Fusion (default)

Payload pre-filters (doc_type, country, city, brand, project, property_type, source, date range) are applied
inside each prefetch, before ranking. Post-processing: at most `max_per_parent` chunks per project/page
(diversity), and small-to-big expansion (a project's summary card is added when 2+ of its chunks match).
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from qdrant_client import QdrantClient, models

from app import config, security
from app.embeddings import embed_query
from app.sparse import encode_query

Mode = Literal["keyword", "semantic", "hybrid"]
PREFETCH_K = 20
_NS = uuid.UUID("6f1c2b4e-6d0a-4c1e-9b8e-2f0c5a7d9e11")

# Lower-cased keyword fields used for filtering (display values stay in the normal payload fields).
FILTER_FIELDS = ["doc_type", "source", "country_key", "city_key", "brand_key", "project_key", "ptype_key", "parent_id"]
ALIASES = {
    "uae": "united arab emirates", "emirates": "united arab emirates",
    "ksa": "saudi arabia", "saudi": "saudi arabia",
    "uk": "united kingdom", "england": "united kingdom", "britain": "united kingdom",
    "ras al khaimah": "al marjan island", "rak": "al marjan island",
    "makkah": "mecca", "jedda": "jeddah",
}

_client: QdrantClient | None = None
_write_client: QdrantClient | None = None
REJECTED = {"hash": 0, "domain": 0}  # running counters of dropped hits (index tampering / poisoning)


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_NS, chunk_id))


def key(value: str | None) -> str | None:
    if value is None:
        return None
    v = " ".join(value.lower().split())
    return ALIASES.get(v, v)


def client() -> QdrantClient:
    """Runtime (query) client: read-only key when configured, short timeout."""
    global _client
    if _client is None:
        _client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_READ_API_KEY,
                               timeout=config.QDRANT_TIMEOUT_S)
    return _client


def write_client() -> QdrantClient:
    """Ingestion-only client with the read-write key. Never used by the app at runtime."""
    global _write_client
    if _write_client is None:
        _write_client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY, timeout=60)
    return _write_client


def _trusted(p) -> bool:
    """Drop points that fail the source allowlist or whose content hash doesn't verify."""
    pl = p.payload or {}
    if not security.is_allowed_url(pl.get("url", "")):
        REJECTED["domain"] += 1
        return False
    if not security.verify_hash(pl.get("text", ""), pl.get("url", ""), pl.get("content_hash")):
        REJECTED["hash"] += 1
        return False
    return True


@dataclass
class Hit:
    id: str
    score: float
    text: str
    url: str
    doc_type: str
    payload: dict
    expanded: bool = False  # added by small-to-big, not ranked


@dataclass
class SearchResult:
    hits: list[Hit]
    timings_ms: dict[str, float] = field(default_factory=dict)


def build_filter(doc_type=None, source=None, country=None, city=None, brand=None, project=None,
                 property_type=None, date_from=None, date_to=None) -> models.Filter | None:
    must: list[models.Condition] = []
    for fname, val in [("doc_type", doc_type), ("source", source), ("country_key", key(country)),
                       ("city_key", key(city)), ("brand_key", key(brand)), ("project_key", key(project)),
                       ("ptype_key", key(property_type))]:
        if val:
            must.append(models.FieldCondition(key=fname, match=models.MatchValue(value=val)))
    if date_from or date_to:
        must.append(models.FieldCondition(key="date", range=models.DatetimeRange(gte=date_from, lte=date_to)))
    return models.Filter(must=must) if must else None


def _to_hit(p) -> Hit:
    pl = p.payload or {}
    return Hit(id=pl.get("chunk_id", str(p.id)), score=float(getattr(p, "score", None) or 0.0), text=pl.get("text", ""),
               url=pl.get("url", ""), doc_type=pl.get("doc_type", ""), payload=pl)


def search(query: str, mode: Mode = "hybrid", k: int = 6, *, max_per_parent: int | None = 2,
           expand: bool = True, **filters) -> SearchResult:
    """Search the knowledge base. `filters`: see build_filter (doc_type, country, city, brand, ...)."""
    t: dict[str, float] = {}
    flt = build_filter(**filters)
    fetch_k = k * 4 if max_per_parent else k
    qc = client()

    dense = sparse = None
    if mode in ("semantic", "hybrid"):
        t0 = time.perf_counter()
        try:
            dense = embed_query(query)
        except Exception:  # noqa: BLE001 - embedding API down: hybrid degrades to keyword, semantic cannot
            if mode == "semantic":
                raise
            mode = "keyword"
            t["embed_failed"] = 1
        t["embed"] = (time.perf_counter() - t0) * 1000
    if mode in ("keyword", "hybrid"):
        idx, val = encode_query(query)
        sparse = models.SparseVector(indices=idx, values=val)
        if not idx:  # query is all stopwords
            if mode == "keyword":
                return SearchResult([], t)
            sparse = None

    t0 = time.perf_counter()
    if mode == "hybrid" and sparse is not None:
        res = qc.query_points(
            config.COLLECTION,
            prefetch=[models.Prefetch(query=sparse, using="bm25", limit=PREFETCH_K, filter=flt),
                      models.Prefetch(query=dense, using="dense", limit=PREFETCH_K, filter=flt)],
            query=models.FusionQuery(fusion=models.Fusion.RRF), limit=fetch_k, with_payload=True)
    elif mode == "keyword":
        res = qc.query_points(config.COLLECTION, query=sparse, using="bm25", query_filter=flt,
                              limit=fetch_k, with_payload=True)
    else:
        res = qc.query_points(config.COLLECTION, query=dense, using="dense", query_filter=flt,
                              limit=fetch_k, with_payload=True)
    t["qdrant"] = (time.perf_counter() - t0) * 1000
    hits = [_to_hit(p) for p in res.points if _trusted(p)]
    if len(hits) < len(res.points):
        t["rejected"] = len(res.points) - len(hits)

    if max_per_parent:
        per: dict[str, int] = {}
        kept = []
        for h in hits:
            pid = h.payload.get("parent_id", h.id)
            if per.get(pid, 0) < max_per_parent:
                per[pid] = per.get(pid, 0) + 1
                kept.append(h)
        hits = kept
    hits = hits[:k]

    if expand:
        t0 = time.perf_counter()
        hits += _expand_summaries(hits)
        t["expand"] = (time.perf_counter() - t0) * 1000
    t["total"] = sum(v for name, v in t.items() if name not in ("embed_failed", "rejected"))
    return SearchResult(hits, t)


def _expand_summaries(hits: list[Hit]) -> list[Hit]:
    counts: dict[str, int] = {}
    have = {h.id for h in hits}
    for h in hits:
        if h.doc_type == "project":
            counts[h.payload["parent_id"]] = counts.get(h.payload["parent_id"], 0) + 1
    need = [f"{pid}#summary" for pid, n in counts.items() if n >= 2 and f"{pid}#summary" not in have]
    if not need:
        return []
    pts = client().retrieve(config.COLLECTION, ids=[point_id(c) for c in need], with_payload=True)
    out = []
    for p in pts:
        if not _trusted(p):
            continue
        h = _to_hit(p)
        h.expanded = True
        out.append(h)
    return out


def get_chunks(parent_id: str) -> list[Hit]:
    """All chunks of one project/page/article (for get_project). Summary first, then section order."""
    pts, _ = client().scroll(config.COLLECTION, scroll_filter=build_filter_parent(parent_id),
                             limit=200, with_payload=True)
    hits = [_to_hit(p) for p in pts if _trusted(p)]
    return sorted(hits, key=lambda h: (h.payload.get("chunk_kind") != "summary", h.payload.get("order", 0)))


def build_filter_parent(parent_id: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key="parent_id", match=models.MatchValue(value=parent_id))])
