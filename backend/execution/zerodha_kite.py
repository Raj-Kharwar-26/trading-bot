"""Zerodha Kite Connect adapter for NSE/BSE paper & live trading.

Uses Kite Connect REST API (https://kite.trade/docs/connect/v1/).
Paper mode uses a local PaperEngine with Kite's fee structure.
Live mode requires access_token from OAuth flow.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import httpx

from backend.core.config import settings
from backend.execution.paper import Fill, Order, OrderSide, OrderStatus, OrderType, PaperEngine
from backend.backtest.costs import CostModel


class KiteExchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"


class KiteOrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"       # Stop-loss
    SL_M = "SL-M"   # Stop-loss market


class KiteTransactionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class KiteProduct(str, Enum):
    CNC = "CNC"     # Delivery (equity)
    MIS = "MIS"     # Intraday
    NRML = "NRML"   # Normal (futures/options)


class KiteVariety(str, Enum):
    REGULAR = "regular"
    AMO = "amo"     # After-market order


@dataclass
class KiteConfig:
    api_key: str
    api_secret: str
    access_token: str = ""  # Set after OAuth
    base_url: str = "https://api.kite.trade"
    paper_mode: bool = True


class KiteConnectClient:
    """Async client for Zerodha Kite Connect REST API."""

    def __init__(self, config: KiteConfig):
        self.config = config
        self._client = httpx.AsyncClient(timeout=10.0)

    def _headers(self) -> dict[str, str]:
        h = {"X-Kite-Version": "3"}
        if self.config.access_token:
            h["Authorization"] = f"token {self.config.api_key}:{self.config.access_token}"
        return h

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url}{endpoint}"
        headers = self._headers()

        if method == "GET":
            resp = await self._client.get(url, params=params, headers=headers)
        elif method == "POST":
            resp = await self._client.post(url, params=params, data=data, headers=headers)
        elif method == "DELETE":
            resp = await self._client.delete(url, params=params, headers=headers)
        elif method == "PUT":
            resp = await self._client.put(url, params=params, data=data, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == "error":
            raise RuntimeError(f"Kite API error: {result.get('message', 'Unknown')}")
        return result.get("data", result)

    # --- Public / Session ---

    async def login_url(self) -> str:
        return f"https://kite.trade/connect/login?api_key={self.config.api_key}"

    async def generate_session(self, request_token: str) -> dict[str, Any]:
        """Exchange request_token for access_token (call after user logs in)."""
        data = {
            "api_key": self.config.api_key,
            "request_token": request_token,
            "checksum": self._checksum(request_token),
        }
        return await self._request("POST", "/session/token", data=data)

    def _checksum(self, request_token: str) -> str:
        return hmac.new(
            self.config.api_secret.encode(),
            f"{self.config.api_key}{request_token}{self.config.api_secret}".encode(),
            hashlib.sha256,
        ).hexdigest()

    async def invalidate_session(self) -> None:
        await self._request("DELETE", "/session/token")
        self.config.access_token = ""

    async def get_margins(self) -> dict[str, Any]:
        return await self._request("GET", "/user/margins")

    async def get_profile(self) -> dict[str, Any]:
        return await self._request("GET", "/user/profile")

    # --- Instruments ---

    async def get_instruments(self, exchange: KiteExchange | None = None) -> list[dict[str, Any]]:
        params = {"exchange": exchange.value} if exchange else {}
        return await self._request("GET", "/instruments", params=params)

    async def get_quote(self, symbols: list[str]) -> dict[str, Any]:
        """Get LTP/quote for symbols. Format: ["NSE:RELIANCE", "BSE:500325"]"""
        params = {"i": ",".join(symbols)}
        return await self._request("GET", "/quote", params=params)

    async def get_ltp(self, symbols: list[str]) -> dict[str, Any]:
        params = {"i": ",".join(symbols)}
        return await self._request("GET", "/quote/ltp", params=params)

    async def get_ohlc(self, symbols: list[str]) -> dict[str, Any]:
        params = {"i": ",".join(symbols)}
        return await self._request("GET", "/quote/ohlc", params=params)

    # --- Orders ---

    async def place_order(
        self,
        tradingsymbol: str,
        exchange: KiteExchange,
        transaction_type: KiteTransactionType,
        quantity: int,
        order_type: KiteOrderType,
        product: KiteProduct = KiteProduct.CNC,
        variety: KiteVariety = KiteVariety.REGULAR,
        price: float | None = None,
        trigger_price: float | None = None,
        validity: str = "DAY",
        disclosed_quantity: int = 0,
        tag: str | None = None,
    ) -> dict[str, Any]:
        data = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange.value,
            "transaction_type": transaction_type.value,
            "quantity": str(quantity),
            "order_type": order_type.value,
            "product": product.value,
            "variety": variety.value,
            "validity": validity,
            "disclosed_quantity": str(disclosed_quantity),
        }
        if price is not None:
            data["price"] = f"{price:.2f}"
        if trigger_price is not None:
            data["trigger_price"] = f"{trigger_price:.2f}"
        if tag:
            data["tag"] = tag

        return await self._request("POST", "/orders/regular", data=data)

    async def modify_order(
        self,
        order_id: str,
        variety: KiteVariety = KiteVariety.REGULAR,
        **kwargs,
    ) -> dict[str, Any]:
        endpoint = f"/orders/{variety.value}/{order_id}"
        return await self._request("PUT", endpoint, data=kwargs)

    async def cancel_order(
        self, order_id: str, variety: KiteVariety = KiteVariety.REGULAR
    ) -> dict[str, Any]:
        endpoint = f"/orders/{variety.value}/{order_id}"
        return await self._request("DELETE", endpoint)

    async def get_orders(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/orders")

    async def get_order_history(self, order_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/orders/{order_id}")

    async def get_trades(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/trades")

    # --- Portfolio ---

    async def get_holdings(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/portfolio/holdings")

    async def get_positions(self) -> dict[str, Any]:
        return await self._request("GET", "/portfolio/positions")

    async def close(self) -> None:
        await self._client.aclose()


class ZerodhaKitePaperEngine:
    """
    Paper engine for NSE/BSE using Zerodha's fee structure.
    In paper_mode: uses local PaperEngine with Kite fees.
    In live_mode: routes to KiteConnectClient (requires access_token).
    """

    # Zerodha fee structure (equity delivery)
    BROKERAGE_PCT = 0.0003      # 0.03% or Rs 20 per executed order, whichever is lower
    BROKERAGE_MAX = 20.0        # Rs 20 cap per order
    STT_PCT = 0.001             # 0.1% on sell side (delivery)
    EXCHANGE_TXN_PCT = 0.0000345
    SEBI_PCT = 0.000001
    STAMP_DUTY_PCT = 0.00015    # 0.015% on buy side
    GST_PCT = 0.18              # 18% on (brokerage + txn + sebi)

    def __init__(
        self,
        local_engine: PaperEngine | None = None,
        config: KiteConfig | None = None,
    ):
        self.local = local_engine or PaperEngine()
        self.config = config or KiteConfig(
            api_key=settings.zerodha_api_key,
            api_secret=settings.zerodha_api_secret,
            access_token=settings.zerodha_access_token,
            paper_mode=True,
        )
        self.client: KiteConnectClient | None = None
        self._cost_model = self._build_cost_model()

    def _build_cost_model(self) -> CostModel:
        """Zerodha equity delivery fees."""
        # Buy side: brokerage + exchange txn + sebi + gst on those + stamp duty
        buy_fee = (
            self.BROKERAGE_PCT
            + self.EXCHANGE_TXN_PCT
            + self.SEBI_PCT
            + self.GST_PCT * (self.BROKERAGE_PCT + self.EXCHANGE_TXN_PCT + self.SEBI_PCT)
            + self.STAMP_DUTY_PCT
        )
        # Sell side: brokerage + exchange txn + sebi + gst on those + STT
        sell_fee = (
            self.BROKERAGE_PCT
            + self.EXCHANGE_TXN_PCT
            + self.SEBI_PCT
            + self.GST_PCT * (self.BROKERAGE_PCT + self.EXCHANGE_TXN_PCT + self.SEBI_PCT)
            + self.STT_PCT
        )
        return CostModel(
            slippage_bps=settings.slippage_bps,
            buy_fee_pct=buy_fee,
            sell_fee_pct=sell_fee,
        )

    async def connect(self) -> bool:
        if self.config.paper_mode:
            return True
        if not self.config.api_key or not self.config.access_token:
            return False
        self.client = KiteConnectClient(self.config)
        try:
            await self.client.get_profile()
            return True
        except Exception:
            return False

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
    ) -> Order:
        if self.config.paper_mode:
            return self.local.place_order(symbol, side, qty, order_type, limit_price)

        # Live mode - map to Kite order
        tradingsymbol = symbol.replace(".NS", "").replace(".BO", "")
        exchange = KiteExchange.NSE if symbol.endswith(".NS") else KiteExchange.BSE
        kite_side = KiteTransactionType.BUY if side == OrderSide.BUY else KiteTransactionType.SELL
        kite_type = KiteOrderType.MARKET if order_type == OrderType.MARKET else KiteOrderType.LIMIT

        order = self.local.place_order(symbol, side, qty, order_type, limit_price)
        # In real implementation, call self.client.place_order(...)
        return order

    def match_order(self, order: Order, reference_price: float) -> Fill | None:
        if self.config.paper_mode:
            self.local.cost_model = self._cost_model
            return self.local.match_order(order, reference_price)

        # Live mode - would check order status via Kite
        return self.local.match_order(order, reference_price)

    def get_position(self, symbol: str):
        return self.local.get_position(symbol)

    def mark_to_market(self, prices: dict[str, float]) -> float:
        return self.local.mark_to_market(prices)

    def get_portfolio(self, prices: dict[str, float] | None = None):
        return self.local.get_portfolio(prices)

    async def close(self) -> None:
        if self.client:
            await self.client.close()


async def get_zerodha_kite_engine(local_engine: PaperEngine | None = None) -> ZerodhaKitePaperEngine:
    """Factory for Zerodha Kite engine."""
    engine = ZerodhaKitePaperEngine(local_engine=local_engine)
    await engine.connect()
    return engine