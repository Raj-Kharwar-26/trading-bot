"""Runner: turn a completed reasoning report + OHLCV into a backtest block.

Kept separate from the pure simulator so the simulator stays dependency-free
and unit-testable. runner.py is the only place that touches IO (market data)
and the report schema.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.core.config import settings
from backend.data.market import MarketDataError, fetch_ohlcv

from .costs import CostModel, cost_model_for_market
from .simulator import BacktestResult, backtest_trade

_DIR = {"LONG": "LONG", "SHORT": "SHORT"}


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_backtest_from_report(
    symbol: str,
    market: str,
    report: dict,
    days: int | None = None,
) -> dict:
    """Given a report dict, produce a ``backtest`` block (or empty).

    Returns keys: {status, message?, result?}.
    """
    signal = report.get("signal")
    direction = _DIR.get(signal) if signal else None
    entry = _as_float(report.get("entry_zone") or report.get("entry"))
    stop = _as_float(report.get("stop_loss"))
    target = _as_float(report.get("target"))

    base = {"status": "skipped", "message": ""}
    if not direction:
        base["message"] = "No directional signal (NEUTRAL) — nothing to backtest."
        return base
    if entry is None or stop is None or target is None:
        base["message"] = (
            "Report missing entry/stop/target — cannot backtest this setup."
        )
        return base

    # Only backtest the trailing window of data we already had at decision time,
    # so the backtest never uses bars after the analysis snapshot.
    days_for_window = days or settings.backtest_window_days
    end = pd.Timestamp.today().normalize()
    start = end - pd.Timedelta(days=days_for_window)

    try:
        df = fetch_ohlcv(symbol, market, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    except MarketDataError as exc:
        return {"status": "error", "message": str(exc)}

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    cost: CostModel | None = None
    if settings.backtest_costs_enabled:
        cost = cost_model_for_market(market, settings)

    result: BacktestResult = backtest_trade(
        high,
        low,
        close,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        max_hold_bars=settings.backtest_max_hold_bars,
        min_trades=settings.backtest_min_trades,
        cost=cost,
    )

    block = {"status": "ok", "result": result.to_dict()}
    if cost is not None:
        block["costs"] = {
            "slippage_bps": cost.slippage_bps,
            "buy_fee_pct": round(cost.buy_fee_pct, 6),
            "sell_fee_pct": round(cost.sell_fee_pct, 6),
        }
    return block
