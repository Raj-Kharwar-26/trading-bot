"""Unit tests for reflection engine."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.execution.reflection import ReflectionEngine, TradeReflection
from backend.execution.trade_log import TradeRecord
from datetime import datetime


class TestReflectionEngine:
    def test_heuristic_reflection_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReflectionEngine(Path(tmp))
            trade = TradeRecord(
                trade_id="T1",
                symbol="TEST",
                market="NSE",
                side="LONG",
                entry_time=datetime.utcnow().isoformat(),
                exit_time=datetime.utcnow().isoformat(),
                entry_price=100.0,
                exit_price=110.0,
                qty=100,
                entry_fee=5.0,
                exit_fee=5.0,
                slippage_bps=10.0,
                gross_pnl=1000.0,
                net_pnl=990.0,
                gross_r=1.0,
                net_r=0.99,
                hold_bars=5,
                exit_reason="TARGET",
            )
            reflection = engine.generate_reflection(trade)

            assert reflection.trade_id == "T1"
            assert reflection.symbol == "TEST"
            assert reflection.side == "LONG"
            assert reflection.net_r == pytest.approx(0.99)
            assert "win" in reflection.tags
            assert "target" in reflection.tags
            assert reflection.confidence > 0

    def test_heuristic_reflection_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReflectionEngine(Path(tmp))
            trade = TradeRecord(
                trade_id="T2",
                symbol="TEST",
                market="NSE",
                side="SHORT",
                entry_time=datetime.utcnow().isoformat(),
                exit_time=datetime.utcnow().isoformat(),
                entry_price=100.0,
                exit_price=105.0,
                qty=100,
                entry_fee=5.0,
                exit_fee=5.0,
                slippage_bps=10.0,
                gross_pnl=-500.0,
                net_pnl=-510.0,
                gross_r=-0.5,
                net_r=-0.51,
                hold_bars=3,
                exit_reason="STOP",
            )
            reflection = engine.generate_reflection(trade)

            assert reflection.net_pnl < 0
            assert "loss" in reflection.tags
            assert "stop" in reflection.tags
            assert "short" in reflection.tags

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReflectionEngine(Path(tmp))
            trade = TradeRecord(
                trade_id="T3", symbol="TEST", market="NSE", side="LONG",
                entry_time=datetime.utcnow().isoformat(), exit_time=datetime.utcnow().isoformat(),
                entry_price=100, exit_price=110, qty=100, entry_fee=1, exit_fee=1,
                slippage_bps=0, gross_pnl=1000, net_pnl=998, gross_r=1.0, net_r=0.998,
                hold_bars=5, exit_reason="TARGET",
            )
            r1 = engine.generate_reflection(trade)
            r2 = engine.generate_reflection(trade)

            recent = engine.get_recent(limit=10)
            assert len(recent) == 2

            by_symbol = engine.get_by_symbol("TEST", limit=10)
            assert len(by_symbol) == 2

    def test_format_for_rag(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReflectionEngine(Path(tmp))
            trade = TradeRecord(
                trade_id="T4", symbol="RELIANCE.NS", market="NSE", side="LONG",
                entry_time=datetime.utcnow().isoformat(), exit_time=datetime.utcnow().isoformat(),
                entry_price=2500, exit_price=2600, qty=50, entry_fee=10, exit_fee=10,
                slippage_bps=5, gross_pnl=5000, net_pnl=4980, gross_r=2.0, net_r=1.99,
                hold_bars=10, exit_reason="TARGET",
            )
            reflection = engine.generate_reflection(trade)
            rag_text = engine.format_for_rag(reflection)

            assert "RELIANCE.NS" in rag_text
            assert "LONG" in rag_text
            assert "2500" in rag_text
            assert "2600" in rag_text
            assert "TARGET" in rag_text
            assert "Key lesson" in rag_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])