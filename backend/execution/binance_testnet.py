"""Binance Testnet paper trading adapter.

Connects to Binance Spot Testnet (testnet.binance.vision) for realistic
paper execution with real order book data. Uses HMAC SHA256 signatures.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from backend.core.config import settings
from backend.execution.paper import Fill, Order, OrderSide, OrderStatus, OrderType


@dataclass
class BinanceConfig:
    api_key: str
    api_secret: str
    base_url: str = "https://testnet.binance.vision"
    recv_window: int = 5000


class BinanceTestnetClient:
    """Async client for Binance Spot Testnet REST API."""

    def __init__(self, config: BinanceConfig):
        self.config = config
        self._client = httpx.AsyncClient(timeout=10.0)

    def _sign(self, params: dict[str, str]) -> str:
        query = urllib.parse.urlencode(params)
        return hmac.new(
            self.config.api_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self.config.api_key}

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url}{endpoint}"
        params = params or {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.config.recv_window
            params["signature"] = self._sign(params)

        resp = await self._request_raw(method, url, params, signed)
        resp.raise_for_status()
        return resp.json()

    async def _request_raw(
        self,
        method: str,
        url: str,
        params: dict[str, Any],
        signed: bool,
    ) -> httpx.Response:
        headers = self._headers() if signed else {}
        if method == "GET":
            return await self._client.get(url, params=params, headers=headers)
        elif method == "POST":
            return await self._client.post(url, params=params, headers=headers)
        elif method == "DELETE":
            return await self._client.delete(url, params=params, headers=headers)
        raise ValueError(f"Unsupported method: {method}")

    # --- Public endpoints ---

    async def ping(self) -> bool:
        try:
            resp = await self._request("GET", "/api/v3/ping")
            return resp == {}
        except Exception:
            return False

    async def get_server_time(self) -> int:
        resp = await self._request("GET", "/api/v3/time")
        return resp["serverTime"]

    async def get_exchange_info(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/exchangeInfo")

    async def get_ticker_price(self, symbol: str) -> float:
        resp = await self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})
        return float(resp["price"])

    async def get_order_book(self, symbol: str, limit: int = 100) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/depth", {"symbol": symbol, "limit": limit})

    async def get_klines(
        self, symbol: str, interval: str, limit: int = 500
    ) -> list[list[Any]]:
        return await self._request(
            "GET", "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit}
        )

    # --- Signed endpoints ---

    async def get_account(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/account", signed=True)

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
        time_in_force: str = "GTC",
    ) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": side.value,
            "type": order_type.value,
            "quantity": self._format_qty(symbol, quantity),
        }
        if order_type == OrderType.LIMIT:
            params["price"] = self._format_price(symbol, price)
            params["timeInForce"] = time_in_force
        return await self._request("POST", "/api/v3/order", params, signed=True)

    async def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        return await self._request(
            "DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True
        )

    async def get_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        return await self._request(
            "GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else {}
        return await self._request("GET", "/api/v3/openOrders", params, signed=True)

    async def get_all_orders(
        self, symbol: str, limit: int = 500
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/api/v3/allOrders", {"symbol": symbol, "limit": limit}, signed=True
        )

    async def close(self) -> None:
        await self._client.aclose()

    # --- Helpers ---

    def _format_qty(self, symbol: str, qty: float) -> str:
        # Use per-symbol LOT_SIZE precision from public exchangeInfo; fall back
        # to a safe guess if it cannot be fetched (alt-coins vary a lot).
        prec, _ = self._precision(symbol)
        return f"{qty:.{prec}f}".rstrip("0").rstrip(".")

    def _format_price(self, symbol: str, price: float | None) -> str:
        if price is None:
            return ""
        _, pprec = self._precision(symbol)
        return f"{price:.{pprec}f}".rstrip("0").rstrip(".")

    def _precision(self, symbol: str) -> tuple[int, int]:
        """Return (qty_decimals, price_decimals) for a symbol from exchangeInfo."""
        try:
            from backend.data.binance_prices import (
                _exchange_info_cache,
                _get,
                BINANCE_PUBLIC_BASE_URL,
            )
            p = _exchange_info_cache.precision_for(symbol, self.config.base_url)
            return p.get("qty_decimals", 6), p.get("price_decimals", 2)
        except Exception:  # noqa: BLE001 - non-fatal; use defaults
            return 6, 2


class BinanceTestnetPaperEngine:
    """
    Paper engine that routes orders to Binance Testnet for realistic fills.
    Falls back to local PaperEngine if API unavailable.
    """

    def __init__(
        self,
        local_engine=None,  # PaperEngine instance for fallback
        config: BinanceConfig | None = None,
    ):
        self.local = local_engine
        self.config = config or BinanceConfig(
            api_key=settings.binance_testnet_api_key,
            api_secret=settings.binance_testnet_api_secret,
            base_url=settings.binance_testnet_base_url,
        )
        self.client: BinanceTestnetClient | None = None
        self._connected = False

    async def connect(self) -> bool:
        if not self.config.api_key or not self.config.api_secret:
            return False
        self.client = BinanceTestnetClient(self.config)
        self._connected = await self.client.ping()
        return self._connected

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
    ) -> Order:
        order = self.local.place_order(symbol, side, qty, order_type, limit_price)

        if not self._connected or self.client is None:
            return order

        try:
            resp = await self.client.create_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=qty,
                price=limit_price,
            )
            order.id = f"BN-{resp['orderId']}"
            order.status = OrderStatus.FILLED if resp["status"] == "FILLED" else OrderStatus.PENDING
        except Exception:
            # Fallback to local
            pass

        return order

    async def match_order(self, order: Order, reference_price: float) -> Fill | None:
        if not self._connected or self.client is None:
            return self.local.match_order(order, reference_price)

        try:
            # For MARKET orders, query order status
            if order.id.startswith("BN-"):
                order_id = int(order.id.split("-")[1])
                resp = await self.client.get_order(order.symbol, order_id)
                if resp["status"] == "FILLED":
                    fill = Fill(
                        order_id=order.id,
                        symbol=order.symbol,
                        side=order.side,
                        qty=float(resp["executedQty"]),
                        price=float(resp["cummulativeQuoteQty"]) / float(resp["executedQty"]),
                        fee=0.0,  # Binance fees in separate endpoint
                    )
                    order.filled_qty = fill.qty
                    order.avg_fill_price = fill.price
                    order.status = OrderStatus.FILLED
                    return fill
        except Exception:
            pass

        return self.local.match_order(order, reference_price)

    async def get_position(self, symbol: str):
        return self.local.get_position(symbol)

    async def mark_to_market(self, prices: dict[str, float]) -> float:
        return self.local.mark_to_market(prices)

    async def get_portfolio(self, prices: dict[str, float] | None = None):
        return self.local.get_portfolio(prices)

    async def close(self) -> None:
        if self.client:
            await self.client.close()


async def get_binance_testnet_engine(local_engine=None) -> BinanceTestnetPaperEngine:
    """Factory for Binance testnet engine."""
    engine = BinanceTestnetPaperEngine(local_engine=local_engine)
    await engine.connect()
    return engine