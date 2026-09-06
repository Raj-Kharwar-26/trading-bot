"""Shared embedding helper.

Wraps ``fastembed`` (local, no API key) which is the embedding source for RAG.
Disables the hf-xet CDN downloader (which is flaky on some networks) before the
model loads; issues are only relevant on first-time model download.

When ``server_side_embeddings`` is enabled, embedding is delegated to the vector
store (Qdrant Cloud Inference) and fastembed is never imported — this keeps the
in-process ONNX model (~hundreds of MB) out of memory on constrained hosts.
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from backend.core.config import settings  # noqa: E402

log = logging.getLogger(__name__)

_embedder = None


class ServerSideEmbeddingError(RuntimeError):
    """Raised when local embedding is requested in server-side mode."""


def _load_fastembed():
    """Import and return the fastembed TextEmbedding class (lazy)."""
    from fastembed import TextEmbedding

    return TextEmbedding


def get_embedder():
    """Return a lazily-initialised, cached embedding model.

    Raises ServerSideEmbeddingError when server-side embedding is configured.
    """
    if settings.server_side_embeddings:
        raise ServerSideEmbeddingError(
            "server_side_embeddings=true: text is embedded by the vector store; "
            "local fastembed is not loaded"
        )
    global _embedder
    if _embedder is None:
        TextEmbedding = _load_fastembed()
        _embedder = TextEmbedding(model_name=settings.embedding_model)
    return _embedder


def embed_texts(texts: list[str], batch_size: int = 2) -> list[list[float]]:
    """Embed a list of strings into float vectors.

    Embeds in small sequential batches to bound ONNX Runtime memory (large PDF
    corpora can otherwise allocate multi-hundred-MB attention buffers and OOM).
    """
    if not texts:
        return []
    embedder = get_embedder()
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        # parallel=None => serial ONNX threading (no multiprocessing spawn).
        # fastembed treats parallel=0 as "use all cores" (data-parallel pool),
        # which spawns worker processes that crash on Windows-under-stdin / OOM.
        for vec in embedder.embed(batch, batch_size=len(batch), parallel=None):
            vectors.append(list(vec))
    return vectors


def warm_up() -> None:
    """Pre-download/cache the local embedding model (Docker build step).

    No-op when server-side embedding is enabled.
    """
    if settings.server_side_embeddings:
        log.info("server-side embedding enabled; skipping local warm-up")
        return
    get_embedder().embed(["warm-up"])