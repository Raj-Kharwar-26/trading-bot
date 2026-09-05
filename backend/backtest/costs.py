"""Realistic cost model for the backtest (Phase 3 prep).

Pure module (no IO) so the fee math is unit-testable in isolation.

A trade's outcome is measured in R multiples where R = |entry - stop|. We
surface that number AFTER costs:

    net_r = gross_r - cost_r
    gross_r = (exit_fill - entry_fill) / risk        (LONG)
    gross_r = (entry_fill - exit_fill) / risk        (SHORT)

Slippage makes fills worse (buy higher / sell lower) and therefore reduces
gross_r. Brokerage/STT/etc. are a separate cash cost expressed in R:

    cost_r = buy_fee_pct * buy_price / risk + sell_fee_pct * sell_price / risk

where buy_price/sell_price are the (slippage-adjusted) fill prices and the
fee fractions are of the traded notional on each leg.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["BUY", "SELL"]
Market = Literal["NSE", "BSE", "CRYPTO"]


@dataclass
class CostModel:
    """Per-side fee fractions (of notional) + adverse slippage.

    ``buy_fee_pct`` applies when the leg action is BUY; ``sell_fee_pct`` when
    SELL. Slippage is symmetric per fill (sign chosen by the action).
    """

    slippage_bps: float = 0.0
    buy_fee_pct: float = 0.0
    sell_fee_pct: float = 0.0


def _bps(bps: float) -> float:
    return bps / 10_000.0


def fill_price(price: float, *, action: Action, slippage_bps: float) -> float:
    """Adverse slippage on a fill. BUY fills higher, SELL fills lower."""
    slip = _bps(slippage_bps)
    if action == "BUY":
        return price * (1.0 + slip)
    return price * (1.0 - slip)


def equity_buy_fee(cfg: Any) -> float:
    return (
        cfg.equity_brokerage_pct
        + cfg.equity_transaction_pct
        + cfg.equity_sebi_pct
        + cfg.equity_gst_pct
        * (cfg.equity_brokerage_pct + cfg.equity_transaction_pct + cfg.equity_sebi_pct)
        + cfg.equity_stamp_duty_pct
    )


def equity_sell_fee(cfg: Any) -> float:
    return (
        cfg.equity_brokerage_pct
        + cfg.equity_transaction_pct
        + cfg.equity_sebi_pct
        + cfg.equity_gst_pct
        * (cfg.equity_brokerage_pct + cfg.equity_transaction_pct + cfg.equity_sebi_pct)
        + cfg.equity_stt_pct
    )


def cost_model_for_market(market: Market, cfg: Any) -> CostModel:
    """Build the applicable CostModel for a market from settings."""
    slip = cfg.slippage_bps
    if market == "CRYPTO":
        return CostModel(slippage_bps=slip, buy_fee_pct=cfg.crypto_taker_pct, sell_fee_pct=cfg.crypto_taker_pct)
    # NSE / BSE (delivery-style equity).
    return CostModel(
        slippage_bps=slip,
        buy_fee_pct=equity_buy_fee(cfg),
        sell_fee_pct=equity_sell_fee(cfg),
    )


def leg_fee(action: Action, price: float, model: CostModel) -> float:
    """Cash fee in R units for a single leg, given the (filled) price."""
    pct = model.buy_fee_pct if action == "BUY" else model.sell_fee_pct
    return pct * price


def net_r(
    *,
    direction: str,
    action_entry: Action,
    action_exit: Action,
    entry_price: float,
    exit_price: float,
    risk: float,
    model: CostModel,
) -> tuple[float, float, float]:
    """Return (gross_r, cost_r, net_r) for one round trip.

    ``entry_price``/``exit_price`` are the RAW (pre-slippage) trigger levels;
    fills are computed inside with adverse slippage.
    """
    buy_fill = fill_price(entry_price, action=action_entry, slippage_bps=model.slippage_bps)
    exit_fill = fill_price(exit_price, action=action_exit, slippage_bps=model.slippage_bps)

    if direction == "LONG":
        gross = (exit_fill - buy_fill) / risk
    else:
        gross = (buy_fill - exit_fill) / risk

    cost = leg_fee(action_entry, buy_fill, model) / risk + leg_fee(
        action_exit, exit_fill, model
    ) / risk

    return float(gross), float(cost), float(gross - cost)
