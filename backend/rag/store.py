"""Qdrant vector store wrapper for strategy-document RAG."""

from __future__ import annotations

import logging

from qdrant_client import QdrantClient

from backend.core.config import settings

log = logging.getLogger(__name__)


class VectorStoreError(Exception):
    """Raised when a Qdrant operation fails."""


def get_client() -> QdrantClient:
    """Return a Qdrant client bound to the configured URL (lazy).

    Handles both self-hosted Qdrant (no key) and hosted Qdrant Cloud (api_key).
    """
    try:
        # check_compatibility=False silences the major-version warning when the
        # locally pinned qdrant-client is newer than the running server.
        return QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            check_compatibility=False,
            # cloud_inference=True tells the SDK to send raw text (Document)
            # to the server instead of embedding locally with fastembed.
            cloud_inference=settings.server_side_embeddings,
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError(f"cannot reach Qdrant at {settings.qdrant_url}: {exc}") from exc


def ensure_collection(client: QdrantClient) -> None:
    """Create the strategy-docs collection if it does not already exist."""
    collection = settings.qdrant_collection
    try:
        existing = client.get_collections().collections
        if not any(c.name == collection for c in existing):
            client.create_collection(
                collection_name=collection,
                vectors_config={
                    "size": settings.embedding_dim,
                    "distance": "Cosine",
                },
            )
            log.info("created Qdrant collection %r (dim=%d)", collection, settings.embedding_dim)
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError(f"ensure_collection failed: {exc}") from exc


def delete_all_documents(client: QdrantClient) -> None:
    """Wipe all points from the collection (re-ingest helper)."""
    from qdrant_client.models import Filter, FilterSelector

    try:
        client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=FilterSelector(filter=Filter(must=[])),
        )
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError(f"delete_all_documents failed: {exc}") from exc
