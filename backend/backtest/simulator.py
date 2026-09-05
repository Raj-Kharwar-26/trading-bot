"""Backtest engine: repeat a proposed setup's exit rule across historical OHLCV.

The report proposes a single setup (signal direction + entry/stop/target). A
single forward run gives one outcome, which tells us little. So we instead
replay the SAME entry/exit rule across the trailing history to get an honest
estimate of win-rate / expectancy / drawdown for that setup.

No-look-ahead guarantee: each bar's decision uses only the current bar's
high/low/close and prior information. Entry is level-triggered (price touches
the entry level) and exit is level-triggered (stop/target) or a max-hold exit
at close. We never condition on future bars.

Pure module (numpy/pandas only, no IO) so the math is unit-testable in
isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from backend.backtest.costs import CostModel, net_r

Direction = Literal["LONG", "SHORT"]


@dataclass
class BacktestResult:
    """Aggregated results from replaying the setup over history."""

    n_trades: int
    win_rate: float | None          # net win-rate (wins / n_trades)
    avg_r_per_trade: float | None   # mean NET R across trades
    total_r: float                  # sum of NET R
    profit_factor: float | None     # net wins / net losses
    max_drawdown_pct: float | None  # worst equity drawdown while risking 1R per trade
    sharpe: float | None            # mean / std of the per-trade NET R series
    avg_hold_bars: float | None
    avg_gross_r_per_trade: float | None = None  # mean R ignoring fees/slippage
    avg_cost_r_per_trade: float | None = None   # mean cost drag in R (None if no cost model)
    structured: bool = False        # whether enough trades to be meaningful

    def to_dict(self) -> dict:
        return {
            "n_trades": self.n_trades,
            "win_rate": round(self.win_rate, 4) if self.win_rate is not None else None,
            "avg_r_per_trade": round(self.avg_r_per_trade, 4)
            if self.avg_r_per_trade is not None else None,
            "total_r": round(self.total_r, 4),
            "profit_factor": round(self.profit_factor, 4)
            if self.profit_factor is not None else None,
            "max_drawdown_pct": round(self.max_drawdown_pct, 4)
            if self.max_drawdown_pct is not None else None,
            "sharpe": round(self.sharpe, 4) if self.sharpe is not None else None,
            "avg_hold_bars": round(self.avg_hold_bars, 2)
            if self.avg_hold_bars is not None else None,
            "avg_gross_r_per_trade": round(self.avg_gross_r_per_trade, 4)
            if self.avg_gross_r_per_trade is not None else None,
            "avg_cost_r_per_trade": round(self.avg_cost_r_per_trade, 4)
            if self.avg_cost_r_per_trade is not None else None,
            "structured": self.structured,
        }


@dataclass
class Trade:
    """One simulated round trip."""

    entry_idx: int
    exit_idx: int
    net_r: float            # outcome in R after fees + slippage
    gross_r: float = 0.0    # outcome in R before fees + slippage
    cost_r: float = 0.0     # cost drag in R


def _risk(entry: float, stop: float) -> float:
    return abs(entry - stop)


def backtest_trade(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    max_hold_bars: int = 20,
    min_trades: int = 5,
    cost: CostModel | None = None,
) -> BacktestResult:
    """Replay the setup across one OHLCV series.

    A new trade starts whenever ``close`` crosses the entry level from the
    favourable side (LONG: close <= entry; SHORT: close >= entry), after the
    previous trade has closed. Each trade then rides until stop/target is hit
    intra-bar, or ``max_hold_bars`` bars elapse (close-out at that bar's close).

    Each trade's outcome is measured in NET R (after fees + slippage) when a
    ``cost`` model is supplied; otherwise it equals the gross R (pure price).

    Returns a BacktestResult with the aggregated stats.
    """
    n = len(close)
    if n < 2:
        return BacktestResult(
            n_trades=0, win_rate=None, avg_r_per_trade=None, total_r=0.0,
            profit_factor=None, max_drawdown_pct=None, sharpe=None,
            avg_hold_bars=None, structured=False,
        )

    risk = _risk(entry, stop)
    if risk <= 0:
        # Degenerate setup -> nothing sensible to backtest.
        return BacktestResult(
            n_trades=0, win_rate=None, avg_r_per_trade=None, total_r=0.0,
            profit_factor=None, max_drawdown_pct=None, sharpe=None,
            avg_hold_bars=None, structured=False,
        )

    # Leg actions follow the opening/closing direction.
    if direction == "LONG":
        action_entry, action_exit = "BUY", "SELL"
    else:
        action_entry, action_exit = "SELL", "BUY"

    trades: list[Trade] = []
    i = 0
    while i < n:
        # Entry trigger for this direction.
        if direction == "LONG":
            if not close[i] <= entry:
                i += 1
                continue
        else:
            if not close[i] >= entry:
                i += 1
                continue

        # Ride the trade forward (starting next bar to avoid same-bar stop/target).
        exit_idx = None
        raw_exit = None
        for j in range(i + 1, min(i + 1 + max_hold_bars, n)):
            bar_high = high[j]
            bar_low = low[j]
            if direction == "LONG":
                if bar_low <= stop:
                    raw_exit, exit_idx = stop, j
                    break
                if bar_high >= target:
                    raw_exit, exit_idx = target, j
                    break
            else:
                if bar_high >= stop:
                    raw_exit, exit_idx = stop, j
                    break
                if bar_low <= target:
                    raw_exit, exit_idx = target, j
                    break
        else:
            # Max hold -> exit at the last bar's close (partial).
            exit_idx = min(i + max_hold_bars, n - 1)
            raw_exit = close[exit_idx]

        if cost is None:
            gross, cost_r, net = (
                (raw_exit - entry) / risk,
                0.0,
                (raw_exit - entry) / risk,
            ) if direction == "LONG" else (
                (entry - raw_exit) / risk,
                0.0,
                (entry - raw_exit) / risk,
            )
        else:
            gross, cost_r, net = net_r(
                direction=direction,
                action_entry=action_entry,
                action_exit=action_exit,
                entry_price=entry,
                exit_price=raw_exit,
                risk=risk,
                model=cost,
            )

        trades.append(
            Trade(entry_idx=i, exit_idx=exit_idx, net_r=float(net), gross_r=float(gross), cost_r=float(cost_r))
        )
        i = exit_idx + 1

    return _aggregate(trades, min_trades=min_trades)


def _aggregate(trades: list[Trade], min_trades: int) -> BacktestResult:
    n = len(trades)
    if n == 0:
        return BacktestResult(
            n_trades=0, win_rate=None, avg_r_per_trade=None, total_r=0.0,
            profit_factor=None, max_drawdown_pct=None, sharpe=None,
            avg_hold_bars=None, structured=False,
        )

    rs = np.array([t.net_r for t in trades])
    gross_rs = np.array([t.gross_r for t in trades])
    cost_rs = np.array([t.cost_r for t in trades])

    wins = rs[rs > 0]
    losses = rs[rs < 0]

    win_rate = float((rs > 0).mean())
    avg_r = float(rs.mean())
    total_r = float(rs.sum())
    avg_gross = float(gross_rs.mean())
    avg_cost = float(cost_rs.mean())

    profit_factor = None
    if float(wins.sum()) != 0 and float(abs(losses.sum())) != 0:
        profit_factor = float(wins.sum() / abs(losses.sum()))

    # Equity curve risking a constant 1R per trade, starting at 100.
    equity = 100.0 * np.cumprod(1.0 + rs / 100.0)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(dd.min()) if n > 0 else None

    sharpe = None
    std = float(rs.std())
    if n > 1 and std > 0:
        sharpe = float(rs.mean() / std)

    holds = np.array([t.exit_idx - t.entry_idx for t in trades])
    avg_hold = float(holds.mean())

    return BacktestResult(
        n_trades=n,
        win_rate=win_rate,
        avg_r_per_trade=avg_r,
        total_r=total_r,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        avg_hold_bars=avg_hold,
        avg_gross_r_per_trade=avg_gross,
        avg_cost_r_per_trade=avg_cost,
        structured=n >= min_trades,
    )
