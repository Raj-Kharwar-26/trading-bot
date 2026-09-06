"""Market data access layer (OpenBB).

Provides OHLCV + metadata for NSE, BSE, and crypto through OpenBB.

VERIFIED (2026-08-28):
  - NSE (`RELIANCE.NS`) -> yfinance provider: OK, no key needed.
  - Crypto (`BTC-USD`)   -> yfinance provider: OK, no key needed.
  - BSE  (`RELIANCE.BO`) -> yfinance provider: NO DATA (empty). BSE shares
    are not covered by yfinance; a paid provider (fmp/intrinio/tiingo) or a
    different source is required for BSE. This is documented as a known gap.

Column quirk: OpenBB/yfinance returns lowercase columns
  ['open','high','low','close','volume']; we normalize all our data to these.
"""

from __future__ import annotations

import logging
from typing import Literal

import pandas as pd
from openbb import obb

log = logging.getLogger(__name__)

MarketKind = Literal["NSE", "BSE", "CRYPTO"]


class MarketDataError(Exception):
    """Raised when market data cannot be fetched for the requested market."""


def infer_market(kind: MarketKind) -> tuple[str, str]:
    """Return a short ``tradeable`` description used for logging/metrics."""
    return {"NSE": ("nse", "/v1/equity"), "BSE": ("bse", "/v1/equity"), "CRYPTO": ("crypto", "/v2/coin")}[kind]


def fetch_ohlcv(
    symbol: str,
    kind: MarketKind,
    start_date: str,
    end_date: str,
    provider: str = "yfinance",
    interval: str = "1d",
) -> pd.DataFrame:
    """Fetch OHLCV for a symbol across a date range.

    ``interval`` is passed through for crypto (Binance klines) and NSE intraday
    (yfinance direct).  Daily (``1d``) is the default for swing; intraday
    callers pass ``1h`` or ``15m``.

    Returns a DataFrame indexed by date with lowercase columns
    ['open','high','low','close','volume'].
    """
    if kind == "BSE":
        raise MarketDataError(
            "BSE shares (.BO) are not covered by the default yfinance provider. "
            "Use NSE or a paid provider (fmp/intrinio/tiingo) for BSE data."
        )

    # --- NSE / BSE intraday via yfinance (best-effort) ---
    if kind in ("NSE", "BSE") and interval != "1d":
        return _fetch_nse_intrady(symbol, interval, end_date)

    # Crypto prefers the keyless Binance public API (covers all alt-coins that
    # yfinance/OpenBB don't reliably serve, e.g. UNI-USD). Falls back to the
    # configured OpenBB provider (yfinance) for compatibility / edge cases.
    # Respect settings.crypto_price_provider: 'binance' (default) or 'yfinance'.
    use_binance = kind == "CRYPTO" and _crypto_prefers_binance(provider)
    if use_binance:
        try:
            return _fetch_binance_crypto(symbol, start_date, end_date, interval=interval)
        except Exception as binance_exc:  # noqa: BLE001
            log.warning("binance crypto fetch failed for %s, falling back to OpenBB: %s", symbol, binance_exc)
            # fall through to the OpenBB path below

    try:
        if kind == "CRYPTO":
            result = obb.crypto.price.historical(
                symbol, provider=provider, start_date=start_date, end_date=end_date
            )
        else:  # NSE
            result = obb.equity.price.historical(
                symbol, provider=provider, start_date=start_date, end_date=end_date
            )
    except Exception as exc:  # noqa: BLE001 - surface provider errors as our own
        raise MarketDataError(f"{kind} fetch failed for {symbol}: {exc}") from exc

    df = result.to_dataframe()
    if df.empty:
        raise MarketDataError(f"No data returned for {kind} {symbol} in range {start_date}..{end_date}")

    # Normalize to lowercase OHLCV in a FIXED, documented order.
    rename = {c: c.lower() for c in df.columns}
    df = df.rename(columns=rename)
    ordered = ["open", "high", "low", "close", "volume"]
    required = set(ordered)
    if not required.issubset(df.columns):
        raise MarketDataError(
            f"{kind} {symbol} response missing expected columns; got {list(df.columns)}"
        )
    return df[[*ordered]]


def _crypto_prefers_binance(provider: str) -> bool:
    """Whether crypto OHLCV should read from the Binance public API.

    Returns True unless the caller explicitly passed a non-yfinance provider
    or settings force yfinance.
    """
    from backend.core.config import settings

    if provider and provider != "yfinance":
        return False
    return settings.crypto_price_provider.lower() != "yfinance"


def _fetch_binance_crypto(
    symbol: str, start_date: str, end_date: str, interval: str = "1d"
) -> pd.DataFrame:
    """Fetch crypto OHLCV from the keyless Binance public API.

    ``interval`` is forwarded to ``binance_klines`` (e.g. ``1d``, ``1h``).

    Returns a DataFrame already normalized to the canonical lowercase
    ``[open, high, low, close, volume]`` contract (DatetimeIndex).
    """
    from backend.data.binance_prices import binance_klines, to_binance_symbol

    st = pd.Timestamp(start_date)
    en = pd.Timestamp(end_date)

    df = binance_klines(to_binance_symbol(symbol), interval=interval, limit=1000)

    # Slice to the requested window (covers TZ edge at midnight).
    df = df.loc[(df.index >= st.floor("D")) & (df.index <= en.ceil("D"))]
    if df.empty:
        raise MarketDataError(
            f"No data returned for CRYPTO {symbol} in range {start_date}..{end_date} "
            f"(Binance, interval={interval})"
        )
    return df


def _fetch_nse_intrady(symbol: str, interval: str, end_date: str) -> pd.DataFrame:
    """Best-effort NSE/BSE intraday via yfinance.

    yfinance supports 15m/60m intervals for NSE tickers (.NS), but the
    history is limited (~60 days).  Raises ``MarketDataError`` on failure
    so callers can surface a clear caveat.
    """
    try:
        import yfinance as yf  # noqa: WPS433
    except ImportError as exc:
        raise MarketDataError(
            "yfinance is required for NSE intraday data"
        ) from exc

    from backend.core.config import settings

    try:
        df = yf.download(
            symbol,
            period=f"{settings.intraday_window_days_nse}d",
            interval=interval,
            progress=False,
            auto_adjust=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise MarketDataError(f"NSE intraday fetch failed for {symbol}: {exc}") from exc

    if df.empty:
        raise MarketDataError(
            f"No NSE intraday data returned for {symbol} (interval={interval}). "
            "Use swing (daily) or try a different symbol."
        )

    rename = {c: c.lower() for c in df.columns}
    df = df.rename(columns=rename)
    ordered = ["open", "high", "low", "close", "volume"]
    missing = [c for c in ordered if c not in df.columns]
    if missing:
        raise MarketDataError(
            f"NSE intraday data for {symbol} missing columns: {missing}"
        )
    return df[[*ordered]]
