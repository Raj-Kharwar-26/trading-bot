"""Portfolio state management: in-memory + periodic persistence."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.config import settings
from backend.execution.paper import PaperEngine
from backend.execution.trade_log import TradeRecord, get_trade_log


@dataclass
class PortfolioSnapshot:
    timestamp: str
    cash: float
    total_equity: float
    unrealized_pnl: float
    realized_pnl: float
    positions: dict[str, dict]


class PortfolioState:
    """Manages portfolio state with PaperEngine + TradeLog + persistence."""

    def __init__(self, initial_cash: float = 1_000_000.0):
        self.engine = PaperEngine(
            initial_cash=initial_cash,
            cost_model=None,  # Will be set per-trade from market
            default_slippage_bps=5.0,
        )
        self.trade_log = get_trade_log()
        self._lock = threading.RLock()
        self._equity_curve: list[PortfolioSnapshot] = []
        self._initial_cash = initial_cash

    def place_and_fill(
        self,
        symbol: str,
        market: str,
        side: str,  # "LONG" or "SHORT"
        qty: float,
        entry_price: float,
        cost_model,
        timestamp: datetime | None = None,
    ) -> dict:
        """Place and immediately fill an entry order (for paper trading)."""
        ts = timestamp or datetime.utcnow()

        with self._lock:
            # Set cost model for this trade
            self.engine.cost_model = cost_model

            # Determine order side
            from backend.execution.paper import OrderSide
            order_side = OrderSide.BUY if side == "LONG" else OrderSide.SELL

            # Place and fill at entry_price
            order = self.engine.place_order(
                symbol=symbol,
                side=order_side,
                qty=qty,
            )
            fill = self.engine.match_order(order, entry_price)

            if not fill:
                return {"success": False, "error": "Order rejected or not filled"}

            return {
                "success": True,
                "order_id": order.id,
                "fill": {
                    "price": fill.price,
                    "qty": fill.qty,
                    "fee": fill.fee,
                    "timestamp": fill.timestamp.isoformat(),
                },
            }

    def close_position(
        self,
        symbol: str,
        market: str,
        side: str,  # "LONG" or "SHORT" - the original side
        exit_price: float,
        cost_model,
        exit_reason: str = "MANUAL",
        hold_bars: int = 0,
        timestamp: datetime | None = None,
    ) -> dict:
        """Close an open position and log the completed trade."""
        ts = timestamp or datetime.utcnow()

        with self._lock:
            pos = self.engine.get_position(symbol)
            if pos.qty == 0:
                return {"success": False, "error": "No open position"}

            # Save entry price BEFORE the exit fill updates the position
            entry_price = pos.avg_entry_price
            qty = abs(pos.qty)

            # Determine exit order side (opposite of entry)
            from backend.execution.paper import OrderSide
            exit_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY

            self.engine.cost_model = cost_model

            order = self.engine.place_order(
                symbol=symbol,
                side=exit_side,
                qty=qty,
            )
            fill = self.engine.match_order(order, exit_price)

            if not fill:
                return {"success": False, "error": "Exit order rejected"}

            # Calculate PnL
            if side == "LONG":
                gross_pnl = (exit_price - entry_price) * qty
            else:
                gross_pnl = (entry_price - exit_price) * qty

            net_pnl = gross_pnl - fill.fee
            # For simplicity, assume entry fee was similar
            entry_fee_est = entry_price * qty * (cost_model.buy_fee_pct if side == "LONG" else cost_model.sell_fee_pct)
            net_pnl -= entry_fee_est

            # Risk per share = 1% of entry price (default assumption if no stop tracked)
            risk_per_share = entry_price * 0.01
            gross_r = gross_pnl / (risk_per_share * qty) if risk_per_share > 0 and qty > 0 else 0
            net_r = net_pnl / (risk_per_share * qty) if risk_per_share > 0 and qty > 0 else 0

            # Log completed trade
            trade = TradeRecord(
                trade_id=f"TRD-{int(ts.timestamp())}",
                symbol=symbol,
                market=market,
                side=side,
                entry_time=ts.isoformat(),  # approximate
                exit_time=ts.isoformat(),
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                entry_fee=entry_fee_est,
                exit_fee=fill.fee,
                slippage_bps=cost_model.slippage_bps,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                gross_r=gross_r,
                net_r=net_r,
                hold_bars=hold_bars,
                exit_reason=exit_reason,
            )
            self.trade_log.log_trade(trade)

            return {
                "success": True,
                "fill": {
                    "price": fill.price,
                    "qty": fill.qty,
                    "fee": fill.fee,
                    "timestamp": fill.timestamp.isoformat(),
                },
                "trade": asdict(trade),
            }

    def get_portfolio(self, prices: dict[str, float] | None = None) -> PortfolioSnapshot:
        """Get current portfolio snapshot."""
        with self._lock:
            snap = self.engine.equity_curve_point(prices or {})
            return PortfolioSnapshot(
                timestamp=datetime.utcnow().isoformat(),
                cash=snap["cash"],
                total_equity=snap["total_equity"],
                unrealized_pnl=snap["unrealized_pnl"],
                realized_pnl=snap["realized_pnl"],
                positions=snap["positions"],
            )

    def get_equity_curve(self, limit: int = 1000) -> list[PortfolioSnapshot]:
        with self._lock:
            return self._equity_curve[-limit:]

    def record_equity_point(self, prices: dict[str, float]) -> None:
        """Record a point on the equity curve (call periodically)."""
        with self._lock:
            snap = self.get_portfolio(prices)
            self._equity_curve.append(snap)
            # Keep last 10000 points
            if len(self._equity_curve) > 10000:
                self._equity_curve = self._equity_curve[-10000:]

    def get_open_positions(self) -> dict[str, dict]:
        with self._lock:
            return {
                sym: {
                    "qty": p.qty,
                    "avg_entry": p.avg_entry_price,
                    "unrealized": 0.0,  # needs current price
                    "realized": p.realized_pnl,
                }
                for sym, p in self.engine.positions.items()
                if p.qty != 0
            }


# Global portfolio state (initialized on first use)
_portfolio_state: PortfolioState | None = None


def get_portfolio_state() -> PortfolioState:
    global _portfolio_state
    if _portfolio_state is None:
        _portfolio_state = PortfolioState(initial_cash=settings.paper_initial_cash)
    return _portfolio_state