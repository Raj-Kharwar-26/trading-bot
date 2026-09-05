"""Unit tests for Zerodha Kite adapter."""

from __future__ import annotations

import pytest

from backend.execution.paper import OrderSide, OrderType, PaperEngine
from backend.execution.zerodha_kite import (
    KiteConfig,
    KiteConnectClient,
    KiteExchange,
    KiteOrderType,
    KiteProduct,
    KiteTransactionType,
    KiteVariety,
    ZerodhaKitePaperEngine,
)


class TestZerodhaConfig:
    def test_config_creation(self):
        config = KiteConfig(
            api_key="test_key",
            api_secret="test_secret",
            access_token="test_token",
        )
        assert config.api_key == "test_key"
        assert config.paper_mode is True

    def test_login_url(self):
        config = KiteConfig(api_key="test_key", api_secret="test_secret")
        client = KiteConnectClient(config)
        # Note: this is async, just test the method exists
        assert hasattr(client, "login_url")


class TestZerodhaPaperEngine:
    def test_cost_model_build(self):
        engine = ZerodhaKitePaperEngine()
        cm = engine._cost_model

        # Buy fee should include stamp duty
        # Sell fee should include STT
        assert cm.buy_fee_pct > 0
        assert cm.sell_fee_pct > 0
        assert cm.sell_fee_pct > cm.buy_fee_pct  # STT on sell

    def test_paper_mode_buy_sell(self):
        local = PaperEngine(initial_cash=500000)
        engine = ZerodhaKitePaperEngine(local_engine=local)

        # BUY
        order = engine.place_order("RELIANCE", OrderSide.BUY, 10)
        fill = engine.match_order(order, 2500.0)
        assert fill is not None
        assert fill.side == OrderSide.BUY
        assert fill.price > 0
        assert fill.fee > 0

        # SELL
        order2 = engine.place_order("RELIANCE", OrderSide.SELL, 10)
        fill2 = engine.match_order(order2, 2600.0)
        assert fill2 is not None
        assert fill2.side == OrderSide.SELL

        # Position should be flat
        pos = engine.get_position("RELIANCE")
        assert pos.qty == 0

    def test_limit_order(self):
        local = PaperEngine(initial_cash=500000)
        engine = ZerodhaKitePaperEngine(local_engine=local)

        order = engine.place_order("RELIANCE", OrderSide.BUY, 10, OrderType.LIMIT, 2450.0)
        fill = engine.match_order(order, 2440.0)  # market below limit
        assert fill is not None
        assert fill.price == pytest.approx(2440.0)

    def test_fee_calculation(self):
        local = PaperEngine(initial_cash=500000)
        engine = ZerodhaKitePaperEngine(local_engine=local)

        order = engine.place_order("RELIANCE", OrderSide.BUY, 10)
        fill = engine.match_order(order, 2500.0)
        assert fill is not None

        # Fee = notional * buy_fee_pct
        notional = fill.price * fill.qty
        expected_fee = notional * engine._cost_model.buy_fee_pct
        assert fill.fee == pytest.approx(expected_fee, rel=0.01)


class TestKiteEnums:
    def test_enums(self):
        assert KiteExchange.NSE.value == "NSE"
        assert KiteExchange.BSE.value == "BSE"
        assert KiteTransactionType.BUY.value == "BUY"
        assert KiteTransactionType.SELL.value == "SELL"
        assert KiteOrderType.MARKET.value == "MARKET"
        assert KiteOrderType.LIMIT.value == "LIMIT"
        assert KiteProduct.CNC.value == "CNC"
        assert KiteVariety.REGULAR.value == "regular"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])