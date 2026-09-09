"""Search stage builders and hybrid result fusion.

Everything that knows about the difference between Automated Embedding and
self-computed vectors lives here. Tool code calls `vector_stage()` and stays the
same in both modes.
"""

from __future__ import annotations

from typing import Any

import httpx2

from app.config import get_settings

VOYAGE_EMBED_URL = "https://api.voyageai.com/v1/embeddings"


async def embed_query(text: str) -> list[float]:
    """Compute one query vector. Only used in "explicit" mode.

    In "auto" mode Atlas does this internally and this function is never called.
    """
    settings = get_settings()
    if not settings.voyage_api_key:
        raise RuntimeError(
            "embedding_mode is 'explicit' but VOYAGE_API_KEY is not set. "
            "Either set the key or switch embedding_mode back to 'auto'."
        )

    async with httpx2.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            VOYAGE_EMBED_URL,
            headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
            json={
                "input": [text],
                "model": settings.voyage_model,
                # Voyage embeds queries and documents slightly differently.
                # Getting this wrong quietly degrades recall.
                "input_type": "query",
            },
        )
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]


async def embed_documents(texts: list[str], *, batch_size: int = 96) -> list[list[float]]:
    """Compute document vectors. Only used by the seed script in "explicit" mode.

    Voyage caps how much can be sent per request, so texts go in batches. The
    `input_type` differs from `embed_query`: Voyage encodes documents and queries
    asymmetrically, and mixing the two silently degrades recall.
    """
    settings = get_settings()
    if not settings.voyage_api_key:
        raise RuntimeError(
            "embedding_mode is 'explicit' but VOYAGE_API_KEY is not set. "
            "Either set the key or switch embedding_mode back to 'auto'."
        )

    vectors: list[list[float]] = []
    async with httpx2.AsyncClient(timeout=120.0) as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = await client.post(
                VOYAGE_EMBED_URL,
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={
                    "input": batch,
                    "model": settings.voyage_model,
                    "input_type": "document",
                },
            )
            response.raise_for_status()
            payload = response.json()["data"]
            # Voyage returns an "index" per item; sort rather than trust order.
            vectors.extend(item["embedding"] for item in sorted(payload, key=lambda d: d["index"]))

    return vectors


async def vector_stage(
    *,
    index: str,
    query_text: str,
    limit: int,
    num_candidates: int | None = None,
    filters: dict[str, Any] | None = None,
) -> dict:
    """Build a `$vectorSearch` stage for whichever embedding mode is configured.

    `num_candidates` controls how many approximate neighbours Atlas considers
    before ranking. MongoDB recommends roughly 10x to 20x the limit; too low
    hurts recall, too high costs latency.
    """
    settings = get_settings()
    stage: dict[str, Any] = {
        "index": index,
        "limit": limit,
        "numCandidates": num_candidates or max(limit * 15, 100),
    }

    if settings.embedding_mode == "auto":
        # Atlas embeds the query text itself against the model named in the index.
        stage["path"] = "embedding_source"
        stage["query"] = query_text
    else:
        stage["path"] = "embedding"
        stage["queryVector"] = await embed_query(query_text)

    if filters:
        stage["filter"] = filters

    return {"$vectorSearch": stage}


def text_stage(*, index: str, query_text: str, paths: list[str], limit: int) -> list[dict]:
    """Build the keyword half of hybrid search.

    Returned as a list because it needs a `$limit` after `$search`; `$search` has
    no limit option of its own.
    """
    return [
        {
            "$search": {
                "index": index,
                "text": {"query": query_text, "path": paths},
            }
        },
        {"$limit": limit},
    ]


def rrf_merge(
    ranked_lists: list[list[dict]],
    *,
    key: str,
    weights: list[float] | None = None,
    k: int = 60,
    limit: int = 10,
) -> list[dict]:
    """Combine several ranked result lists with Reciprocal Rank Fusion.

    RRF scores a document by 1 / (k + rank) summed across the lists it appears
    in. It needs no score normalisation, which matters here because a vector
    similarity and a Lucene relevance score are not on comparable scales.

    Fusion runs in Python rather than through MongoDB's `$rankFusion` so the
    behaviour is identical on any cluster tier and is easy to unit test.

    Args:
        ranked_lists: Result lists, each already ordered best first.
        key: Field that identifies a document across lists, e.g. "sku".
        weights: Per-list multipliers. Defaults to equal weighting.
        k: RRF damping constant. 60 is the value from the original paper.
        limit: How many merged results to return.
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights must have one entry per ranked list")

    scores: dict[Any, float] = {}
    documents: dict[Any, dict] = {}

    for documents_list, weight in zip(ranked_lists, weights, strict=True):
        for rank, document in enumerate(documents_list):
            identifier = document.get(key)
            if identifier is None:
                continue
            scores[identifier] = scores.get(identifier, 0.0) + weight / (k + rank + 1)
            # Keep the richest copy: later lists may carry fields earlier ones lack.
            documents.setdefault(identifier, document)

    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    merged = []
    for identifier, score in ordered[:limit]:
        document = dict(documents[identifier])
        document["_score"] = round(score, 6)
        merged.append(document)
    return merged
