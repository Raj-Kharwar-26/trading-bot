"""Unit tests for the Phase 2 backtest simulator.

These prove the setup-replay math: winning/losing paths, short direction,
degenerate (flat) setups, the max-hold close-out (no look-ahead), and the
min-trades "structured" gating. Run with: pytest backend/tests/test_backtest.py
"""

from __future__ import annotations

import numpy as np

from backend.backtest.simulator import backtest_trade


def test_long_profitable_target_hit():
    high = np.array([100.0, 112.0, 99.0, 113.0])
    low = np.array([100.0, 101.0, 99.0, 102.0])
    close = np.array([100.0, 100.0, 99.0, 100.0])
    res = backtest_trade(
        high, low, close,
        direction="LONG", entry=100.0, stop=90.0, target=110.0,
        min_trades=2,
    )
    assert res.n_trades == 2
    assert res.win_rate == 1.0
    assert res.avg_r_per_trade == 1.0
    assert res.total_r == 2.0
    assert res.structured is True


def test_long_stop_loss_path():
    high = np.array([100.0, 95.0])
    low = np.array([100.0, 89.0])
    close = np.array([100.0, 100.0])
    res = backtest_trade(
        high, low, close,
        direction="LONG", entry=100.0, stop=90.0, target=110.0,
    )
    assert res.n_trades == 1
    assert res.win_rate == 0.0
    assert res.avg_r_per_trade == -1.0
    assert res.total_r == -1.0


def test_short_profitable_target_hit():
    high = np.array([100.0, 95.0])
    low = np.array([100.0, 88.0])
    close = np.array([100.0, 100.0])
    res = backtest_trade(
        high, low, close,
        direction="SHORT", entry=100.0, stop=110.0, target=90.0,
    )
    assert res.n_trades == 1
    assert res.win_rate == 1.0
    assert res.total_r == 1.0  # (entry - target)/risk = (100-90)/10


def test_degenerate_flat_stop():
    high = np.array([100.0, 105.0])
    low = np.array([100.0, 95.0])
    close = np.array([100.0, 100.0])
    res = backtest_trade(
        high, low, close,
        direction="LONG", entry=100.0, stop=100.0, target=110.0,
    )
    assert res.n_trades == 0
    assert res.win_rate is None
    assert res.structured is False


def test_max_hold_close_out_no_lookahead():
    """If neither stop nor target is hit, exit at the bar right after entry
    (max_hold_bars=1) using that bar's close only — never the later bar."""
    high = np.array([100.0, 105.0, 110.0])
    low = np.array([100.0, 95.0, 90.0])
    close = np.array([100.0, 103.0, 104.0])
    res = backtest_trade(
        high, low, close,
        direction="LONG", entry=100.0, stop=90.0, target=110.0,
        max_hold_bars=1,
    )
    assert res.n_trades == 1
    # exit at close[1]=103 -> r = (103-100)/10 = 0.3 (NOT close[2]=104 -> 0.4)
    assert res.total_r == 0.3


def test_min_trades_gates_structured():
    high = np.array([100.0, 112.0])
    low = np.array([100.0, 101.0])
    close = np.array([100.0, 100.0])
    res = backtest_trade(
        high, low, close,
        direction="LONG", entry=100.0, stop=90.0, target=110.0,
    )  # default min_trades=5
    assert res.n_trades == 1
    assert res.structured is False


def test_mixed_trades_profit_factor_and_sharpe():
    # Two wins then a loss -> profit_factor = 2/1; sharpe defined.
    high = np.array([100.0, 112.0, 100.0, 112.0, 100.0, 95.0])
    low = np.array([100.0, 101.0, 100.0, 101.0, 100.0, 89.0])
    close = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    res = backtest_trade(
        high, low, close,
        direction="LONG", entry=100.0, stop=90.0, target=110.0,
        min_trades=3,
    )
    assert res.n_trades == 3
    assert res.total_r == 1.0  # +1 +1 -1
    assert res.profit_factor == 2.0
    assert res.sharpe is not None
