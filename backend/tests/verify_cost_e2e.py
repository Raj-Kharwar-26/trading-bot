#!/usr/bin/env python
"""Synthetic E2E verification for fee/slippage cost model.

Constructs a LONG signal report, fetches real OHLCV for a liquid NSE ticker,
runs the backtest with costs enabled, and asserts the cost breakdown.
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import sys
sys.path.insert(0, r"D:\Projects\trading-bot")

import numpy as np
from backend.backtest.runner import build_backtest_from_report
from backend.core.config import Settings
from backend.data.market import fetch_ohlcv


def main():
    # Use a liquid NSE ticker with good yfinance coverage
    symbol = "TCS.NS"
    market = "NSE"

    # Fetch enough bars for multiple trade cycles
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=180)
    df = fetch_ohlcv(symbol, kind="NSE", start_date=str(start), end_date=str(end))
    print(f"Fetched {len(df)} bars for {symbol}")

    # Construct a LONG report that will trigger entries
    # Use recent price levels for realistic entry/stop/target
    recent_close = float(df["close"].iloc[-1])
    atr_proxy = float((df["high"] - df["low"]).tail(20).mean())

    entry = round(recent_close * 0.995, 2)  # slightly below current
    stop = round(entry - 1.5 * atr_proxy, 2)
    target = round(entry + 3.0 * atr_proxy, 2)  # 2:1 R:R

    print(f"Synthetic LONG: entry={entry}, stop={stop}, target={target}, R:R={(target-entry)/(entry-stop):.2f}")

    report = {
        "signal": "LONG",
        "confidence": 0.75,
        "summary": "Synthetic test for cost model verification",
        "entry": entry,
        "stop_loss": stop,
        "target": target,
        "risk_reward": round((target - entry) / (entry - stop), 2),
        "key_levels": [],
        "strategy_applied": "synthetic_verification",
        "caveats": [],
        "_meta": {"symbol": symbol, "market": market, "rag_hits": 0, "engine": "lean"},
    }

    # Use a fresh settings with costs explicitly enabled
    test_settings = Settings(
        backtest_enabled=True,
        backtest_costs_enabled=True,
        backtest_window_days=180,
        backtest_max_hold_bars=20,
        backtest_min_trades=3,
        slippage_bps=15.0,
        equity_brokerage_pct=0.0003,
        equity_stt_pct=0.001,
        equity_transaction_pct=0.0000345,
        equity_sebi_pct=0.000001,
        equity_stamp_duty_pct=0.00015,
        equity_gst_pct=0.18,
        crypto_taker_pct=0.001,
    )

    # Monkey-patch the runner's settings import (it uses from backend.core.config import settings)
    import backend.backtest.runner as runner_module
    original_settings = runner_module.settings
    runner_module.settings = test_settings

    try:
        result = build_backtest_from_report(symbol, market, report)
    finally:
        runner_module.settings = original_settings

    print("\n=== BACKTEST RESULT ===")
    print(f"Status: {result.get('status')}")

    if result.get("status") == "ok":
        res = result["result"]
        print(f"Trades: {res['n_trades']}")
        print(f"Win rate: {res['win_rate']}")
        print(f"Avg R (net): {res['avg_r_per_trade']:.4f}")
        print(f"Avg gross R: {res['avg_gross_r_per_trade']:.4f}")
        print(f"Avg cost R: {res['avg_cost_r_per_trade']:.4f}")
        print(f"Total R: {res['total_r']:.4f}")
        print(f"Profit factor: {res['profit_factor']}")
        print(f"Max DD%: {res['max_drawdown_pct']}")
        print(f"Sharpe: {res['sharpe']}")
        print(f"Avg hold bars: {res['avg_hold_bars']:.1f}")

        costs = result.get("costs")
        if costs:
            print(f"\nCosts breakdown:")
            print(f"  Slippage: {costs['slippage_bps']} bps")
            print(f"  Buy fee: {costs['buy_fee_pct']:.6%}")
            print(f"  Sell fee: {costs['sell_fee_pct']:.6%}")

        # Assertions
        assert res["n_trades"] >= test_settings.backtest_min_trades, f"Expected >= {test_settings.backtest_min_trades} trades"
        assert res["avg_gross_r_per_trade"] is not None
        assert res["avg_cost_r_per_trade"] is not None
        assert res["avg_cost_r_per_trade"] > 0, "Cost drag should be positive"
        assert res["avg_r_per_trade"] < res["avg_gross_r_per_trade"], "Net R should be less than gross R"
        assert costs is not None, "Costs breakdown should be present"
        assert costs["slippage_bps"] == test_settings.slippage_bps
        assert costs["buy_fee_pct"] > 0
        assert costs["sell_fee_pct"] > costs["buy_fee_pct"], "Sell fee should include STT"

        print("\n✅ ALL ASSERTIONS PASSED — Cost model verified end-to-end")
        return 0
    else:
        print(f"Backtest skipped/error: {result}")
        return 1


if __name__ == "__main__":
    sys.exit(main())