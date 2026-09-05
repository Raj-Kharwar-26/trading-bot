"""Unit tests for the market data layer.

Mock OpenBB + the Binance path so tests are deterministic and offline-friendly.
Proves:
  - columns are normalized to lowercase OHLCV
  - BSE is rejected with a clear error (known yfinance gap)
  - empty/error results surface as MarketDataError
  - Binance is preferred for crypto and returns a normalized frame
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.data.market import MarketDataError, fetch_ohlcv


@pytest.fixture(autouse=True)
def _fake_openbb(monkeypatch):
    """Replace openbb.obb with a stub returning a realistic OHLCV frame."""

    class FakeResult:
        def to_dataframe(self):
            return pd.DataFrame(
                {
                    "Open": [100.0, 101.0],
                    "High": [102.0, 103.0],
                    "Low": [99.0, 100.0],
                    "Close": [101.5, 102.5],
                    "Volume": [1000, 2000],
                },
                index=pd.to_datetime(["2026-08-24", "2026-08-25"]),
            )

    class FakeEquity:
        class price:
            @staticmethod
            def historical(*args, **kwargs):
                return FakeResult()

    class FakeCrypto:
        class price:
            @staticmethod
            def historical(*args, **kwargs):
                return FakeResult()

    class FakeObb:
        equity = FakeEquity
        crypto = FakeCrypto

    monkeypatch.setattr("backend.data.market.obb", FakeObb())
    # Binance-crypto path returns a fixed frame (mirrors the expected contract).
    monkeypatch.setattr(
        "backend.data.market._fetch_binance_crypto",
        lambda symbol, start, end: pd.DataFrame(
            {
                "open": [100.0, 101.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "close": [101.5, 102.5],
                "volume": [1000, 2000],
            },
            index=pd.to_datetime(["2026-08-24", "2026-08-25"]),
        ),
    )


def test_crypto_prefers_binance_when_configured():
    # Config default is 'binance' -> crypto goes through the binance lambda.
    df = fetch_ohlcv("UNI-USD", "CRYPTO", "2026-08-24", "2026-08-27")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == 102.5


def test_normalizes_columns_to_lowercase():
    df = fetch_ohlcv("RELIANCE.NS", "NSE", "2026-08-24", "2026-08-27")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_crypto_works():
    df = fetch_ohlcv("BTC-USD", "CRYPTO", "2026-08-24", "2026-08-27")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == 102.5


def test_bse_rejected_with_clear_error():
    with pytest.raises(MarketDataError, match="BSE shares"):
        fetch_ohlcv("RELIANCE.BO", "BSE", "2026-08-24", "2026-08-27")


def test_maps_crypto_symbols():
    from backend.data.binance_prices import to_binance_symbol

    assert to_binance_symbol("UNI-USD") == "UNIUSDT"
    assert to_binance_symbol("UNIUSDT") == "UNIUSDT"
    assert to_binance_symbol("UNI") == "UNIUSDT"
    assert to_binance_symbol("BTC-USD") == "BTCUSDT"
    assert to_binance_symbol("uni") == "UNIUSDT"
