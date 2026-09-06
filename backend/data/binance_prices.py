"""Binance market-data access (crypto).

Hand-rolled, keyless client for Binance's public OHLCV + exchange-info
endpoints so we can analyze/trade *any* Binance spot coin (BTC, ETH, SOL,
XRP, UNI, and thousands of alts) that yfinance/OpenBB do not reliably cover.

Only public endpoints are used here (no API key / secret required):
  - GET /api/v3/klines        daily OHLCV
  - GET /api/v3/exchangeInfo  per-symbol LOT_SIZE / PRICE_FILTER precision

Every function returns plain data; the caller (market.py) is responsible for
normalizing to the canonical lowercase [open,high,low,close,volume] contract.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pandas as pd

log = logging.getLogger(__name__)

# Public Binance API (not testnet) — market data is free + keyless.
BINANCE_PUBLIC_BASE_URL = "https://api.binance.com"

# Http timeout for these lightweight calls.
_REQUEST_TIMEOUT = 10.0


def to_binance_symbol(symbol: str) -> str:
    """Map a user-provided symbol to Binance spot format (``UNIUSDT``).

    Accepts any reasonable coin spelling:
      'UNI-USD' / 'UNI/USDT' / 'UNIUSDT' / 'uni' / 'BTC-USD' -> 'BTCUSDT'
    Adds the ``USDT`` quote when the input has no quote (or uses USD/USDT).
    """
    if not symbol:
        return symbol
    s = symbol.upper().strip()
    # Normalize separators: 'UNI-USD'->'UNIUSDT', 'UNI/USDT'->'UNIUSDT', 'UNI/USD'->'UNIUSDT'
    if "-" in s or "/" in s:
        base, _, quote = s.partition("/") if "/" in s else s.partition("-")
        if quote == "USD":
            quote = "USDT"
        return (base + quote) if quote else base + "USDT"
    # Already 'BTCUSDT'-style (ends in a known quote, length reasonable)
    for quote in ("USDT", "USDC", "BUSD", "FDUSD"):
        if s.endswith(quote) and len(s) > len(quote):
            return s
    # Bare coin name or 'USD' suffix
    if s.endswith("USD") and len(s) > 3:
        return s[:-3] + "USDT"
    return s + "USDT"


def to_display_symbol(symbol: str) -> str:
    """Return a human-friendly ``UNI-USD`` label for replies/labels."""
    bc = to_binance_symbol(symbol)
    if bc.endswith("USDT"):
        return bc[:-4] + "-USD"
    if bc.endswith("USDC"):
        return bc[:-4] + "-USD"
    return bc


class BinancePricesError(Exception):
    """Raised when Binance market data cannot be fetched."""


def _get(url: str, params: dict[str, Any] | None = None) -> Any:
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            resp = client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise BinancePricesError(f"Binance request failed for {params}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - JSON decode etc.
        raise BinancePricesError(f"Binance response invalid for {params}: {exc}") from exc


def binance_klines(
    symbol: str,
    interval: str = "1d",
    limit: int = 500,
    base_url: str = BINANCE_PUBLIC_BASE_URL,
) -> pd.DataFrame:
    """Fetch daily OHLCV klines from Binance's public API.

    Returns a DataFrame indexed by DatetimeIndex (UTC daily open) with
    lowercase columns [open, high, low, close, volume].

    Kline fields (Binance): [0]open_time, [1]open, [2]high, [3]low,
    [4]close, [5]volume, ... (rest ignored).
    """
    bsym = to_binance_symbol(symbol)
    data = _get(f"{base_url}/api/v3/klines", {"symbol": bsym, "interval": interval, "limit": limit})
    if not isinstance(data, list) or not data:
        raise BinancePricesError(f"Binance returned no klines for {bsym}")

    rows = [
        {
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in data
    ]
    idx = pd.to_datetime([k[0] for k in data], unit="ms")
    df = pd.DataFrame(rows, index=idx)
    df.index.name = "date"
    return df[["open", "high", "low", "close", "volume"]]


class _ExchangeInfoCache:
    """Lazy per-symbol LOT_SIZE / PRICE_FILTER cache from /api/v3/exchangeInfo."""

    def __init__(self) -> None:
        self._symbols: dict[str, Any] = {}
        self._loaded = False

    def _load(self, base_url: str) -> None:
        if self._loaded:
            return
        info = _get(f"{base_url}/api/v3/exchangeInfo")
        pairs = {s["symbol"]: s for s in info.get("symbols", [])}
        self._symbols = pairs
        self._loaded = True

    def precision_for(self, symbol: str, base_url: str) -> dict[str, Any]:
        self._load(base_url)
        bsym = to_binance_symbol(symbol)
        info = self._symbols.get(bsym)
        if not info:
            return {}
        out: dict[str, Any] = {"qty_decimals": 6, "price_decimals": 2, "min_qty": 0.0, "tick_size": 0.0}
        for f in info.get("filters", []):
            ftype = f.get("filterType")
            if ftype == "LOT_SIZE":
                out["qty_decimals"] = _decimals_from_step(str(f.get("stepSize", "0.000001")))
                out["min_qty"] = float(f.get("minQty", 0))
            elif ftype == "PRICE_FILTER":
                out["price_decimals"] = _decimals_from_step(str(f.get("tickSize", "0.01")))
                out["tick_size"] = float(f.get("tickSize", 0))
        return out


def _decimals_from_step(step: str) -> int:
    """Return the number of decimal places implied by a step string like '0.01'."""
    if "." not in step:
        return 0
    return len(step.split(".")[1].rstrip("0")) or 0


_exchange_info_cache = _ExchangeInfoCache()