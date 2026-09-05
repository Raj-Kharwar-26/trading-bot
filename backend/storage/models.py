"""ORM models for the decision log (one row per /analyze report)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DecisionLog(Base):
    """A single persisted analysis decision + its backtest outcome."""

    __tablename__ = "decision_log"
    __table_args__ = (
        Index("ix_decision_log_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False, default="NSE")
    signal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rag_hits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report: Mapped[dict] = mapped_column(JSON, nullable=False)
    backtest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "market": self.market,
            "signal": self.signal,
            "confidence": self.confidence,
            "engine": self.engine,
            "rag_hits": self.rag_hits,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "report": self.report,
            "backtest": self.backtest,
        }
