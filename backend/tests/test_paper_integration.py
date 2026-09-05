"""Integration test: backtest signal -> paper execution."""

from __future__ import annotations

import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import sys
sys.path.insert(0, r"D:\Projects\trading-bot")

from datetime import date, timedelta

from backend.backtest.runner import build_backtest_from_report
from backend.backtest.costs import cost_model_for_market
from backend.core.config import Settings
from backend.data.market import fetch_ohlcv
from backend.execution.state import PortfolioState
from backend.execution.trade_log import TradeLog
from pathlib import Path
import tempfile


def test_backtest_to_paper_flow():
    """Full flow: generate report -> backtest -> if positive, paper trade."""
    with tempfile.TemporaryDirectory() as tmp:
        # Setup
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
            paper_initial_cash=100000,
            paper_log_dir=tmp,
        )

        # Monkey-patch runner settings
        import backend.backtest.runner as runner_module
        original_settings = runner_module.settings
        runner_module.settings = test_settings

        try:
            # Use a liquid NSE ticker
            symbol = "TCS.NS"
            market = "NSE"

            # Fetch real data
            end = date.today()
            start = end - timedelta(days=180)
            df = fetch_ohlcv(symbol, market, str(start), str(end))
            print(f"Fetched {len(df)} bars for {symbol}")

            # Build a synthetic LONG report
            recent_close = float(df["close"].iloc[-1])
            atr_proxy = float((df["high"] - df["low"]).tail(20).mean())
            entry = round(recent_close * 0.995, 2)
            stop = round(entry - 1.5 * atr_proxy, 2)
            target = round(entry + 3.0 * atr_proxy, 2)

            print(f"Synthetic LONG: entry={entry}, stop={stop}, target={target}")

            report = {
                "signal": "LONG",
                "confidence": 0.75,
                "summary": "Integration test signal",
                "entry": entry,
                "stop_loss": stop,
                "target": target,
                "risk_reward": round((target - entry) / (entry - stop), 2),
                "key_levels": [],
                "strategy_applied": "integration_test",
                "caveats": [],
                "_meta": {"symbol": symbol, "market": market, "rag_hits": 0, "engine": "lean"},
            }

            # Run backtest
            bt_result = build_backtest_from_report(symbol, market, report)
            print(f"Backtest status: {bt_result.get('status')}")

            if bt_result.get("status") != "ok":
                print(f"Backtest skipped: {bt_result.get('message')}")
                return  # Not a failure - just no setup

            res = bt_result["result"]
            print(f"Trades: {res['n_trades']}, Net R/trade: {res['avg_r_per_trade']:.4f}, Win rate: {res['win_rate']:.2%}")

            # Check if backtest is positive (net expectancy > 0)
            if res["avg_r_per_trade"] <= 0:
                print("Backtest expectancy negative - not trading")
                return

            # Positive backtest -> execute on paper
            print("Backtest positive -> executing paper trade")

            # Setup paper portfolio with fresh trade log
            trade_log = TradeLog(Path(tmp))
            portfolio = PortfolioState(initial_cash=test_settings.paper_initial_cash)
            portfolio.trade_log = trade_log

            cost_model = cost_model_for_market(market, test_settings)

            # Open position
            open_result = portfolio.place_and_fill(
                symbol=symbol,
                market=market,
                side="LONG",
                qty=100,  # 100 shares
                entry_price=entry,
                cost_model=cost_model,
            )
            assert open_result["success"], f"Open failed: {open_result.get('error')}"
            print(f"Opened: fill_price={open_result['fill']['price']}, fee={open_result['fill']['fee']}")

            # Simulate holding and closing at target
            close_result = portfolio.close_position(
                symbol=symbol,
                market=market,
                side="LONG",
                exit_price=target,
                cost_model=cost_model,
                exit_reason="TARGET",
                hold_bars=10,
            )
            assert close_result["success"], f"Close failed: {close_result.get('error')}"
            print(f"Closed: fill_price={close_result['fill']['price']}, fee={close_result['fill']['fee']}")

            # Verify trade logged
            trades = portfolio.trade_log.get_recent(10)
            assert len(trades) == 1
            trade = trades[0]
            assert trade.symbol == symbol
            assert trade.side == "LONG"
            assert trade.entry_price == pytest.approx(entry)
            assert trade.exit_price == pytest.approx(target)
            print(f"Trade logged: net_pnl={trade.net_pnl:.2f}, net_r={trade.net_r:.4f}")

            # Portfolio should have profit
            assert portfolio.engine.cash > test_settings.paper_initial_cash
            print(f"Final cash: {portfolio.engine.cash:.2f} (start: {test_settings.paper_initial_cash})")

            print("\n✅ Integration test PASSED: backtest -> paper execution flow works!")

        finally:
            runner_module.settings = original_settings


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])