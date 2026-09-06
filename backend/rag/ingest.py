"""Ingest strategy documents (PDF/Word/text/images) into the Qdrant vector store.

Documents are read from ``STRATEGY_DOCS_DIR`` (default ``./docs/strategies``),
split into overlapping chunks, embedded with fastembed, and upserted as Qdrant
points. Re-running wipes and rebuilds the collection so stale strategy content
does not linger.

Supported formats:
  - text: ``.txt``, ``.md``
  - PDF:  ``.pdf`` (pypdf)
  - Word: ``.docx`` (python-docx), ``.doc`` (best-effort RTF strip)
  - images: ``.png``, ``.jpg``, ``.jpeg``, ``.bmp``, ``.webp``, ``.tiff``
    via RapidOCR (onnxruntime) — text inside images is extracted.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from backend.core.config import settings
from backend.rag.embeddings import embed_texts
from backend.rag.store import delete_all_documents, ensure_collection, get_client

log = logging.getLogger(__name__)


SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"}


def _extract_text(path: Path) -> str:
    """Extract plain text from a supported file."""
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".doc":
        return _extract_legacy_doc(path)
    if suffix in {"", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"} or _looks_like_image(path):
        return _extract_image(path)
    return ""


def _extract_pdf(path: Path) -> str:
    """Extract text from a PDF via pypdf (local import keeps startup light)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to extract PDF %s: %s", path.name, exc)
        return ""


def _extract_docx(path: Path) -> str:
    """Extract paragraphs + tables from a .docx via python-docx."""
    try:
        from docx import Document

        doc = Document(str(path))
        parts: list[str] = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to extract docx %s: %s", path.name, exc)
        return ""


def _extract_legacy_doc(path: Path) -> str:
    """Best-effort text from legacy .doc.

    python-docx cannot read the binary .doc format. Files that are actually RTF
    (common for exported Word/RichEdit docs) are stripped of control words;
    anything else is skipped with a warning so the user can convert to .docx.
    """
    try:
        raw = path.read_bytes()
        head = raw[:512].lstrip(b"\xff\xfe\xfe\xff\x00")
        if head.lstrip().startswith(b"{\\rtf"):
            import re

            text = head + raw[512:]
            text = text.decode("utf-8", errors="replace")
            text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
            text = re.sub(r"[{}]", "", text)
            return text
        log.warning(
            "legacy .doc %s is not RTF-extractable; convert it to .docx/.txt", path.name
        )
        return ""
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to extract legacy .doc %s: %s", path.name, exc)
        return ""


def _looks_like_image(path: Path) -> bool:
    """Cheap magic-byte sniff for image formats without a known suffix."""
    try:
        with path.open("rb") as fh:
            head = fh.read(12)
        for sig in (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"BM", b"RIFF"):
            if head.startswith(sig):
                return True
    except OSError:
        pass
    return False


def _extract_image(path: Path) -> str:
    """OCR an image to text via RapidOCR (onnxruntime, no system deps)."""
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        result, _ = engine(str(path))
        if not result:
            return ""
        lines: list[str] = []
        for item in result:
            if isinstance(item, dict):
                text = item.get("txt") or item.get("text") or ""
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                text = item[1] or ""
            else:
                text = ""
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR failed for %s: %s", path.name, exc)
        return ""


def _chunk_text(text: str, chunk_size: int = 600, overlap: int = 80) -> list[str]:
    """Split text into overlapping, roughly token-aligned chunks."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def ingest_strategy_docs(force_rebuild: bool = True) -> dict:
    """Ingest all strategy documents into Qdrant.

    Returns a summary dict. Set ``force_rebuild=False`` to skip wiping an
    already-populated collection.
    """
    docs_dir = Path(settings.strategy_docs_dir)
    if not docs_dir.exists():
        raise FileNotFoundError(f"strategy docs dir not found: {docs_dir}")

    files = sorted(
        p
        for p in docs_dir.iterdir()
        if p.is_file() and (p.suffix.lower() in SUPPORTED_SUFFIXES or _looks_like_image(p))
    )
    if not files:
        raise FileNotFoundError(
            f"no supported files found in {docs_dir} "
            f"(supported: {sorted(SUPPORTED_SUFFIXES)})"
        )

    client = get_client()
    ensure_collection(client)
    if force_rebuild:
        delete_all_documents(client)

    points = _build_points(files)
    # Upsert in small batches to avoid Qdrant free-cloud request timeouts
    # (large payloads of PDF chunks can exceed the per-request limit).
    batch_size = 50
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        client.upsert(collection_name=settings.qdrant_collection, points=batch)

    summary = {
        "files": len(files),
        "chunks": len(points),
        "collection": settings.qdrant_collection,
        "docs_dir": str(docs_dir),
    }
    log.info("ingested strategy docs: %s", summary)
    return summary


def _build_points(files: Iterable[Path]) -> list[object]:
    """Build Qdrant points from file chunks (batch-embedding the corpus)."""
    from qdrant_client.models import PointStruct

    all_texts: list[tuple[str, dict]] = []
    for path in files:
        text = _extract_text(path)
        if not text.strip():
            log.warning("no extractable text in %s", path.name)
            continue
        chunks = _chunk_text(text)
        for idx, chunk in enumerate(chunks):
            all_texts.append(
                (chunk, {"source": path.name, "chunk_index": idx, "text": chunk})
            )

    if not all_texts:
        return []

    texts = [t for t, _ in all_texts]
    if settings.server_side_embeddings:
        from qdrant_client.models import Document

        # Server-side inference: send the raw chunk text; the vector store
        # embeds it (no local ONNX model, keeps the process light).
        points = [
            PointStruct(
                id=str(uuid4()),
                vector=Document(text=chunk, model=settings.qdrant_inference_model),
                payload=payload,
            )
            for (chunk, payload) in all_texts
        ]
        return points

    vectors = embed_texts(texts)

    points = [
        PointStruct(
            id=str(uuid4()),
            vector=vec,
            payload=payload,
        )
        for (_, payload), vec in zip(all_texts, vectors, strict=False)
    ]
    return points


# ensure_collection is imported at the top of this module.
