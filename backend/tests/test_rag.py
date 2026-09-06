"""Tests for the server-side embedding mode (Qdrant Cloud Inference).

When ``server_side_embeddings`` is enabled the process must never load
fastembed (the in-process ONNX model that OOMed Render); retrieval and ingest
must instead send raw text as ``Document`` objects and the store client must be
constructed with ``cloud_inference=True``.
"""

from pathlib import Path

from qdrant_client.models import Document

from backend.core.config import Settings
from backend.rag import embeddings, ingest, retriever, store


class _FakeHits:
    def __init__(self) -> None:
        self.points = [
            type("P", (), {"payload": {"text": "a", "source": "s.md", "chunk_index": 3}, "score": 0.9})
        ]


class _FakeClient:
    def __init__(self) -> None:
        self.captured_query = None
        self.captured_kwargs = None
        self.upserted_points = []

    def query_points(self, **kwargs):
        self.captured_kwargs = kwargs
        self.captured_query = kwargs.get("query")
        return _FakeHits()

    def upsert(self, **kwargs):
        self.upserted_points.extend(kwargs.get("points", []))


def _enable_server(monkeypatch, settings) -> None:
    monkeypatch.setattr(settings, "server_side_embeddings", True)
    monkeypatch.setattr(settings, "qdrant_inference_model", "test-model/1")


def test_retrieve_context_server_mode_sends_document(monkeypatch) -> None:
    monkeypatch.setattr(retriever, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(retriever, "ensure_collection", lambda client: None)
    _enable_server(monkeypatch, retriever.settings)

    hits = retriever.retrieve_context("what is a breakout?")

    assert len(hits) == 1
    assert hits[0]["text"] == "a"
    assert hits[0]["source"] == "s.md"
    assert hits[0]["chunk_index"] == 3
    assert hits[0]["score"] == 0.9


def test_retrieve_context_server_mode_document_query(monkeypatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(retriever, "get_client", lambda: fake)
    monkeypatch.setattr(retriever, "ensure_collection", lambda client: None)
    _enable_server(monkeypatch, retriever.settings)

    retriever.retrieve_context("what is a breakout?")

    assert fake.captured_query == Document(text="what is a breakout?", model="test-model/1")


def test_retrieve_context_local_mode_sends_vector(monkeypatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(retriever, "get_client", lambda: fake)
    monkeypatch.setattr(retriever, "ensure_collection", lambda client: None)
    vec = [0.1] * 384
    monkeypatch.setattr(retriever, "embed_texts", lambda texts: [vec])

    retriever.retrieve_context("k")

    assert fake.captured_query == vec
    assert not isinstance(fake.captured_query, Document)


def test_get_client_cloud_inference_flag_matches_setting(monkeypatch) -> None:
    captured = {}

    class _Recording:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(store, "QdrantClient", _Recording)
    monkeypatch.setattr(store.settings, "server_side_embeddings", True)
    store.get_client()
    assert captured["cloud_inference"] is True

    monkeypatch.setattr(store.settings, "server_side_embeddings", False)
    store.get_client()
    assert captured["cloud_inference"] is False


def test_build_points_server_mode_uses_document(monkeypatch, tmp_path: Path) -> None:
    _enable_server(monkeypatch, ingest.settings)
    src = tmp_path / "plan.md"
    src.write_text("Momentum breakout with volume and a tight stop.", encoding="utf-8")

    points = ingest._build_points([src])

    assert len(points) == 1
    assert isinstance(points[0].vector, Document)
    assert "Momentum breakout" in points[0].vector.text
    assert points[0].vector.model == "test-model/1"
    assert points[0].payload["source"] == "plan.md"


def test_get_embedder_rejects_server_mode(monkeypatch) -> None:
    monkeypatch.setattr(embeddings.settings, "server_side_embeddings", True)
    try:
        embeddings.get_embedder()
    except embeddings.ServerSideEmbeddingError:
        return
    raise AssertionError("expected ServerSideEmbeddingError in server mode")


def test_settings_defaults() -> None:
    s = Settings(server_side_embeddings=False)
    assert s.qdrant_inference_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert s.embedding_dim == 384