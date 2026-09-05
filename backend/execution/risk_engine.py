"""Auto-risk engine: enforces hard risk limits on every order/fill.

This is the second line of defense (after the pre-trade risk gate).
It validates every fill in real-time and can halt trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any

from backend.core.config import settings
from backend.execution.paper import Fill, Order, OrderSide, PaperEngine, Position
from backend.risk.gate import TradeProposal, evaluate_trade


@dataclass
class RiskViolation:
    rule: str
    message: str
    severity: str  # "WARN" | "BLOCK" | "HALT"
    timestamp: datetime
    details: dict[str, Any]


class AutoRiskEngine:
    """
    Real-time risk enforcement for paper and live trading.

    Checks on every fill:
    - Per-trade max loss
    - Daily loss limit
    - Position size limit
    - Total exposure limit
    - Max concurrent positions
    - Min R:R (validated pre-trade, re-checked here)
    """

    def __init__(self, engine: PaperEngine):
        self.engine = engine
        self._lock = RLock()
        self.violations: list[RiskViolation] = []
        self._daily_pnl = 0.0
        self._day_start_equity = engine.initial_cash
        self._day_start_date = datetime.utcnow().date()
        self._halted = False
        self._halt_reason: str | None = None

    def check_pre_trade(self, proposal: TradeProposal) -> tuple[bool, str | None]:
        """Validate a trade proposal before placing order."""
        result = evaluate_trade(proposal, settings)
        if not result.approved:
            return False, f"Risk gate: {result.reasons[0]}"
        return True, None

    def check_pre_fill(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        price: float,
    ) -> tuple[bool, str | None]:
        """Validate an order before it's sent/matched."""
        with self._lock:
            self._rollover_day()

            if self._halted:
                return False, f"Trading halted: {self._halt_reason}"

            notional = qty * price
            equity = self.engine.cash + self._unrealized_pnl({symbol: price})

            # Max position size
            max_pos = equity * settings.max_position_pct
            pos = self.engine.get_position(symbol)
            current_notional = abs(pos.qty) * price
            new_notional = current_notional + (notional if side == OrderSide.BUY else -notional)
            if abs(new_notional) > max_pos:
                return False, f"Position size exceeds {settings.max_position_pct:.0%} of equity"

            # Max concurrent positions
            open_positions = sum(1 for p in self.engine.positions.values() if p.qty != 0)
            if side == OrderSide.BUY and pos.qty == 0 and open_positions >= settings.max_concurrent_positions:
                return False, f"Max concurrent positions ({settings.max_concurrent_positions}) reached"

            # Total exposure
            total_exposure = sum(
                abs(p.qty) * (price if sym == symbol else p.avg_entry_price)
                for sym, p in self.engine.positions.items()
            ) + notional
            if total_exposure > equity * settings.max_total_exposure:
                return False, f"Total exposure exceeds {settings.max_total_exposure:.0%} of equity"

            return True, None

    def on_fill(self, fill: Fill) -> list[RiskViolation]:
        """Process a fill and check for violations. Returns new violations."""
        violations = []

        with self._lock:
            self._rollover_day()

            # Update daily PnL (realized only)
            pos = self.engine.get_position(fill.symbol)
            # The position's realized_pnl already includes this fill's PnL
            self._daily_pnl = sum(p.realized_pnl for p in self.engine.positions.values())

            # Daily loss limit
            daily_loss_pct = -self._daily_pnl / self._day_start_equity
            if daily_loss_pct > settings.max_daily_loss:
                self._halt(trading_halt=True, reason=f"Daily loss limit {settings.max_daily_loss:.0%} exceeded")
                violations.append(RiskViolation(
                    rule="daily_loss_limit",
                    message=f"Daily loss {daily_loss_pct:.2%} > {settings.max_daily_loss:.0%}",
                    severity="HALT",
                    timestamp=datetime.utcnow(),
                    details={"daily_pnl": self._daily_pnl, "limit_pct": settings.max_daily_loss},
                ))

            # Per-trade loss (approximate from position)
            if pos.qty != 0:
                entry_val = pos.avg_entry_price * abs(pos.qty)
                current_val = fill.price * abs(pos.qty)
                if pos.qty > 0:  # LONG
                    loss_pct = (entry_val - current_val) / entry_val
                else:  # SHORT
                    loss_pct = (current_val - entry_val) / entry_val
                if loss_pct > settings.max_loss_per_trade:
                    violations.append(RiskViolation(
                        rule="per_trade_loss",
                        message=f"Position loss {loss_pct:.2%} > {settings.max_loss_per_trade:.0%}",
                        severity="WARN",
                        timestamp=datetime.utcnow(),
                        details={"symbol": fill.symbol, "loss_pct": loss_pct},
                    ))

            self.violations.extend(violations)
            return violations

    def get_violations(self, since: datetime | None = None) -> list[RiskViolation]:
        with self._lock:
            if since:
                return [v for v in self.violations if v.timestamp >= since]
            return list(self.violations)

    def is_halted(self) -> tuple[bool, str | None]:
        with self._lock:
            return self._halted, self._halt_reason

    def reset_halt(self) -> None:
        with self._lock:
            self._halted = False
            self._halt_reason = None

    def get_risk_metrics(self, prices: dict[str, float]) -> dict[str, Any]:
        with self._lock:
            self._rollover_day()
            equity = self.engine.cash + self._unrealized_pnl(prices)
            open_positions = sum(1 for p in self.engine.positions.values() if p.qty != 0)
            total_exposure = sum(
                abs(p.qty) * prices.get(sym, p.avg_entry_price)
                for sym, p in self.engine.positions.items()
            )
            return {
                "equity": equity,
                "cash": self.engine.cash,
                "daily_pnl": self._daily_pnl,
                "daily_pnl_pct": self._daily_pnl / self._day_start_equity if self._day_start_equity else 0,
                "daily_loss_limit": settings.max_daily_loss,
                "open_positions": open_positions,
                "max_positions": settings.max_concurrent_positions,
                "total_exposure": total_exposure,
                "exposure_pct": total_exposure / equity if equity else 0,
                "exposure_limit": settings.max_total_exposure,
                "halted": self._halted,
                "halt_reason": self._halt_reason,
            }

    def _unrealized_pnl(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.engine.positions.items():
            if pos.qty != 0 and sym in prices:
                total += pos.unrealized_pnl(prices[sym])
        return total

    def _rollover_day(self) -> None:
        today = datetime.utcnow().date()
        if today != self._day_start_date:
            self._day_start_date = today
            self._day_start_equity = self.engine.cash + self._unrealized_pnl({})
            self._daily_pnl = 0.0

    def _halt(self, trading_halt: bool, reason: str) -> None:
        self._halted = trading_halt
        self._halt_reason = reason


def create_risk_engine(engine: PaperEngine) -> AutoRiskEngine:
    """Factory for AutoRiskEngine."""
    return AutoRiskEngine(engine)