"""Reflection & learning loop: post-trade analysis injected into future reasoning.

After each closed trade, generate a structured reflection and store it in Qdrant
as a "lesson" that can be retrieved by the RAG system for future analyses.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.config import settings
from backend.execution.trade_log import TradeRecord


@dataclass
class TradeReflection:
    """A structured post-trade reflection for learning."""
    reflection_id: str
    trade_id: str
    symbol: str
    market: str
    side: str  # LONG/SHORT
    entry_price: float
    exit_price: float
    net_pnl: float
    net_r: float
    hold_bars: int
    exit_reason: str
    # LLM-generated
    what_went_well: str
    what_went_wrong: str
    key_lesson: str
    confidence: float  # 0-1, how confident in the lesson
    tags: list[str]
    generated_at: str


class ReflectionEngine:
    """
    Generates reflections from closed trades and stores them for RAG retrieval.

    Uses the lean LLM to analyze trade outcomes and extract actionable lessons.
    Reflections are stored as JSONL + indexed in Qdrant for semantic search.
    """

    def __init__(self, reflection_dir: Path | None = None):
        self.reflection_dir = reflection_dir or Path(settings.reflection_log_dir)
        self.reflection_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.reflection_dir / "reflections.jsonl"

    def generate_reflection(self, trade: TradeRecord) -> TradeReflection:
        """Generate a reflection for a completed trade using the LLM."""
        # For now, create a heuristic reflection without LLM call
        # (LLM integration would call the lean reasoning pipeline)
        reflection = self._heuristic_reflection(trade)
        self.save_reflection(reflection)
        return reflection

    def _heuristic_reflection(self, trade: TradeRecord) -> TradeReflection:
        """Generate a basic reflection without LLM (fallback/template)."""
        if trade.net_pnl > 0:
            what_went_well = (
                f"Trade hit {trade.exit_reason} at {trade.exit_price:.2f} "
                f"for {trade.net_r:.2f}R profit. Risk management held."
            )
            what_went_wrong = (
                "Consider if position size was optimal; could have scaled in/out."
            )
            key_lesson = (
                f"{trade.side} on {trade.symbol} worked when "
                f"{trade.exit_reason.lower()} was respected."
            )
            tags = ["win", trade.exit_reason.lower(), trade.side.lower()]
        else:
            what_went_well = (
                f"Exit at {trade.exit_price:.2f} ({trade.exit_reason}) limited loss "
                f"to {abs(trade.net_r):.2f}R. Risk control functioned."
            )
            what_went_wrong = (
                f"Entry at {trade.entry_price:.2f} was unfavorable; "
                f"consider better timing or confirmation."
            )
            key_lesson = (
                f"Failed {trade.side} on {trade.symbol}: "
                f"avoid similar setups without stronger confirmation."
            )
            tags = ["loss", trade.exit_reason.lower(), trade.side.lower()]

        return TradeReflection(
            reflection_id=str(uuid.uuid4())[:8],
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            market=trade.market,
            side=trade.side,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            net_pnl=trade.net_pnl,
            net_r=trade.net_r,
            hold_bars=trade.hold_bars,
            exit_reason=trade.exit_reason,
            what_went_well=what_went_well,
            what_went_wrong=what_went_wrong,
            key_lesson=key_lesson,
            confidence=0.7,
            tags=tags,
            generated_at=datetime.utcnow().isoformat(),
        )

    def save_reflection(self, reflection: TradeReflection) -> None:
        """Append reflection to JSONL file."""
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(reflection), separators=(",", ":")) + "\n")

    def get_recent(self, limit: int = 50) -> list[TradeReflection]:
        """Load recent reflections from JSONL."""
        if not self.jsonl_path.exists():
            return []
        reflections = []
        with self.jsonl_path.open("r", encoding="utf-8") as f:
            for line in f.readlines()[-limit:]:
                line = line.strip()
                if line:
                    reflections.append(TradeReflection(**json.loads(line)))
        return list(reversed(reflections))

    def get_by_symbol(self, symbol: str, limit: int = 20) -> list[TradeReflection]:
        """Get reflections for a specific symbol."""
        all_refs = self.get_recent(limit=1000)
        return [r for r in all_refs if r.symbol == symbol][-limit:]

    def format_for_rag(self, reflection: TradeReflection) -> str:
        """Format reflection as a text chunk for RAG ingestion."""
        return (
            f"REFLECTION {reflection.reflection_id}: {reflection.symbol} {reflection.side}\n"
            f"Entry: {reflection.entry_price:.2f} -> Exit: {reflection.exit_price:.2f} "
            f"({reflection.exit_reason})\n"
            f"PnL: {reflection.net_pnl:+.2f} ({reflection.net_r:+.2f}R) in {reflection.hold_bars} bars\n"
            f"What went well: {reflection.what_went_well}\n"
            f"What went wrong: {reflection.what_went_wrong}\n"
            f"Key lesson: {reflection.key_lesson}\n"
            f"Confidence: {reflection.confidence:.0%} | Tags: {', '.join(reflection.tags)}"
        )


def create_reflection_engine() -> ReflectionEngine:
    """Factory for ReflectionEngine."""
    return ReflectionEngine()