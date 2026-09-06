"""One-process entrypoint for Render (free plan).

Runs the FastAPI app (serves /healthz so the free web service is considered
healthy and can be kept awake by a heartbeat ping) AND the Telegram long-poller
in the same asyncio event loop. On boot it seeds the RAG collection if it is
empty (strategy docs ingested into the persistent Qdrant store).
"""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

log = logging.getLogger("run_cloud")


def seed_rag() -> None:
    """Ingest strategy docs into Qdrant if the collection is empty."""
    try:
        from backend.core.config import settings
        from backend.rag.ingest import ingest_strategy_docs
        from backend.rag.store import ensure_collection, get_client

        client = get_client()
        ensure_collection(client)
        count = 0
        try:
            count = client.count(
                collection_name=settings.qdrant_collection, exact=True
            ).count
        except Exception:  # noqa: BLE001
            count = 0
        if count == 0:
            summary = ingest_strategy_docs(force_rebuild=False)
            log.info("seeded RAG collection: %s", summary)
        else:
            log.info("RAG collection already populated (%d points); skip seed", count)
    except Exception as exc:  # noqa: BLE001
        log.warning("RAG seed skipped (non-fatal): %s", exc)


async def run_telegram() -> None:
    """Start the Telegram polling application."""
    from backend.telegram.bot import build_app

    app = build_app()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("Telegram poller started")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.stop()


async def probe_outbound() -> None:
    """Temporary connectivity probe for openrouter.ai (debug only)."""
    import httpx

    try:
        with httpx.Client(timeout=20.0) as client:
            for host in ("https://openrouter.ai/api/v1/models", "https://api.binance.com/api/v3/time"):
                try:
                    r = client.get(host)
                    log.info("PROBE %s -> HTTP %s", host, r.status_code)
                except Exception as exc:  # noqa: BLE001
                    log.error("PROBE %s -> %r", host, exc)
    except Exception as exc:  # noqa: BLE001
        log.error("PROBE setup failed: %r", exc)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # Seed RAG in the background: fastembed's first run downloads the ONNX
    # model and embedding a corpus can take minutes. Never let it block
    # uvicorn's socket bind (Render scans for the port and times out slow boots).
    asyncio.create_task(asyncio.to_thread(seed_rag))
    if os.getenv("PROBE_OUTBOUND"):
        asyncio.create_task(probe_outbound())

    port = int(os.getenv("PORT", "10000"))
    config = uvicorn.Config(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    tasks = [
        asyncio.create_task(server.serve()),
        asyncio.create_task(run_telegram()),
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass