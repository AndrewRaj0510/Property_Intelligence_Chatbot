"""Embed data/chunks.jsonl and (re)build the Qdrant hybrid collection.

Collection `property_kb`: named dense vector `dense` (cosine) + sparse vector `bm25` (IDF modifier),
payload indexes on the filter fields. Embeddings are cached in data/.cache/embeddings.npz keyed by
(model, text) hash, so re-runs only embed new or changed chunks (free-tier friendly).

Usage: python -m ingestion.build_qdrant   (always drops and recreates the collection)
"""
import hashlib
import json
import sys
import time

import numpy as np
from qdrant_client import models

from app import config
from app.embeddings import USAGE, embed
from app import security
from app.retrieval import FILTER_FIELDS, key, point_id, write_client
from app.sparse import encode_doc, tokenize

BATCH_EMBED, BATCH_UPSERT = 32, 64


def _hash(text: str) -> str:
    return hashlib.sha1(f"{config.EMBED_MODEL}\n{text}".encode("utf-8")).hexdigest()


def load_cache() -> dict[str, np.ndarray]:
    if config.EMBED_CACHE_PATH.exists():
        with np.load(config.EMBED_CACHE_PATH) as z:
            return {k: z[k] for k in z.files}
    return {}


def save_cache(cache: dict[str, np.ndarray]) -> None:
    config.EMBED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(config.EMBED_CACHE_PATH, **cache)


def embed_all(texts: list[str]) -> list[np.ndarray]:
    cache = load_cache()
    todo = [t for t in dict.fromkeys(texts) if _hash(t) not in cache]
    print(f"embeddings: {len(texts) - len(todo)} cached, {len(todo)} to embed")
    for i in range(0, len(todo), BATCH_EMBED):
        batch = todo[i:i + BATCH_EMBED]
        for t, v in zip(batch, embed(batch)):
            cache[_hash(t)] = np.asarray(v, dtype=np.float32)
        if (i // BATCH_EMBED) % 5 == 4:
            save_cache(cache)
        print(f"  embedded {min(i + BATCH_EMBED, len(todo))}/{len(todo)}")
    save_cache(cache)
    return [cache[_hash(t)] for t in texts]


def payload(c: dict, order: int) -> dict:
    p = {k: v for k, v in c.items() if k != "id"}
    p["chunk_id"] = c["id"]
    p["content_hash"] = security.content_hash(c["text"], c["url"])  # verified at query time
    p["order"] = order
    p["country_key"] = key(c.get("country"))
    p["city_key"] = key(c.get("city"))
    p["brand_key"] = [key(b) for b in c.get("brand") or []]
    p["project_key"] = key(c.get("project_name"))
    p["ptype_key"] = [key(t) for t in c.get("property_type") or []]
    return p


def main() -> None:
    chunks = [json.loads(line) for line in open(config.CHUNKS_PATH, encoding="utf-8")]
    t0 = time.perf_counter()
    vectors = embed_all([c["text"] for c in chunks])
    dim = len(vectors[0])
    avgdl = sum(len(tokenize(c["text"])) for c in chunks) / len(chunks)

    qc = write_client()
    if qc.collection_exists(config.COLLECTION):
        qc.delete_collection(config.COLLECTION)
    qc.create_collection(
        config.COLLECTION,
        vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    for f in FILTER_FIELDS:
        qc.create_payload_index(config.COLLECTION, f, models.PayloadSchemaType.KEYWORD)
    qc.create_payload_index(config.COLLECTION, "date", models.PayloadSchemaType.DATETIME)

    orders: dict[str, int] = {}
    points = []
    for c, v in zip(chunks, vectors):
        orders[c["parent_id"]] = orders.get(c["parent_id"], -1) + 1
        idx, val = encode_doc(c["text"], avgdl)
        points.append(models.PointStruct(
            id=point_id(c["id"]),
            vector={"dense": v.tolist(), "bm25": models.SparseVector(indices=idx, values=val)},
            payload=payload(c, orders[c["parent_id"]]),
        ))
    for i in range(0, len(points), BATCH_UPSERT):
        qc.upsert(config.COLLECTION, points=points[i:i + BATCH_UPSERT], wait=True)
    n = qc.count(config.COLLECTION, exact=True).count
    stats = {"points": n, "dim": dim, "avgdl": round(avgdl, 2), "embed_model": config.EMBED_MODEL,
             "embed_calls": USAGE["calls"], "embed_tokens": USAGE["tokens"],
             "seconds": round(time.perf_counter() - t0, 1),
             "signed": "hmac" if security._SIGNING_KEY else "sha256"}
    (config.DATA_DIR / "index_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats))


if __name__ == "__main__":
    if "--help" in sys.argv:
        print(__doc__)
    else:
        main()
