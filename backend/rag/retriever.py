"""Retrieve relevant strategy context from the Qdrant vector store."""

from __future__ import annotations

import logging
from typing import Any

from backend.core.config import settings
from backend.rag.embeddings import embed_texts
from backend.rag.store import ensure_collection, get_client

log = logging.getLogger(__name__)


def retrieve_context(query: str, top_k: int | None = None, score_threshold: float | None = None) -> list[dict[str, Any]]:
    """Return the most relevant strategy chunks for ``query``.

    Each item: ``{"text": str, "source": str, "chunk_index": int, "score": float}``.
    """
    top_k = top_k or settings.rag_top_k
    score_threshold = score_threshold if score_threshold is not None else settings.rag_score_threshold

    client = get_client()
    ensure_collection(client)

    vector = embed_texts([query])[0]

    hits = client.query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        limit=top_k,
        score_threshold=score_threshold,
    ).points

    results: list[dict[str, Any]] = []
    for hit in hits:
        payload = hit.payload or {}
        results.append(
            {
                "text": payload.get("text", ""),
                "source": payload.get("source", ""),
                "chunk_index": payload.get("chunk_index", 0),
                "score": hit.score,
            }
        )
    return results
