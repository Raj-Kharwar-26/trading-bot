"""Tests for the trade-plan builder and interval-aware market data."""

import json

import numpy as np
import pandas as pd
import pytest

from backend.data.market import fetch_ohlcv
from backend.reasoning import planner
from backend.reasoning.planner import (
    _build_targets,
    _compute_grade,
    _compute_sizing,
    _resolve_entry,
    _resolve_stop,
    build_trade_plan,
    compute_atr,
)


def _fake_ohlcv(n: int = 200, end: float = 70.0) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = np.linspace(end * 50 / 70, end, n)
    high = close * 1.02
    low = close * 0.98
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    vol = np.full(n, 1000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


# ---------------------------------------------------------------------------
# ATR / setup helpers
# ---------------------------------------------------------------------------

def test_compute_atr_positive() -> None:
    assert compute_atr(_fake_ohlcv(), period=14) > 0


def test_resolve_entry_ai_within_band() -> None:
    last = 70.0
    entry, src = _resolve_entry({"entry_zone": "70.5"}, last)
    assert src == "ai"
    assert entry == 70.5


def test_resolve_entry_mechanical_when_outside_band() -> None:
    last = 70.0
    entry, src = _resolve_entry({"entry_zone": "51.0"}, last)  # AI far away
    assert src == "mechanical"
    assert entry == round(last, 6)


def test_resolve_entry_mechanical_when_missing() -> None:
    entry, src = _resolve_entry({}, 12.34)
    assert src == "mechanical"
    assert entry == 12.34


def test_resolve_stop_long_ai_valid() -> None:
    stop, src = _resolve_stop({"stop_loss": "69.0"}, 70.0, "LONG", atr=1.0)
    assert src == "ai"
    assert stop == 69.0


def test_resolve_stop_long_ai_wrong_side_falls_back() -> None:
    stop, src = _resolve_stop({"stop_loss": "71.0"}, 70.0, "LONG", atr=1.0)
    assert src == "mechanical"
    assert abs(stop - (70.0 - 2.0 * 1.0)) < 1e-9


def test_resolve_stop_long_mechanical() -> None:
    stop, src = _resolve_stop({}, 70.0, "LONG", atr=1.0)
    assert src == "mechanical"
    assert stop == 68.0


def test_resolve_stop_short_mechanical() -> None:
    stop, src = _resolve_stop({}, 70.0, "SHORT", atr=1.0)
    assert src == "mechanical"
    assert stop == 72.0


def test_build_targets_long() -> None:
    assert _build_targets("LONG", 10.0, 9.0) == {"T1": 11.0, "T2": 12.0, "T3": 13.0}


def test_build_targets_short() -> None:
    assert _build_targets("SHORT", 10.0, 12.0) == {"T1": 8.0, "T2": 6.0, "T3": 4.0}


def test_compute_sizing_clamped_by_position_cap() -> None:
    # equity 1M -> risk budget 10k; wide stop on low-priced coin would blow
    # past max_position_pct (5%) without clamping.
    sizing = _compute_sizing("UNI-USD", "CRYPTO", entry=7.0, stop=6.85)
    assert sizing["qty"] > 0
    equity = sizing["equity"]
    assert sizing["order_value"] <= equity * 0.051  # small tolerance for rounding


def test_compute_grade() -> None:
    assert (
        _compute_grade(
            {"status": "ok", "result": {"structured": True, "avg_r_per_trade": 0.5, "win_rate": 0.5, "n_trades": 16}}
        )
        == "PASS"
    )
    assert (
        _compute_grade(
            {"status": "ok", "result": {"structured": True, "avg_r_per_trade": -0.8, "win_rate": 0.2, "n_trades": 16}}
        )
        == "AVOID"
    )
    assert (
        _compute_grade(
            {"status": "ok", "result": {"structured": True, "avg_r_per_trade": 0.1, "win_rate": 0.3, "n_trades": 16}}
        )
        == "CAUTION"
    )
    assert _compute_grade({"status": "skipped"}) == "N/A"


# ---------------------------------------------------------------------------
# build_trade_plan end-to-end (mocked IO)
# ---------------------------------------------------------------------------

def _patch_io(monkeypatch, report: dict, df: pd.DataFrame | None = None) -> None:
    df = df if df is not None else _fake_ohlcv()
    monkeypatch.setattr(planner, "fetch_ohlcv", lambda *a, **k: df)
    monkeypatch.setattr("backend.backtest.runner.fetch_ohlcv", lambda *a, **k: df)
    monkeypatch.setattr(planner, "complete", lambda *a, **k: json.dumps(report))
    monkeypatch.setattr("backend.reasoning.agent_runner._retrieve", lambda *a, **k: "")
    monkeypatch.setattr(planner, "_persist_plan", lambda *a, **k: None)


def test_build_trade_plan_long_hybrid_ai_setup(monkeypatch) -> None:
    last = 70.0
    _patch_io(
        monkeypatch,
        {
            "signal": "LONG",
            "confidence": 0.8,
            "summary": "trending up",
            "entry_zone": str(last),
            "stop_loss": "65.0",
            "target": "75.0",
            "key_levels": ["support 65"],
            "strategy_applied": "none",
            "caveats": ["low volume"],
        },
    )
    plan = build_trade_plan("BTC-USD", "CRYPTO", style="swing")
    assert plan["direction"] == "LONG"
    assert plan["entry_source"] == "ai"
    assert plan["stop_source"] == "ai"
    assert plan["entry"] == 70.0
    assert plan["stop_loss"] == 65.0
    assert plan["targets"]["T2"] == 80.0  # +2R where R = 5
    assert plan["backtest"]["status"] in ("ok", "skipped", "error")
    assert plan["sizing"]["qty"] > 0


def test_build_trade_plan_long_hybrid_mechanical_fallback(monkeypatch) -> None:
    _patch_io(
        monkeypatch,
        {
            "signal": "LONG",
            "confidence": 0.7,
            "summary": "trending up",
            "entry_zone": None,
            "stop_loss": None,
            "target": None,
            "key_levels": [],
            "caveats": [],
        },
    )
    plan = build_trade_plan("BTC-USD", "CRYPTO", style="swing")
    assert plan["direction"] == "LONG"
    assert plan["entry_source"] == "mechanical"
    assert plan["stop_source"] == "mechanical"
    assert plan["entry"] == round(plan["last_close"], 6)
    assert plan["targets"]["T2"] > plan["entry"]  # LONG target above entry


def test_build_trade_plan_short(monkeypatch) -> None:
    _patch_io(
        monkeypatch,
        {"signal": "SHORT", "confidence": 0.6, "summary": "downtrend", "entry_zone": "70.0", "stop_loss": "75.0", "target": "60.0", "caveats": []},
    )
    plan = build_trade_plan("BTC-USD", "CRYPTO", style="swing")
    assert plan["direction"] == "SHORT"
    assert plan["entry"] == 70.0
    assert plan["stop_loss"] == 75.0
    assert plan["targets"]["T2"] < plan["entry"]  # SHORT target below entry


def test_build_trade_plan_neutral(monkeypatch) -> None:
    _patch_io(monkeypatch, {"signal": "NEUTRAL", "confidence": 0.5, "summary": "no edge", "caveats": []})
    plan = build_trade_plan("BTC-USD", "CRYPTO", style="swing")
    assert plan["direction"] == "NEUTRAL"
    assert plan["grade"] == "N/A"


# ---------------------------------------------------------------------------
# Interval-aware market data
# ---------------------------------------------------------------------------

def test_fetch_ohlcv_binance_interval_forwarded(monkeypatch) -> None:
    idx = pd.date_range("2025-01-01", periods=5, freq="D")
    fake = pd.DataFrame(
        {"open": [1] * 5, "high": [2] * 5, "low": [0.5] * 5, "close": [1.5] * 5, "volume": [10] * 5},
        index=idx,
    )
    caught: dict = {}

    def fake_binance(symbol, start_date, end_date, interval="1d"):
        caught["symbol"] = symbol
        caught["interval"] = interval
        return fake

    monkeypatch.setattr("backend.data.market._fetch_binance_crypto", fake_binance)
    df = fetch_ohlcv("BTC-USD", "CRYPTO", "2025-01-01", "2025-01-10", interval="1h")
    assert caught["interval"] == "1h"
    assert caught["symbol"] == "BTC-USD"
    assert len(df) == 5


def test_fetch_ohlcv_nse_intraday(monkeypatch) -> None:
    import yfinance as yf

    idx = pd.date_range("2025-01-01", periods=5, freq="15min")
    fake = pd.DataFrame(
        {"Open": [1] * 5, "High": [2] * 5, "Low": [0.5] * 5, "Close": [1.5] * 5, "Volume": [10] * 5},
        index=idx,
    )
    monkeypatch.setattr(yf, "download", lambda *a, **k: fake)
    df = fetch_ohlcv("RELIANCE.NS", "NSE", "2025-01-01", "2025-01-06", interval="15m")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 5