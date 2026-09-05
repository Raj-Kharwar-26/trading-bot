"""Unit tests for auto-risk engine and Binance testnet adapter."""

from __future__ import annotations

from datetime import datetime

import pytest

from backend.execution.paper import Fill, OrderSide, OrderStatus, OrderType, PaperEngine, Position
from backend.execution.risk_engine import AutoRiskEngine, RiskViolation


class TestAutoRiskEngine:
    def test_pre_trade_approval(self):
        engine = PaperEngine(initial_cash=100000)
        risk = AutoRiskEngine(engine)

        from backend.risk.gate import TradeProposal
        proposal = TradeProposal(
            symbol="TEST",
            side="BUY",
            entry=100.0,
            stop_loss=90.0,
            target=120.0,
            risk_amount=1000.0,  # 1% of equity
            equity=100000.0,
            position_value=4000.0,  # 4% of equity (under 5% limit)
            open_positions=0,
            total_exposure=0.0,
        )
        ok, msg = risk.check_pre_trade(proposal)
        assert ok
        assert msg is None

    def test_pre_fill_position_limit(self):
        engine = PaperEngine(initial_cash=100000)
        risk = AutoRiskEngine(engine)

        # Try to buy > 5% of equity (max_position_pct = 0.05)
        # 5% of 100000 = 5000 notional
        # At price 100, max qty = 50
        ok, msg = risk.check_pre_fill("TEST", OrderSide.BUY, 100, 100.0)  # 10000 notional > 5000
        assert not ok
        assert "Position size exceeds" in msg

    def test_pre_fill_exposure_limit(self):
        engine = PaperEngine(initial_cash=100000)
        risk = AutoRiskEngine(engine)

        # Set up positions with realistic quantities (simulating fills)
        # 5 positions at 40 qty = 4000 each = 20000 total
        for i in range(1, 6):
            pos = engine.get_position(f"TEST{i}")
            pos.qty = 40
            pos.avg_entry_price = 100.0

        # Add fills to bring TEST1-3 to 50 (3000 more)
        for i in range(1, 4):
            pos = engine.get_position(f"TEST{i}")
            pos.qty = 50

        # Add TEST6 at 50 (5000 more)
        pos6 = engine.get_position("TEST6")
        pos6.qty = 50
        pos6.avg_entry_price = 100.0

        # Now total = 5*50*100 + 2*40*100 + 50*100 = 25000 + 8000 + 5000 = 38000? 
        # Wait: TEST1-3: 50 each = 150*100 = 15000
        # TEST4-5: 40 each = 80*100 = 8000
        # TEST6: 50 = 5000
        # Total = 28000

        # Try to add 21 to new symbol TEST7 = 2100 more -> 30100 > 30000
        ok, msg = risk.check_pre_fill("TEST7", OrderSide.BUY, 21, 100.0)
        assert not ok
        assert "exposure exceeds" in msg.lower()

    def test_pre_fill_concurrent_positions(self):
        engine = PaperEngine(initial_cash=100000)
        risk = AutoRiskEngine(engine)

        # Create 10 open positions (max_concurrent_positions = 10)
        for i in range(10):
            pos = engine.get_position(f"TEST{i}")
            pos.qty = 10
            pos.avg_entry_price = 100.0

        # Try to open 11th
        ok, msg = risk.check_pre_fill("TEST10", OrderSide.BUY, 10, 100.0)
        assert not ok
        assert "concurrent" in msg.lower()

    def test_daily_loss_halt(self):
        engine = PaperEngine(initial_cash=100000)
        risk = AutoRiskEngine(engine)

        # Simulate large daily loss
        pos = engine.get_position("TEST")
        pos.qty = 1000
        pos.avg_entry_price = 100.0
        pos.realized_pnl = -4000  # 4% loss

        violations = risk.on_fill(Fill(
            order_id="1", symbol="TEST", side=OrderSide.SELL,
            qty=1000, price=96.0, fee=10.0
        ))

        # Should halt at 3% daily loss
        assert risk._halted
        assert any(v.severity == "HALT" for v in risk.violations)

    def test_per_trade_loss_warn(self):
        engine = PaperEngine(initial_cash=100000)
        risk = AutoRiskEngine(engine)

        pos = engine.get_position("TEST")
        pos.qty = 100
        pos.avg_entry_price = 100.0

        # Fill at 98.5 (1.5% loss, limit is 1%)
        violations = risk.on_fill(Fill(
            order_id="1", symbol="TEST", side=OrderSide.SELL,
            qty=100, price=98.5, fee=1.0
        ))

        assert any(v.rule == "per_trade_loss" and v.severity == "WARN" for v in violations)

    def test_risk_metrics(self):
        engine = PaperEngine(initial_cash=100000)
        risk = AutoRiskEngine(engine)

        metrics = risk.get_risk_metrics({"TEST": 100.0})
        assert metrics["equity"] == 100000
        assert metrics["cash"] == 100000
        assert metrics["daily_pnl"] == 0
        assert not metrics["halted"]

    def test_halt_reset(self):
        engine = PaperEngine(initial_cash=100000)
        risk = AutoRiskEngine(engine)
        risk._halt(trading_halt=True, reason="Test halt")

        assert risk.is_halted() == (True, "Test halt")
        risk.reset_halt()
        assert risk.is_halted() == (False, None)


class TestBinanceTestnetAdapter:
    def test_config_creation(self):
        from backend.execution.binance_testnet import BinanceConfig
        config = BinanceConfig(api_key="key", api_secret="secret")
        assert config.api_key == "key"
        assert config.base_url == "https://testnet.binance.vision"

    def test_signing(self):
        from backend.execution.binance_testnet import BinanceTestnetClient
        config = type("Config", (), {"api_key": "k", "api_secret": "s", "recv_window": 5000})()
        client = BinanceTestnetClient(config)
        sig = client._sign({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "1"})
        assert len(sig) == 64  # SHA256 hex


if __name__ == "__main__":
    pytest.main([__file__, "-v"])