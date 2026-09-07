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


def resolve_keepalive_url(keepalive_url: str, env_values: dict | None = None) -> str | None:
    """Pick the public base URL used for the heartbeat ping.

    Priority: explicit ``keepalive_url`` setting, then Render-provided
    ``RENDER_EXTERNAL_URL``/``RENDER_URL`` env.  Returns ``None`` when no
    public URL is configured (keep-alive disabled).
    """
    env = env_values if env_values is not None else os.environ
    return keepalive_url or env.get("RENDER_EXTERNAL_URL") or env.get("RENDER_URL")


async def keep_alive() -> None:
    """Keep the free Render instance awake with inbound HTTP traffic.

    Render free web services go to sleep after ~15 minutes with no inbound
    requests; our Telegram getUpdates calls are outbound and do not count.
    This task GETs our own public /healthz every few minutes so the sleep
    timer never expires.
    """
    from backend.core.config import settings

    target = resolve_keepalive_url(settings.keepalive_url)
    if not target:
        log.warning("keep_alive: no public URL (set RENDER_EXTERNAL_URL or keepalive_url); skipping")
        return

    import httpx

    url = target.rstrip("/") + "/healthz"
    interval = max(60, settings.keepalive_interval_seconds)
    log.info("keep_alive: pinging %s every %ds", url, interval)
    ticks = 0
    while True:
        await asyncio.sleep(interval)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
            ok = resp.status_code == 200
        except Exception as exc:  # noqa: BLE001 - heartbeat is best-effort
            ok = False
            log.warning("keep_alive ping failed: %s", exc)
        ticks += 1
        if ticks % 15 == 0:  # log ~once per hour, not every ping
            log.info("keep_alive: last ping ok=%s (%d ticks)", ok, ticks)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # Seed RAG in the background: server-side inference embeds via the vector
    # store (no in-process model); local mode downloads ONNX on first run and
    # embedding a corpus can take minutes. Either way, never block uvicorn's
    # socket bind (Render scans for the port and times out slow boots).
    asyncio.create_task(asyncio.to_thread(seed_rag))

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
        asyncio.create_task(keep_alive()),
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass