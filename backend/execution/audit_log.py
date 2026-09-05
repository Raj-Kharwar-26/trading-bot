"""Order audit log for SEBI algo-trading compliance.

Immutable, append-only log of all order lifecycle events.
Required for SEBI algo-trading registration audit trail.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.config import settings


@dataclass
class AuditEvent:
    """Single immutable audit event."""
    event_id: str
    timestamp: str
    event_type: str  # ORDER_PLACED, ORDER_MODIFIED, ORDER_CANCELLED, ORDER_FILLED, ORDER_REJECTED, APPROVAL_GRANTED, APPROVAL_DENIED
    order_id: str
    client_order_id: str | None = None
    symbol: str = ""
    exchange: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float | None = None
    order_type: str = ""
    product: str = ""
    status: str = ""
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    fees: float = 0.0
    algo_id: str = "trading-bot-v1"
    user_id: int | None = None
    metadata: dict[str, Any] | None = None


class AuditLog:
    """
    Thread-safe, append-only audit log with SQLite index.
    Immutable: events can only be appended, never modified or deleted.
    """

    def __init__(self, base_path: Path | None = None):
        self.base_path = base_path or Path(settings.audit_log_dir)
        self.base_path.mkdir(parents=True, exist_ok=True)

        self.jsonl_path = self.base_path / "audit_log.jsonl"
        self.db_path = self.base_path / "audit_log.db"
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        with self._db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    client_order_id TEXT,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL,
                    order_type TEXT NOT NULL,
                    product TEXT NOT NULL,
                    status TEXT NOT NULL,
                    filled_qty REAL DEFAULT 0,
                    avg_fill_price REAL DEFAULT 0,
                    fees REAL DEFAULT 0,
                    algo_id TEXT NOT NULL,
                    user_id INTEGER,
                    metadata TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_order ON audit_events(order_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_symbol ON audit_events(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type)")

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def log_event(self, event: AuditEvent) -> None:
        """Append event to JSONL and SQLite (atomic)."""
        with self._lock:
            # JSONL append
            with self.jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event), separators=(",", ":")) + "\n")

            # SQLite insert
            with self._db() as conn:
                conn.execute("""
                    INSERT INTO audit_events VALUES (
                        :event_id, :timestamp, :event_type, :order_id, :client_order_id,
                        :symbol, :exchange, :side, :quantity, :price, :order_type,
                        :product, :status, :filled_qty, :avg_fill_price, :fees,
                        :algo_id, :user_id, :metadata
                    )
                """, asdict(event))

    def get_events(
        self,
        order_id: str | None = None,
        symbol: str | None = None,
        event_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
    ) -> list[AuditEvent]:
        """Query audit events with filters."""
        with self._lock:
            conditions = []
            params = []

            if order_id:
                conditions.append("order_id = ?")
                params.append(order_id)
            if symbol:
                conditions.append("symbol = ?")
                params.append(symbol)
            if event_type:
                conditions.append("event_type = ?")
                params.append(event_type)
            if start:
                conditions.append("timestamp >= ?")
                params.append(start.isoformat())
            if end:
                conditions.append("timestamp <= ?")
                params.append(end.isoformat())

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            params.append(limit)

            with self._db() as conn:
                rows = conn.execute(
                    f"SELECT * FROM audit_events {where} ORDER BY timestamp DESC LIMIT ?",
                    params,
                ).fetchall()
                return [AuditEvent(**dict(row)) for row in rows]

    def get_order_lifecycle(self, order_id: str) -> list[AuditEvent]:
        """Get complete lifecycle of an order (oldest first)."""
        with self._lock:
            with self._db() as conn:
                rows = conn.execute("""
                    SELECT * FROM audit_events
                    WHERE order_id = ?
                    ORDER BY timestamp ASC
                """, (order_id,)).fetchall()
                return [AuditEvent(**dict(row)) for row in rows]

    def daily_summary(self, date: datetime) -> dict[str, Any]:
        """Generate daily summary for compliance reporting."""
        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59, second=59, microsecond=999999)

        with self._lock:
            with self._db() as conn:
                row = conn.execute("""
                    SELECT
                        COUNT(*) as total_events,
                        SUM(CASE WHEN event_type = 'ORDER_PLACED' THEN 1 ELSE 0 END) as orders_placed,
                        SUM(CASE WHEN event_type = 'ORDER_FILLED' THEN 1 ELSE 0 END) as orders_filled,
                        SUM(CASE WHEN event_type = 'ORDER_CANCELLED' THEN 1 ELSE 0 END) as orders_cancelled,
                        SUM(CASE WHEN event_type = 'ORDER_REJECTED' THEN 1 ELSE 0 END) as orders_rejected,
                        SUM(CASE WHEN event_type = 'APPROVAL_GRANTED' THEN 1 ELSE 0 END) as approvals_granted,
                        SUM(CASE WHEN event_type = 'APPROVAL_DENIED' THEN 1 ELSE 0 END) as approvals_denied,
                        SUM(fees) as total_fees,
                        SUM(filled_qty * avg_fill_price) as total_volume
                    FROM audit_events
                    WHERE timestamp >= ? AND timestamp <= ?
                """, (start.isoformat(), end.isoformat())).fetchone()

                return dict(row) if row else {}


# Global instance
_audit_log: AuditLog | None = None


def get_audit_log() -> AuditLog:
    global _audit_log
    if _audit_log is None:
        _audit_log = AuditLog()
    return _audit_log


def log_order_event(
    event_type: str,
    order_id: str,
    symbol: str,
    exchange: str,
    side: str,
    quantity: float,
    order_type: str,
    product: str,
    status: str,
    client_order_id: str | None = None,
    price: float | None = None,
    filled_qty: float = 0.0,
    avg_fill_price: float = 0.0,
    fees: float = 0.0,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Helper to create and log an audit event."""
    event = AuditEvent(
        event_id=f"AUD-{int(datetime.utcnow().timestamp() * 1000)}",
        timestamp=datetime.utcnow().isoformat(),
        event_type=event_type,
        order_id=order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        exchange=exchange,
        side=side,
        quantity=quantity,
        price=price,
        order_type=order_type,
        product=product,
        status=status,
        filled_qty=filled_qty,
        avg_fill_price=avg_fill_price,
        fees=fees,
        user_id=user_id,
        metadata=metadata,
    )
    get_audit_log().log_event(event)
    return event