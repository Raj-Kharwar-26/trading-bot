"""Unit tests for the paper execution engine, trade log, and portfolio state."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from backend.backtest.costs import CostModel
from backend.execution.paper import Fill, Order, OrderSide, OrderStatus, OrderType, PaperEngine, Position
from backend.execution.state import PortfolioState
from backend.execution.trade_log import TradeLog, TradeRecord


class TestPosition:
    def test_long_entry_and_exit(self):
        pos = Position(symbol="TEST")
        # Open long
        pos.update_on_fill(Fill(order_id="1", symbol="TEST", side=OrderSide.BUY, qty=100, price=100.0, fee=1.0))
        assert pos.qty == 100
        assert pos.avg_entry_price == 100.0

        # Add to long
        pos.update_on_fill(Fill(order_id="2", symbol="TEST", side=OrderSide.BUY, qty=50, price=105.0, fee=0.5))
        assert pos.qty == 150
        # VWAP
        assert pos.avg_entry_price == pytest.approx((100*100 + 105*50) / 150)

        # Partial close
        pos.update_on_fill(Fill(order_id="3", symbol="TEST", side=OrderSide.SELL, qty=50, price=110.0, fee=0.5))
        assert pos.qty == 100
        # Realized PnL uses average cost method (VWAP)
        avg_cost = (100*100 + 105*50) / 150
        assert pos.realized_pnl == pytest.approx((110 - avg_cost) * 50 - 0.5)

        # Full close
        pos.update_on_fill(Fill(order_id="4", symbol="TEST", side=OrderSide.SELL, qty=100, price=115.0, fee=1.0))
        assert pos.qty == 0
        assert pos.avg_entry_price == 0.0

    def test_short_entry_and_cover(self):
        pos = Position(symbol="TEST")
        # Open short
        pos.update_on_fill(Fill(order_id="1", symbol="TEST", side=OrderSide.SELL, qty=100, price=100.0, fee=1.0))
        assert pos.qty == -100
        assert pos.avg_entry_price == 100.0

        # Cover partial
        pos.update_on_fill(Fill(order_id="2", symbol="TEST", side=OrderSide.BUY, qty=50, price=95.0, fee=0.5))
        assert pos.qty == -50
        assert pos.realized_pnl == pytest.approx((100 - 95) * 50 - 0.5)

        # Full cover
        pos.update_on_fill(Fill(order_id="3", symbol="TEST", side=OrderSide.BUY, qty=50, price=90.0, fee=0.5))
        assert pos.qty == 0
        assert pos.avg_entry_price == 0.0


class TestPaperEngine:
    def test_buy_sell_roundtrip(self):
        engine = PaperEngine(initial_cash=100000, default_slippage_bps=0)
        # BUY
        order = engine.place_order("TEST", OrderSide.BUY, 100)
        fill = engine.match_order(order, 100.0)
        assert fill is not None
        assert fill.qty == 100
        assert fill.price == 100.0
        assert order.status == OrderStatus.FILLED
        assert engine.cash == pytest.approx(100000 - 10000)  # no fees
        pos = engine.get_position("TEST")
        assert pos.qty == 100
        assert pos.avg_entry_price == 100.0

        # SELL
        order2 = engine.place_order("TEST", OrderSide.SELL, 100)
        fill2 = engine.match_order(order2, 110.0)
        assert fill2 is not None
        assert fill2.qty == 100
        assert fill2.price == 110.0
        assert engine.cash == pytest.approx(100000 - 10000 + 11000)  # profit 1000

    def test_slippage(self):
        engine = PaperEngine(initial_cash=100000, default_slippage_bps=100)  # 1%
        order = engine.place_order("TEST", OrderSide.BUY, 100)
        fill = engine.match_order(order, 100.0)
        assert fill.price == pytest.approx(101.0)  # BUY fills higher
        assert engine.cash == pytest.approx(100000 - 10100)

    def test_cost_model_fees(self):
        cost_model = CostModel(slippage_bps=0, buy_fee_pct=0.001, sell_fee_pct=0.001)
        engine = PaperEngine(initial_cash=100000, cost_model=cost_model)
        order = engine.place_order("TEST", OrderSide.BUY, 100)
        fill = engine.match_order(order, 100.0)
        # fee = 10000 * 0.001 = 10
        assert fill.fee == pytest.approx(10.0)
        assert engine.cash == pytest.approx(100000 - 10000 - 10)

    def test_insufficient_cash_rejected(self):
        engine = PaperEngine(initial_cash=5000)
        order = engine.place_order("TEST", OrderSide.BUY, 100)
        fill = engine.match_order(order, 100.0)  # needs 10000
        assert fill is None
        assert order.status == OrderStatus.REJECTED

    def test_limit_order_not_filled(self):
        engine = PaperEngine(initial_cash=100000)
        order = engine.place_order("TEST", OrderSide.BUY, 100, order_type=OrderType.LIMIT, limit_price=95.0)
        fill = engine.match_order(order, 100.0)  # market > limit
        assert fill is None
        assert order.status == OrderStatus.PENDING

    def test_limit_order_filled(self):
        engine = PaperEngine(initial_cash=100000, default_slippage_bps=0)
        order = engine.place_order("TEST", OrderSide.BUY, 100, order_type=OrderType.LIMIT, limit_price=100.0)
        fill = engine.match_order(order, 99.0)  # market <= limit
        assert fill is not None
        assert fill.price == pytest.approx(99.0)


class TestTradeLog:
    def test_log_and_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = TradeLog(Path(tmp))
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
            log.log_trade(trade)

            recent = log.get_recent(limit=10)
            assert len(recent) == 1
            assert recent[0].trade_id == "T1"
            assert recent[0].symbol == "TEST"
            assert recent[0].net_r == pytest.approx(0.99)

            stats = log.stats()
            assert stats["n_trades"] == 1
            assert stats["avg_net_r"] == pytest.approx(0.99)
            assert stats["win_rate"] == pytest.approx(1.0)

    def test_multiple_trades_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = TradeLog(Path(tmp))
            now = datetime.utcnow().isoformat()
            # Winning trade
            log.log_trade(TradeRecord(
                trade_id="T1", symbol="TEST", market="NSE", side="LONG",
                entry_time=now, exit_time=now, entry_price=100, exit_price=110, qty=100,
                entry_fee=1, exit_fee=1, slippage_bps=0,
                gross_pnl=1000, net_pnl=998, gross_r=1.0, net_r=0.998, hold_bars=5, exit_reason="TARGET",
            ))
            # Losing trade
            log.log_trade(TradeRecord(
                trade_id="T2", symbol="TEST", market="NSE", side="LONG",
                entry_time=now, exit_time=now, entry_price=100, exit_price=95, qty=100,
                entry_fee=1, exit_fee=1, slippage_bps=0,
                gross_pnl=-500, net_pnl=-502, gross_r=-0.5, net_r=-0.502, hold_bars=3, exit_reason="STOP",
            ))

            stats = log.stats()
            assert stats["n_trades"] == 2
            assert stats["win_rate"] == pytest.approx(0.5)


class TestPortfolioState:
    def test_open_and_close_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            import backend.execution.state as state_mod
            from backend.execution.trade_log import TradeLog

            # Create fresh trade log for this test
            trade_log = TradeLog(Path(tmp))
            portfolio = PortfolioState(initial_cash=100000)
            portfolio.trade_log = trade_log
            cost_model = CostModel(slippage_bps=0, buy_fee_pct=0.001, sell_fee_pct=0.001)

            # Open LONG
            result = portfolio.place_and_fill("TEST", "NSE", "LONG", 100, 100.0, cost_model)
            assert result["success"]
            assert portfolio.engine.cash == pytest.approx(100000 - 10000 - 10)  # fee

            # Close LONG
            result = portfolio.close_position("TEST", "NSE", "LONG", 110.0, cost_model, exit_reason="TARGET", hold_bars=5)
            assert result["success"]
            # Cash: 100000 - 10000 - 10 (entry fee) + 11000 - 11 (exit fee) = 100979
            assert portfolio.engine.cash == pytest.approx(100979)

            # Trade logged
            trades = portfolio.trade_log.get_recent(10)
            assert len(trades) == 1
            assert trades[0].side == "LONG"
            assert trades[0].entry_price == pytest.approx(100.0)
            assert trades[0].exit_price == pytest.approx(110.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])