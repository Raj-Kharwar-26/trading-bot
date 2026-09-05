"""Unit tests for the risk gate.

These prove the hard limits actually reject violating trades, and that
in-limit trades are approved. Run with: pytest backend/tests/test_risk_gate.py
"""

from __future__ import annotations

import pytest

from backend.core.config import Settings
from backend.risk.gate import (
    TradeProposal,
    Verdict,
    evaluate_trade,
)


@pytest.fixture
def cfg() -> Settings:
    return Settings(
        max_loss_per_trade=0.01,
        max_daily_loss=0.03,
        min_rr_ratio=1.5,
        max_position_pct=0.05,
        max_concurrent_positions=10,
        max_total_exposure=0.30,
    )


def _proposal(**overrides) -> TradeProposal:
    base = dict(
        symbol="BTCUSDT",
        side="BUY",
        entry=100.0,
        stop_loss=98.0,     # risk 2.0
        target=103.0,       # reward 3.0 -> R/R 1.5 (ok)
        risk_amount=100.0,  # 1% of 10000 equity
        equity=10000.0,
        position_value=400.0,  # 4% of equity (within 5% max)
        open_positions=0,
        total_exposure=0.0,
    )
    base.update(overrides)
    return TradeProposal(**base)


def test_approves_valid_trade(cfg):
    p = _proposal()
    v = evaluate_trade(p, cfg)
    assert v.approved is True


def test_rejects_low_rr(cfg):
    # R/R 1.0 < min 1.5
    p = _proposal(entry=100.0, stop_loss=98.0, target=102.0)
    v = evaluate_trade(p, cfg)
    assert v.verdict == Verdict.REJECT
    assert any("R/R" in r for r in v.reasons)


def test_rejects_over_max_loss_per_trade(cfg):
    # risk 3% > max 1%
    p = _proposal(risk_amount=300.0)
    v = evaluate_trade(p, cfg)
    assert v.verdict == Verdict.REJECT
    assert any("exceeds max loss" in r for r in v.reasons)


def test_rejects_over_max_position(cfg):
    # position 40% equity > max 5%
    p = _proposal(position_value=4000.0)
    v = evaluate_trade(p, cfg)
    assert v.verdict == Verdict.REJECT
    assert any("exceeds" in r and "xposure" not in r.lower() for r in v.reasons)
    # must be the position-size reason, not the exposure reason
    assert "Position" in v.reasons[0] if v.reasons else False


def test_rejects_at_max_concurrent_positions(cfg):
    p = _proposal(open_positions=10)
    v = evaluate_trade(p, cfg)
    assert v.verdict == Verdict.REJECT
    assert any("Open positions" in r for r in v.reasons)


def test_rejects_exposure_breach(cfg):
    # current exposure 25% + new 25% tilts past 45% > 30%
    p = _proposal(total_exposure=0.25, position_value=2500.0)
    v = evaluate_trade(p, cfg)
    assert v.verdict == Verdict.REJECT
    assert any("exposure" in r.lower() for r in v.reasons)


def test_rejects_zero_equity(cfg):
    p = _proposal(equity=0.0)
    v = evaluate_trade(p, cfg)
    assert v.verdict == Verdict.REJECT


def test_flat_stop_rejected(cfg):
    # SL == entry -> invalid R/R
    p = _proposal(entry=100.0, stop_loss=100.0, target=105.0)
    v = evaluate_trade(p, cfg)
    assert v.verdict == Verdict.REJECT
    assert any("Invalid risk/reward" in r for r in v.reasons)
