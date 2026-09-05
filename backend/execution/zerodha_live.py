"""Zerodha Kite Live execution adapter (production NSE/BSE).

Uses Kite Connect REST API (https://kite.trade/docs/connect/v1/).
Live mode requires access_token from OAuth flow.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import httpx

from backend.core.config import settings
from backend.execution.paper import Fill, Order, OrderSide, OrderStatus, OrderType, PaperEngine
from backend.execution.risk_engine import AutoRiskEngine


class KiteExchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"


class KiteOrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class KiteTransactionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class KiteProduct(str, Enum):
    CNC = "CNC"
    MIS = "MIS"
    NRML = "NRML"


class KiteVariety(str, Enum):
    REGULAR = "regular"
    AMO = "amo"


@dataclass
class KiteLiveConfig:
    api_key: str
    api_secret: str
    access_token: str
    base_url: str = "https://api.kite.trade"


class KiteLiveClient:
    """Async client for Zerodha Kite Connect REST API (live)."""

    def __init__(self, config: KiteLiveConfig):
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

    # --- Orders ---
    async def place_order(
        self,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        order_type: str,
        product: str = "CNC",
        variety: str = "regular",
        price: float | None = None,
        trigger_price: float | None = None,
        validity: str = "DAY",
        disclosed_quantity: int = 0,
        tag: str | None = None,
    ) -> dict[str, Any]:
        data = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": transaction_type,
            "quantity": str(quantity),
            "order_type": order_type,
            "product": product,
            "variety": variety,
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
        variety: str = "regular",
        **kwargs,
    ) -> dict[str, Any]:
        endpoint = f"/orders/{variety}/{order_id}"
        return await self._request("PUT", endpoint, data=kwargs)

    async def cancel_order(self, order_id: str, variety: str = "regular") -> dict[str, Any]:
        endpoint = f"/orders/{variety}/{order_id}"
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

    async def get_margins(self) -> dict[str, Any]:
        return await self._request("GET", "/user/margins")

    async def close(self) -> None:
        await self._client.aclose()


class ZerodhaLiveEngine:
    """
    Live execution engine for NSE/BSE via Zerodha Kite Connect.
    """

    # Zerodha fee structure (equity delivery)
    BROKERAGE_PCT = 0.0003
    BROKERAGE_MAX = 20.0
    STT_PCT = 0.001
    EXCHANGE_TXN_PCT = 0.0000345
    SEBI_PCT = 0.000001
    STAMP_DUTY_PCT = 0.00015
    GST_PCT = 0.18

    def __init__(
        self,
        risk_engine=None,
        config: dict | None = None,
    ):
        self.config = config or {
            "api_key": settings.zerodha_api_key,
            "api_secret": settings.zerodha_api_secret,
            "access_token": settings.zerodha_access_token,
            "base_url": "https://api.kite.trade",
        }
        self.client = None
        self.risk_engine = risk_engine
        self._connected = False
        self._pending_approvals: dict[str, dict] = {}

    async def connect(self) -> bool:
        if not self.config.get("api_key") or not self.config.get("access_token"):
            return False
        self.client = self._create_client(self.config)
        try:
            await self.client.get_profile()
            self._connected = True
            return True
        except Exception:
            return False

    def _create_client(self, config: dict) -> "KiteLiveClient":
        from backend.execution.zerodha_live import KiteLiveClient, KiteLiveConfig
        return KiteLiveClient(KiteLiveConfig(**config))

    async def place_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        qty: int,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        product: str = "CNC",
        exchange: str = "NSE",
        require_approval: bool = True,
    ) -> dict:
        """Place an order (with optional approval workflow)."""
        from backend.execution.paper import Order as PaperOrder, OrderSide, OrderType, OrderStatus

        order = PaperOrder(
            id=f"KITE-LIVE-{int(time.time() * 1000)}",
            symbol=symbol,
            side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
            qty=qty,
            order_type=OrderType.MARKET if order_type == "MARKET" else OrderType.LIMIT,
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
                df = fetch_ohlcv(symbol, "NSE", str(start), str(end))
                ref_price = float(df["close"].iloc[-1]) if not df.empty else 0
            except Exception:
                ref_price = 0

            ok, msg = self.risk_engine.check_pre_fill(symbol, 
                OrderSide.BUY if side == "BUY" else OrderSide.SELL, qty, ref_price or 0)
            if not ok:
                order.status = OrderStatus.REJECTED
                return {"order_id": order.id, "status": "REJECTED", "reason": msg}

        # Approval workflow
        if settings.approval_required:
            order.status = "PENDING_APPROVAL"
            self._pending_approvals[order.id] = {
                "order": order,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_type": order_type,
                "limit_price": limit_price,
                "product": product,
                "exchange": exchange,
                "created_at": time.time(),
            }
            return {"order_id": order.id, "status": "PENDING_APPROVAL", "message": "Awaiting approval via Telegram"}

        return await self._execute_order(order, product, exchange)

    async def approve_order(self, order_id: str) -> dict:
        """Approve a pending order."""
        pending = self._pending_approvals.pop(order_id, None)
        if not pending:
            return {"success": False, "error": "Order not found or already processed"}
        return await self._execute_order(pending["order"], pending.get("product", "CNC"), pending.get("exchange", "NSE"))

    async def reject_order(self, order_id: str) -> bool:
        pending = self._pending_approvals.pop(order_id, None)
        if not pending:
            return False
        order = pending["order"]
        order.status = "REJECTED"
        return True

    async def _execute_order(self, order, product: str, exchange: str) -> dict:
        if not self.client:
            order.status = "REJECTED"
            return {"order_id": order.id, "status": "REJECTED", "reason": "Not connected"}

        try:
            tradingsymbol = order.symbol.replace(".NS", "").replace(".BO", "")
            kite_side = "BUY" if order.side.value == "BUY" else "SELL"
            kite_type = order.order_type.value

            resp = await self.client.place_order(
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                transaction_type=kite_side,
                quantity=int(order.qty),
                order_type=kite_type,
                product=product,
                price=order.limit_price,
            )
            order_id = resp.get("order_id", f"KITE-{int(time.time() * 1000)}")
            return {
                "order_id": order_id,
                "status": "PLACED",
                "kite_response": resp,
            }
        except Exception as e:
            return {"order_id": order.id, "status": "REJECTED", "reason": str(e)}

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self.client.cancel_order(order_id)
            return True
        except Exception:
            return False

    async def get_positions(self) -> dict:
        if not self.client:
            return {}
        return await self.client.get_positions()

    async def get_holdings(self) -> list:
        if not self.client:
            return []
        return await self.client.get_holdings()

    async def get_margins(self) -> dict:
        if not self.client:
            return {}
        return await self.client.get_margins()

    async def connect(self) -> bool:
        if not self.config.get("api_key") or not self.config.get("access_token"):
            return False
        self.client = self._create_client(self.config)
        try:
            await self.client.get_profile()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self.client:
            await self.client.close()


async def get_zerodha_live_engine(risk_engine=None) -> ZerodhaLiveEngine:
    """Factory for Zerodha live engine."""
    engine = ZerodhaLiveEngine(risk_engine=risk_engine)
    await engine.connect()
    return engine