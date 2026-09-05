"""In-house paper execution simulator.

Pure-Python, no external deps. Reuses CostModel from backtest for fee/slippage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

from backend.backtest.costs import CostModel, fill_price, leg_fee


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def remaining_qty(self) -> float:
        return self.qty - self.filled_qty


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    fee: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0

    def market_value(self, current_price: float) -> float:
        return self.qty * current_price

    def unrealized_pnl(self, current_price: float) -> float:
        if self.qty == 0:
            return 0.0
        return (current_price - self.avg_entry_price) * self.qty

    def update_on_fill(self, fill: Fill) -> None:
        """Update position after a fill. Handles both opening and closing."""
        if fill.side == OrderSide.BUY:
            if self.qty >= 0:
                # Adding to long or opening long
                new_qty = self.qty + fill.qty
                if self.qty != 0:
                    self.avg_entry_price = (
                        (self.avg_entry_price * self.qty + fill.price * fill.qty) / new_qty
                    )
                else:
                    self.avg_entry_price = fill.price
                self.qty = new_qty
            else:
                # Covering short
                cover_qty = min(fill.qty, -self.qty)
                pnl = (self.avg_entry_price - fill.price) * cover_qty - fill.fee
                self.realized_pnl += pnl
                self.qty += cover_qty
                if self.qty == 0:
                    self.avg_entry_price = 0.0
                # Excess qty beyond cover becomes new long
                excess = fill.qty - cover_qty
                if excess > 0:
                    self.avg_entry_price = fill.price
                    self.qty = excess
        else:  # SELL
            if self.qty <= 0:
                # Adding to short or opening short
                new_qty = self.qty - fill.qty
                if self.qty != 0:
                    self.avg_entry_price = (
                        (self.avg_entry_price * (-self.qty) + fill.price * fill.qty) / (-new_qty)
                    )
                else:
                    self.avg_entry_price = fill.price
                self.qty = new_qty
            else:
                # Closing long
                close_qty = min(fill.qty, self.qty)
                pnl = (fill.price - self.avg_entry_price) * close_qty - fill.fee
                self.realized_pnl += pnl
                self.qty -= close_qty
                if self.qty == 0:
                    self.avg_entry_price = 0.0
                # Excess qty beyond close becomes new short
                excess = fill.qty - close_qty
                if excess > 0:
                    self.avg_entry_price = fill.price
                    self.qty = -excess


class PaperEngine:
    """Paper trading engine: order matching + position tracking + PnL."""

    def __init__(
        self,
        initial_cash: float = 1_000_000.0,
        cost_model: CostModel | None = None,
        default_slippage_bps: float = 5.0,
    ):
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.cost_model = cost_model
        self.default_slippage_bps = default_slippage_bps
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self._order_counter = 0

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"ORD-{self._order_counter:06d}"

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
    ) -> Order:
        order = Order(
            id=self._next_order_id(),
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
        )
        self.orders[order.id] = order
        return order

    def _compute_fill_price(
        self, symbol: str, side: OrderSide, reference_price: float
    ) -> float:
        """Compute fill price with slippage."""
        if self.cost_model:
            return fill_price(reference_price, action=side.value, slippage_bps=self.cost_model.slippage_bps)
        # Default simple slippage
        slippage = self.default_slippage_bps / 10000.0
        if side == OrderSide.BUY:
            return reference_price * (1 + slippage)
        return reference_price * (1 - slippage)

    def _compute_fee(self, side: OrderSide, price: float, qty: float) -> float:
        """Compute fee for a fill."""
        if self.cost_model:
            # leg_fee returns fee per unit of price, so multiply by qty
            return leg_fee(side.value, price, self.cost_model) * qty
        return 0.0

    def match_order(self, order: Order, reference_price: float) -> Fill | None:
        """Match an order at the given reference price (e.g., next bar open)."""
        if order.status != OrderStatus.PENDING:
            return None

        if order.order_type == OrderType.LIMIT:
            if order.side == OrderSide.BUY and reference_price > (order.limit_price or float("inf")):
                return None
            if order.side == OrderSide.SELL and reference_price < (order.limit_price or 0):
                return None
            # LIMIT order fills at the better price (market price if favorable, else limit)
            if order.side == OrderSide.BUY:
                fill_price = min(reference_price, order.limit_price or float("inf"))
            else:
                fill_price = max(reference_price, order.limit_price or 0)
        else:
            # MARKET order - apply slippage
            fill_price = self._compute_fill_price(order.symbol, order.side, reference_price)

        fill_qty = order.remaining_qty()
        fee = self._compute_fee(order.side, fill_price, fill_qty)
        notional = fill_price * fill_qty

        # Check cash for BUY
        if order.side == OrderSide.BUY and self.cash < notional + fee:
            order.status = OrderStatus.REJECTED
            order.updated_at = datetime.utcnow()
            return None

        # Execute fill
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=fill_qty,
            price=fill_price,
            fee=fee,
        )

        order.filled_qty = order.qty
        order.avg_fill_price = fill_price
        order.status = OrderStatus.FILLED
        order.updated_at = datetime.utcnow()

        self.fills.append(fill)

        # Update cash
        if order.side == OrderSide.BUY:
            self.cash -= notional + fee
        else:
            self.cash += notional - fee

        # Update position
        pos = self.get_position(order.symbol)
        pos.update_on_fill(fill)

        return fill

    def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order and order.status == OrderStatus.PENDING:
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.utcnow()
            return True
        return False

    def mark_to_market(self, prices: dict[str, float]) -> float:
        """Update unrealized PnL for all positions. Returns total portfolio value."""
        total = self.cash
        for symbol, pos in self.positions.items():
            if pos.qty != 0 and symbol in prices:
                total += pos.market_value(prices[symbol])
            else:
                total += pos.qty * pos.avg_entry_price  # fallback to entry
        return total

    def equity_curve_point(self, prices: dict[str, float]) -> dict:
        """Return a snapshot for equity curve."""
        total = self.mark_to_market(prices)
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "cash": self.cash,
            "total_equity": total,
            "unrealized_pnl": total - self.cash,
            "realized_pnl": sum(p.realized_pnl for p in self.positions.values()),
            "positions": {
                sym: {
                    "qty": p.qty,
                    "avg_entry": p.avg_entry_price,
                    "unrealized": p.unrealized_pnl(prices.get(sym, p.avg_entry_price)),
                    "realized": p.realized_pnl,
                }
                for sym, p in self.positions.items()
                if p.qty != 0 or p.realized_pnl != 0
            },
        }

    def get_open_orders(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status == OrderStatus.PENDING]

    def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)