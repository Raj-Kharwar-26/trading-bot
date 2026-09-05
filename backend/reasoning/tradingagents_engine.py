"""Deep multi-agent reasoning via TradingAgents, routed through OmniRoute.

Wraps the vendored ``TradingAgentsGraph`` at
``vendor/tradingagents`` so its multi-agent pipeline can be run on-demand
behind the same FastAPI + RAG + Telegram layers as the lean path.

Config is built from our typed settings and injected into the framework's
``DEFAULT_CONFIG`` (the framework is fully env/config overridable). The RAG
strategy context we retrieve is threaded into the graph's Portfolio Manager
prompt via the ``strategy_context`` attribute the vendored ``_run_graph``
appends to ``past_context``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.core.config import settings

log = logging.getLogger(__name__)


class TradingAgentsError(Exception):
    """Raised when the deep TradingAgents pipeline cannot complete."""


def _build_config() -> dict[str, Any]:
    """Build a TradingAgents config dict from our typed settings.

    Uses OmniRoute as an OpenAI-compatible keyless backend so no API key is
    required (matches the lean path). Artifacts are written under
    ``tradingagents_home`` to avoid polluting the user's home directory.
    """
    home = Path(settings.tradingagents_home).expanduser().resolve()
    cfg = {
        # LLM provider + endpoint (keyless OpenAI-compatible gateway).
        "llm_provider": settings.tradingagents_provider,
        "backend_url": settings.omniroute_base_url,
        "deep_think_llm": settings.tradingagents_deep_model,
        "quick_think_llm": settings.tradingagents_quick_model,
        "temperature": settings.tradingagents_temperature,
        "llm_max_retries": settings.tradingagents_max_retries,
        # Keep the multi-agent graph shallow: single debate + risk rounds.
        "max_debate_rounds": settings.tradingagents_max_debate_rounds,
        "max_risk_discuss_rounds": settings.tradingagents_max_risk_rounds,
        "max_recur_limit": settings.tradingagents_max_recur_limit,
        "output_language": "English",
        "checkpoint_enabled": False,
        # Scratch locations so deep runs don't touch the user's home.
        "project_dir": str(home),
        "results_dir": str(home / "logs"),
        "data_cache_dir": str(home / "cache"),
        "memory_log_path": str(home / "memory" / "trading_memory.md"),
    }
    return cfg


def run_tradingagents(
    symbol: str,
    kind: str,
    days: int = 30,
    strategy_context: str = "",
) -> tuple[dict[str, Any], str]:
    """Run the TradingAgents graph for a symbol and return (final_state, rating).

    ``final_state`` holds ``final_trade_decision`` (PM markdown), the analyst
    reports, the trader/PM plans, and the debate/risk history. ``rating`` is
    the 5-tier signal (Buy/Overweight/Hold/Underweight/Sell).

    A generous per-node recursion cap keeps runaway tool loops from hanging;
    the caller wraps this in a timeout and falls back to the lean path.
    """
    try:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph
    except Exception as exc:  # noqa: BLE001
        raise TradingAgentsError(f"tradingagents import failed: {exc}") from exc

    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_build_config())

    try:
        graph = TradingAgentsGraph(config=cfg, debug=False)
    except Exception as exc:  # noqa: BLE001
        raise TradingAgentsError(f"tradingagents graph init failed: {exc}") from exc

    # Inject our RAG strategy context into the PM prompt via the vendored hook.
    graph.strategy_context = strategy_context

    trade_date = _trade_date()
    try:
        final_state, parsed_signal = graph.propagate(
            symbol, trade_date=trade_date, asset_type=_asset_type(kind)
        )
    except Exception as exc:  # noqa: BLE001
        raise TradingAgentsError(f"tradingagents run failed for {symbol}: {exc}") from exc

    # ``parsed_signal`` is the deterministic 5-tier rating (Buy/Overweight/Hold/
    # Underweight/Sell) extracted from the PM decision. Prefer it over parsing
    # the raw markdown ourselves.
    rating = parsed_signal or (final_state.get("final_trade_decision") or "")
    return final_state, rating


def _asset_type(kind: str) -> str:
    """Map our market kind to TradingAgents' asset_type."""
    return "crypto" if kind == "CRYPTO" else "stock"


def _trade_date() -> str:
    """Today's date as YYYY-MM-DD, used as the graph's trade date."""
    import datetime as _dt

    return _dt.date.today().isoformat()
