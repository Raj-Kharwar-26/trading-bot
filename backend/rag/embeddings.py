"""Shared embedding helper.

Wraps ``fastembed`` (local, no API key) which is the embedding source for RAG.
Disables the hf-xet CDN downloader (which is flaky on some networks) before the
model loads; issues are only relevant on first-time model download.
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from fastembed import TextEmbedding  # noqa: E402

from backend.core.config import settings

log = logging.getLogger(__name__)

_embedder: TextEmbedding | None = None


def get_embedder() -> TextEmbedding:
    """Return a lazily-initialised, cached embedding model."""
    global _embedder
    if _embedder is None:
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
