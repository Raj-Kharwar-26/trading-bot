"""Trade log: append-only JSONL + SQLite for durability + queryability."""

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
class TradeRecord:
    """A completed round-trip trade (entry + exit)."""
    trade_id: str
    symbol: str
    market: str
    side: str  # "LONG" or "SHORT"
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: float
    entry_fee: float
    exit_fee: float
    slippage_bps: float
    gross_pnl: float
    net_pnl: float
    gross_r: float
    net_r: float
    hold_bars: int
    exit_reason: str  # "STOP", "TARGET", "MAX_HOLD", "MANUAL"


class TradeLog:
    """Thread-safe trade log with JSONL file + SQLite index."""

    def __init__(self, base_path: Path | None = None):
        self.base_path = base_path or Path(settings.paper_log_dir)
        self.base_path.mkdir(parents=True, exist_ok=True)

        self.jsonl_path = self.base_path / "trades.jsonl"
        self.db_path = self.base_path / "trades.db"
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        with self._db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    qty REAL NOT NULL,
                    entry_fee REAL NOT NULL,
                    exit_fee REAL NOT NULL,
                    slippage_bps REAL NOT NULL,
                    gross_pnl REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    gross_r REAL NOT NULL,
                    net_r REAL NOT NULL,
                    hold_bars INTEGER NOT NULL,
                    exit_reason TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, entry_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time)")

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

    def log_trade(self, trade: TradeRecord) -> None:
        """Append to JSONL and insert into SQLite atomically."""
        with self._lock:
            # JSONL append
            with self.jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(trade), separators=(",", ":")) + "\n")

            # SQLite insert
            with self._db() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO trades VALUES (
                        :trade_id, :symbol, :market, :side, :entry_time, :exit_time,
                        :entry_price, :exit_price, :qty, :entry_fee, :exit_fee,
                        :slippage_bps, :gross_pnl, :net_pnl, :gross_r, :net_r,
                        :hold_bars, :exit_reason
                    )
                """, asdict(trade))

    def get_recent(self, limit: int = 100, symbol: str | None = None) -> list[TradeRecord]:
        with self._lock:
            with self._db() as conn:
                if symbol:
                    rows = conn.execute(
                        "SELECT * FROM trades WHERE symbol = ? ORDER BY entry_time DESC LIMIT ?",
                        (symbol, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                return [TradeRecord(**dict(row)) for row in rows]

    def get_by_date_range(
        self, start: datetime, end: datetime, symbol: str | None = None
    ) -> list[TradeRecord]:
        with self._lock:
            with self._db() as conn:
                if symbol:
                    rows = conn.execute(
                        "SELECT * FROM trades WHERE symbol = ? AND entry_time >= ? AND entry_time <= ? ORDER BY entry_time",
                        (symbol, start.isoformat(), end.isoformat()),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM trades WHERE entry_time >= ? AND entry_time <= ? ORDER BY entry_time",
                        (start.isoformat(), end.isoformat()),
                    ).fetchall()
                return [TradeRecord(**dict(row)) for row in rows]

    def stats(self, symbol: str | None = None) -> dict[str, Any]:
        """Aggregate statistics."""
        with self._lock:
            with self._db() as conn:
                where = "WHERE symbol = ?" if symbol else ""
                params = (symbol,) if symbol else ()

                row = conn.execute(f"""
                    SELECT
                        COUNT(*) as n_trades,
                        SUM(gross_pnl) as total_gross_pnl,
                        SUM(net_pnl) as total_net_pnl,
                        AVG(gross_r) as avg_gross_r,
                        AVG(net_r) as avg_net_r,
                        SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as win_rate,
                        MAX(net_r) as max_net_r,
                        MIN(net_r) as min_net_r
                    FROM trades {where}
                """, params).fetchone()

                if row["n_trades"] == 0:
                    return {"n_trades": 0}

                return {
                    "n_trades": row["n_trades"],
                    "total_gross_pnl": row["total_gross_pnl"],
                    "total_net_pnl": row["total_net_pnl"],
                    "avg_gross_r": row["avg_gross_r"],
                    "avg_net_r": row["avg_net_r"],
                    "win_rate": row["win_rate"],
                    "max_net_r": row["max_net_r"],
                    "min_net_r": row["min_net_r"],
                }


# Global instance (initialized on first use)
_trade_log: TradeLog | None = None


def get_trade_log() -> TradeLog:
    global _trade_log
    if _trade_log is None:
        _trade_log = TradeLog()
    return _trade_log