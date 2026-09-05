"""Map TradingAgents multi-agent output into the lean report JSON shape.

The deep path returns a rich ``final_state``; we project it onto the same
compact report schema the single-LLM path produces so downstream consumers
(FastAPI response, risk gate, Telegram) stay unchanged.
"""

from __future__ import annotations

import re
from typing import Any

# 5-tier (TA) -> tri-state (ours).
SIGNAL_MAP = {
    "Buy": "LONG",
    "Overweight": "LONG",
    "Hold": "NEUTRAL",
    "Underweight": "SHORT",
    "Sell": "SHORT",
}

# TA sentiment confidence (data-quality) -> rough 0-1 probability proxy.
_CONFIDENCE_MAP = {"high": 0.8, "medium": 0.6, "low": 0.35}

_NUM_RE = re.compile(r"[-+]?\d*\.?\d+")
# Field header: "**Name**: value" or "**Name:** value" (some renders put the
# colon right after the closing asterisks). Use a lazy ``.+?`` for the name and
# strip a trailing colon in code, so the name never swallows a preceding colon.
_FIELD_RE = re.compile(r"\*\*(.+?)\*\*\s*:?\s*(.*)")


def _field(text: str, name: str) -> str | None:
    """Extract the value of a ``**Name**: value`` field from rendered markdown."""
    if not text:
        return None
    name_lower = name.lower()
    for line in text.splitlines():
        line = line.strip()
        m = _FIELD_RE.match(line)
        if not m:
            continue
        # Normalize the extracted name (may carry a trailing ':' from the
        # "**Name:** value" rendering) before comparing case-insensitively.
        field_name = m.group(1).strip().rstrip(":：").strip().lower()
        if field_name == name_lower:
            val = m.group(2).strip()
            return val or None
    return None


def _first_number(text: str | None) -> float | None:
    """Return the first number found in a string, or None."""
    if not text:
        return None
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _sentiment_confidence(final_state: dict[str, Any]) -> float:
    """Best-effort 0-1 confidence from the sentiment report's confidence tag."""
    report = final_state.get("sentiment_report") or ""
    conf = _field(report, "Confidence")
    if conf:
        key = conf.strip().lower()
        if key in _CONFIDENCE_MAP:
            return _CONFIDENCE_MAP[key]
        if "high" in key:
            return _CONFIDENCE_MAP["high"]
        if "medium" in key or "moderate" in key:
            return _CONFIDENCE_MAP["medium"]
    # Fall back to the debate/PM rating decisiveness.
    return _CONFIDENCE_MAP["low"]


def map_tradingagents_output(final_state: dict[str, Any], rating: str, symbol: str, kind: str) -> dict[str, Any]:
    """Project TradingAgents ``final_state`` into our report JSON."""
    pm_decision = final_state.get("final_trade_decision") or ""
    trader_plan = final_state.get("trader_investment_plan") or ""
    research_plan = final_state.get("investment_plan") or ""

    # Normalise the 5-tier rating to tri-state.
    normalized = rating.strip().capitalize()
    signal = SIGNAL_MAP.get(normalized, "NEUTRAL")

    # Trader levels (entry + stop) and PM target.
    entry = _first_number(_field(trader_plan, "Entry Price")) or _first_number(
        _field(research_plan, "Entry")
    )
    stop = _first_number(_field(trader_plan, "Stop Loss"))
    target = _first_number(_field(pm_decision, "Price Target"))

    risk_reward: float | None = None
    if entry is not None and stop is not None and target is not None:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk > 0:
            risk_reward = round(reward / risk, 2)

    entry_zone: str | None = None
    if entry is not None:
        entry_zone = f"~{entry:g}"

    # PM executive summary as the terse summary.
    summary = _field(pm_decision, "Executive Summary")
    if not summary:
        summary = _field(pm_decision, "Investment Thesis")
    if not summary:
        summary = f"Multi-agent {kind} analysis of {symbol}: {signal}."
    summary = summary.strip() or f"Multi-agent {kind} analysis of {symbol}: {signal}."

    # Key levels assembled from the identified entry/stop/target.
    key_levels: list[str] = []
    if entry is not None:
        key_levels.append(f"entry ~{entry:g}")
    if stop is not None:
        key_levels.append(f"stop ~{stop:g}")
    if target is not None:
        key_levels.append(f"target ~{target:g}")
    if not key_levels:
        key_levels = ["no explicit levels defined"]

    # Caveats: PM/risk debate residual + any unknown-confidence note.
    caveats: list[str] = []
    if risk_reward is None:
        caveats.append("risk/reward not computable (missing entry/stop/target)")
    thesis = _field(pm_decision, "Investment Thesis")
    if thesis:
        caveats.append(thesis.strip())
    if not caveats:
        caveats = ["deep analysis via multi-agent debate"]

    return {
        "signal": signal,
        "confidence": round(_sentiment_confidence(final_state), 2),
        "summary": summary,
        "entry_zone": entry_zone,
        "stop_loss": f"{stop:g}" if stop is not None else None,
        "target": f"{target:g}" if target is not None else None,
        "risk_reward": risk_reward,
        "key_levels": key_levels,
        "strategy_applied": "tradingagents (multi-agent deep analysis)",
        "caveats": caveats,
        "_meta": {
            "symbol": symbol,
            "market": kind,
            "rag_hits": 0,
            "engine": "tradingagents",
            "ta_rating": normalized,
            "ta_debate_verdict": _field(research_plan, "Recommendation") or None,
        },
    }
