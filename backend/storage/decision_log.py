"""Decision-log CRUD: persist + query analysis reports.

All writes are best-effort: a DB failure must never break the trading pipeline,
so callers treat this as telemetry. Queries raise only on misuse.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, func

from backend.storage.db import get_session
from backend.storage.models import DecisionLog

log = logging.getLogger(__name__)


def log_decision(report: dict[str, Any]) -> int | None:
    """Persist one analysis report (+ its backtest block).

    Best-effort: returns the new row id on success, or None if the DB is
    unreachable / the insert fails (logged, never raised).
    """
    meta = report.get("_meta") or {}
    try:
        row = DecisionLog(
            symbol=str(meta.get("symbol") or ""),
            market=str(meta.get("market") or "NSE"),
            signal=report.get("signal"),
            confidence=report.get("confidence"),
            engine=meta.get("engine"),
            rag_hits=meta.get("rag_hits"),
            report=report,
            backtest=report.get("backtest"),
        )
        with get_session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id
    except Exception as exc:  # noqa: BLE001 - telemetry must never fail the pipeline
        log.warning("decisions_log failed (non-fatal): %s", exc)
        return None


def latest_decisions(symbol: str | None = None, limit: int = 20) -> list[dict]:
    """Return the most recent decision rows, optionally filtered by symbol."""
    limit = max(1, min(int(limit), 200))
    stmt = select(DecisionLog).order_by(
        DecisionLog.created_at.desc(), DecisionLog.id.desc()
    )
    if symbol:
        stmt = stmt.where(DecisionLog.symbol == symbol)
    stmt = stmt.limit(limit)

    with get_session() as session:
        rows = session.execute(stmt).scalars().all()
        return [r.to_dict() for r in rows]


def count_decisions() -> int:
    with get_session() as session:
        return int(session.execute(select(func.count(DecisionLog.id))).scalar() or 0)
