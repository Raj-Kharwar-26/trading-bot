"""Unit tests for the backtest cost model (fees + slippage).

Verifies fee fractions for NSE/BSE vs crypto, adverse slippage direction,
and the net-R computation (gross minus cost drag in R).
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.backtest.costs import (
    CostModel,
    cost_model_for_market,
    equity_buy_fee,
    equity_sell_fee,
    fill_price,
    net_r,
)
from backend.backtest.simulator import backtest_trade
from backend.core.config import Settings


@pytest.fixture
def cfg() -> Settings:
    return Settings(
        slippage_bps=15.0,
        equity_brokerage_pct=0.0003,
        equity_stt_pct=0.001,
        equity_transaction_pct=0.0000345,
        equity_sebi_pct=0.000001,
        equity_stamp_duty_pct=0.00015,
        equity_gst_pct=0.18,
        crypto_taker_pct=0.001,
    )


def test_equity_sell_fee_gt_buy_fee(cfg):
    # STT (0.1%) is charged on the sell side only -> sell leg is more costly.
    assert equity_sell_fee(cfg) > equity_buy_fee(cfg)
    assert equity_buy_fee(cfg) > 0
    # Buy side carries stamp duty; sell side carries STT.
    assert (equity_sell_fee(cfg) - equity_buy_fee(cfg)) == pytest.approx(
        cfg.equity_stt_pct - cfg.equity_stamp_duty_pct
    )


def test_crypto_fees_symmetric(cfg):
    model = cost_model_for_market("CRYPTO", cfg)
    assert model.buy_fee_pct == cfg.crypto_taker_pct
    assert model.sell_fee_pct == cfg.crypto_taker_pct


def test_cost_model_for_market_slippage(cfg):
    assert cost_model_for_market("NSE", cfg).slippage_bps == cfg.slippage_bps
    assert cost_model_for_market("CRYPTO", cfg).slippage_bps == cfg.slippage_bps


def test_fill_price_adverse_buy_higher_sell_lower():
    assert fill_price(100.0, action="BUY", slippage_bps=100) == pytest.approx(101.0)
    assert fill_price(100.0, action="SELL", slippage_bps=100) == pytest.approx(99.0)


def test_net_r_long_target():
    model = CostModel(slippage_bps=100, buy_fee_pct=0.001, sell_fee_pct=0.001)
    gross, cost, net = net_r(
        direction="LONG",
        action_entry="BUY",
        action_exit="SELL",
        entry_price=100.0,
        exit_price=110.0,  # target
        risk=10.0,          # stop at 90
        model=model,
    )
    assert gross == pytest.approx(0.79)   # (108.9 - 101) / 10
    assert cost == pytest.approx(0.02099)
    assert net == pytest.approx(0.76901)
    assert net < gross


def test_net_r_short_target():
    model = CostModel(slippage_bps=100, buy_fee_pct=0.001, sell_fee_pct=0.001)
    gross, cost, net = net_r(
        direction="SHORT",
        action_entry="SELL",
        action_exit="BUY",
        entry_price=100.0,
        exit_price=90.0,  # target
        risk=10.0,         # stop at 110
        model=model,
    )
    assert gross == pytest.approx(0.81)   # (99 - 90.9) / 10
    assert cost == pytest.approx(0.01899)
    assert net == pytest.approx(0.79101)


def test_simulator_net_below_gross_with_cost(cfg):
    high = np.array([100.0, 112.0, 99.0, 113.0])
    low = np.array([100.0, 101.0, 99.0, 102.0])
    close = np.array([100.0, 100.0, 99.0, 100.0])

    cost = cost_model_for_market("CRYPTO", cfg)

    gross_only = backtest_trade(
        high, low, close,
        direction="LONG", entry=100.0, stop=90.0, target=110.0, min_trades=2,
    )
    net = backtest_trade(
        high, low, close,
        direction="LONG", entry=100.0, stop=90.0, target=110.0, min_trades=2,
        cost=cost,
    )

    assert net.n_trades == 2
    assert net.avg_cost_r_per_trade is not None and net.avg_cost_r_per_trade > 0
    assert net.avg_r_per_trade < gross_only.avg_r_per_trade
    assert net.avg_r_per_trade < net.avg_gross_r_per_trade


def test_no_cost_model_matches_gross():
    # Backward-compat: cost=None must equal the gross (net_r == gross_r, cost 0).
    res = backtest_trade(
        np.array([100.0, 95.0]), np.array([100.0, 89.0]), np.array([100.0, 100.0]),
        direction="LONG", entry=100.0, stop=90.0, target=110.0,
    )
    assert res.avg_r_per_trade == res.avg_gross_r_per_trade == -1.0
    assert res.avg_cost_r_per_trade == 0.0
