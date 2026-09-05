"""Binance Live execution adapter (production).

Connects to Binance Spot (api.binance.com) for live trading.
Uses HMAC SHA256 signatures. Requires API key with trading permissions.
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
from backend.execution.risk_engine import AutoRiskEngine


@dataclass
class BinanceLiveConfig:
    api_key: str
    api_secret: str
    base_url: str = "https://api.binance.com"
    recv_window: int = 5000


class BinanceLiveClient:
    """Async client for Binance Spot REST API (live)."""

    def __init__(self, config: BinanceLiveConfig):
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
        data = resp.json()
        if "code" in data and data["code"] != 200:
            raise RuntimeError(f"Binance API error: {data.get('msg', 'Unknown')}")
        return data

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

    async def close(self) -> None:
        await self._client.aclose()

    # --- Helpers ---
    def _format_qty(self, symbol: str, qty: float) -> str:
        return f"{qty:.6f}".rstrip("0").rstrip(".")

    def _format_price(self, symbol: str, price: float | None) -> str:
        if price is None:
            return ""
        return f"{price:.2f}".rstrip("0").rstrip(".")


class BinanceLiveEngine:
    """
    Live execution engine for Binance Spot.
    Wraps BinanceLiveClient with order management and risk checks.
    """

    def __init__(
        self,
        risk_engine: AutoRiskEngine | None = None,
        config: BinanceLiveConfig | None = None,
    ):
        self.config = config or BinanceLiveConfig(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            base_url=settings.binance_base_url,
        )
        self.client: BinanceLiveClient | None = None
        self.risk_engine = risk_engine
        self._connected = False
        self._pending_approvals: dict[str, dict] = {}

    async def connect(self) -> bool:
        if not self.config.api_key or not self.config.api_secret:
            return False
        self.client = BinanceLiveClient(self.config)
        self._connected = await self.client.ping()
        return self._connected

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        require_approval: bool = True,
    ) -> Order:
        """Place an order (with optional approval workflow)."""
        from backend.execution.paper import Order as PaperOrder

        order = PaperOrder(
            id=f"BN-LIVE-{int(time.time() * 1000)}",
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            status=OrderStatus.PENDING,
        )

        # Risk pre-check
        if self.risk_engine:
            from backend.data.market import fetch_ohlcv
            from datetime import date, timedelta
            end = date.today()
            start = end - timedelta(days=1)
            try:
                df = fetch_ohlcv(symbol, "CRYPTO", str(start), str(end))
                ref_price = float(df["close"].iloc[-1]) if not df.empty else (limit_price or 0)
            except Exception:
                ref_price = limit_price or 0

            ok, msg = self.risk_engine.check_pre_fill(symbol, side, qty, ref_price or limit_price or 0)
            if not ok:
                order.status = OrderStatus.REJECTED
                order.updated_at = datetime.utcnow()
                return order

        # Approval workflow
        if require_approval and settings.approval_required:
            order.status = OrderStatus.PENDING
            self._pending_approvals[order.id] = {
                "order": order,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_type": order_type,
                "limit_price": limit_price,
                "created_at": time.time(),
            }
            return order

        # Execute immediately (no approval required)
        return await self._execute_order(order)

    async def approve_order(self, order_id: str) -> Order | None:
        """Approve a pending order."""
        pending = self._pending_approvals.pop(order_id, None)
        if not pending:
            return None
        order = pending["order"]
        return await self._execute_order(order)

    async def reject_order(self, order_id: str) -> bool:
        """Reject a pending order."""
        pending = self._pending_approvals.pop(order_id, None)
        if not pending:
            return False
        order = pending["order"]
        order.status = OrderStatus.REJECTED
        order.updated_at = datetime.utcnow()
        return True

    async def _execute_order(self, order: Order) -> Order:
        if not self._connected or self.client is None:
            order.status = OrderStatus.REJECTED
            return order

        try:
            resp = await self.client.create_order(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.qty,
                price=order.limit_price,
            )
            order.id = f"BN-LIVE-{resp['orderId']}"
            order.status = OrderStatus.FILLED if resp["status"] == "FILLED" else OrderStatus.PENDING

            # Record fill for risk engine
            if self.risk_engine and resp["status"] == "FILLED":
                fill = Fill(
                    order_id=order.id,
                    symbol=order.symbol,
                    side=order.side,
                    qty=float(resp["executedQty"]),
                    price=float(resp["cummulativeQuoteQty"]) / float(resp["executedQty"]),
                    fee=0.0,
                )
                self.risk_engine.on_fill(fill)
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.updated_at = datetime.utcnow()
        return order

    async def cancel_order(self, order_id: str) -> bool:
        if not self._connected or self.client is None:
            return False
        try:
            # Extract numeric order ID
            parts = order_id.split("-")
            if len(parts) >= 2:
                numeric_id = int(parts[-1])
                await self.client.cancel_order("", numeric_id)  # symbol needed
            return True
        except Exception:
            return False

    async def get_position(self, symbol: str):
        if not self._connected:
            return None
        account = await self.client.get_account()
        for balance in account.get("balances", []):
            if balance["asset"] == symbol.replace("USDT", ""):
                return {"free": float(balance["free"]), "locked": float(balance["locked"])}
        return None

    async def mark_to_market(self, prices: dict[str, float]) -> float:
        if not self._connected:
            return 0.0
        account = await self.client.get_account()
        total = 0.0
        for balance in account.get("balances", []):
            free = float(balance["free"])
            locked = float(balance["locked"])
            asset = balance["asset"]
            if asset in prices:
                total += (free + locked) * prices[asset]
            elif asset == "USDT":
                total += free + locked
        return total

    async def close(self) -> None:
        if self.client:
            await self.client.close()


async def get_binance_live_engine(
    risk_engine: AutoRiskEngine | None = None,
) -> BinanceLiveEngine:
    """Factory for Binance live engine."""
    engine = BinanceLiveEngine(risk_engine=risk_engine)
    await engine.connect()
    return engine