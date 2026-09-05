"""Risk gate: hard, code-enforced limits that must hold for every trade.

This is a pure, dependency-free module so it can be unit-tested in isolation.
The gate returns a structured verdict: APPROVE or REJECT with reasons.
Violating trades are rejected BEFORE any execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from backend.core.config import Settings


class Verdict(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


@dataclass
class TradeProposal:
    """A proposed trade, prior to execution."""
    symbol: str
    side: str  # BUY / SELL
    entry: float
    stop_loss: float
    target: float
    risk_amount: float  # absolute capital risked on this trade
    equity: float       # current account equity
    position_value: float  # proposed position market value
    open_positions: int    # current count of open positions
    total_exposure: float  # current total exposure fraction of equity


@dataclass
class RiskVerdict:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.verdict == Verdict.APPROVE


def _rr_ratio(p: TradeProposal) -> float | None:
    """reward / risk. Returns None if risk <= 0."""
    risk = abs(p.entry - p.stop_loss)
    if risk <= 0:
        return None
    reward = abs(p.target - p.entry)
    return reward / risk


def evaluate_trade(p: TradeProposal, cfg: Settings) -> RiskVerdict:
    """Evaluate a trade against every hard limit. Returns the verdict."""
    reasons: list[str] = []

    # --- Minimum risk:reward ---
    rr = _rr_ratio(p)
    if rr is None:
        reasons.append(f"Invalid risk/reward (SL == entry) for {p.symbol}")
    elif rr < cfg.min_rr_ratio:
        reasons.append(
            f"R/R {rr:.2f} < minimum {cfg.min_rr_ratio:.2f} ({p.symbol})"
        )

    # --- Max loss per trade ---
    if p.equity <= 0:
        reasons.append("Equity must be > 0")
    else:
        loss_frac = p.risk_amount / p.equity
        if loss_frac > cfg.max_loss_per_trade:
            reasons.append(
                f"Risk/loss {loss_frac:.1%} exceeds max loss per trade "
                f"{cfg.max_loss_per_trade:.1%} ({p.symbol})"
            )

    # --- Max position size per symbol ---
    if p.equity > 0 and p.position_value / p.equity > cfg.max_position_pct:
        reasons.append(
            f"Position {p.position_value / p.equity:.1%} of equity exceeds "
            f"max {cfg.max_position_pct:.1%} ({p.symbol})"
        )

    # --- Max concurrent positions ---
    if p.open_positions >= cfg.max_concurrent_positions:
        reasons.append(
            f"Open positions {p.open_positions} >= max "
            f"{cfg.max_concurrent_positions}"
        )

    # --- Round-trip exposure (this trade added to current exposure) ---
    if p.equity > 0:
        new_exposure = p.total_exposure + (p.position_value / p.equity)
        if new_exposure > cfg.max_total_exposure:
            reasons.append(
                f"Total exposure would reach {new_exposure:.1%} > "
                f"max {cfg.max_total_exposure:.1%}"
            )

    if reasons:
        return RiskVerdict(verdict=Verdict.REJECT, reasons=reasons)
    return RiskVerdict(verdict=Verdict.APPROVE, reasons=reasons)
