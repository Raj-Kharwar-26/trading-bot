"""Reasoning runner: turn a symbol into a grounded, strategy-aware analysis.

Pipeline: fetch market OHLCV (OpenBB) -> retrieve RAG strategy context
(Qdrant) -> one LLM completion (OmniRoute /v1) -> parse the JSON report.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

import pandas as pd

from backend.core.config import settings
from backend.data.market import MarketKind, fetch_ohlcv
from backend.rag.retriever import retrieve_context
from backend.reasoning.llm import LLMError, complete
from backend.reasoning.prompts import ANALYSIS_SYSTEM, build_analysis_user_prompt

log = logging.getLogger(__name__)

DEEP_TIMEOUT_SECONDS = 180


class AnalysisError(Exception):
    """Raised when a symbol cannot be analysed."""


def analyse_symbol(
    symbol: str,
    kind: MarketKind,
    *,
    days: int = 30,
    model: str | None = None,
    deep: bool = False,
) -> dict[str, Any]:
    """Return a structured analysis report for one symbol.

    By default runs the lean (single-LLM) path. When ``deep`` is True, runs
    the TradingAgents multi-agent deep analysis (injected with our RAG strategy
    context); on any failure or timeout it transparently falls back to the
    lean path so a valid report is always returned.
    """
    context = _retrieve(symbol, kind, days)
    rag_hits = len(context.split("\n\n")) if context else 0

    if deep:
        report = _run_deep(symbol, kind, days, context)
        if report is not None:
            report.setdefault("_meta", {})["rag_hits"] = rag_hits
            report = _attach_backtest(report, symbol, kind, days)
            _persist_decision(report)
            return report
        log.warning("deep analysis failed for %s; falling back to lean path", symbol)

    data = _fetch_ohlcv_summary(symbol, kind, days)

    user_prompt = build_analysis_user_prompt(
        symbol=symbol,
        market=kind,
        ohlcv_summary=data,
        strategy_context=context,
    )

    try:
        raw = complete(user_prompt, system_prompt=ANALYSIS_SYSTEM, model=model)
    except LLMError as exc:
        raise AnalysisError(f"reasoning failed for {symbol}: {exc}") from exc

    try:
        report = _parse_report(raw)
    except AnalysisError:
        # The primary model returned non-JSON (common with free models that dump
        # reasoning text). Retry once with the configured fallback model, which is
        # a different family and usually honours the JSON-only instruction.
        log.warning(
            "primary model returned non-JSON for %s; retrying with fallback model", symbol
        )
        raw = complete(
            user_prompt,
            system_prompt=ANALYSIS_SYSTEM,
            model=settings.reasoning_model_fallback,
            fallback=False,
        )
        report = _parse_report(raw)

    report["_meta"] = {
        "symbol": symbol,
        "market": kind,
        "rag_hits": rag_hits,
        "engine": "lean",
    }
    report = _attach_backtest(report, symbol, kind, days)
    _persist_decision(report)
    return report


def _persist_decision(report: dict) -> None:
    """Best-effort write of the final report to the Postgres decision log."""
    try:
        from backend.storage.decision_log import log_decision

        decision_id = log_decision(report)
        if decision_id:
            log.info("decisions_log optional row id=%s", decision_id)
    except Exception as exc:  # noqa: BLE001 - never break the analysis over persistence
        log.warning("decisions_log optional persist failed (non-fatal): %s", exc)


def _attach_backtest(report: dict, symbol: str, kind: MarketKind, days: int) -> dict:
    """Attach a ``backtest`` block from the report's own setup, if it has one."""
    if not settings.backtest_enabled:
        report["backtest"] = {"status": "disabled"}
        return report

    try:
        from backend.backtest.runner import build_backtest_from_report

        report["backtest"] = build_backtest_from_report(symbol, kind, report, days=days)
    except Exception as exc:  # noqa: BLE001 - never let backtest break the report
        log.warning("backtest failed for %s: %s", symbol, exc)
        report["backtest"] = {"status": "error", "message": str(exc)}
        return report

    _surface_backtest_caveat(report)
    return report


def _surface_backtest_caveat(report: dict) -> None:
    """Append a caveat when the backtest says the proposed setup is weak.

    The backtest is informative, not binding: it never rejects a trade by
    itself (the hard risk gate does that at execution). But surfacing a
    negative-expectancy / under-sampled setup in caveats keeps "report
    probabilities" honest.
    """
    bt = report.get("backtest")
    if not isinstance(bt, dict) or bt.get("status") != "ok":
        return
    res = bt.get("result") or {}
    caveats = report.setdefault("caveats", [])
    n = res.get("n_trades", 0)
    avg_r = res.get("avg_r_per_trade")

    if not res.get("structured"):
        caveats.append(
            f"Backtest only replayed {n} trade(s) — too few to trust win-rate; "
            "treat probabilities as indicative."
        )
        return
    if avg_r is not None and avg_r < 0:
        caveats.append(
            f"Backtest expectancy is negative ({avg_r:+.2f}R/trade over {n} trades); "
            "this setup has historically been unprofitable under these rules."
        )


def _run_deep(
    symbol: str,
    kind: MarketKind,
    days: int,
    strategy_context: str,
) -> dict[str, Any] | None:
    """Run TradingAgents in a thread with a timeout; return mapped report or None."""
    from backend.reasoning.tradingagents_engine import TradingAgentsError, run_tradingagents
    from backend.reasoning.tradingagents_mapper import map_tradingagents_output

    def _work():
        return run_tradingagents(symbol, kind, days=days, strategy_context=strategy_context)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_work)
            try:
                final_state, rating = future.result(timeout=DEEP_TIMEOUT_SECONDS)
            except FutureTimeout:
                log.warning("deep analysis timed out after %ss for %s", DEEP_TIMEOUT_SECONDS, symbol)
                return None
    except TradingAgentsError as exc:
        log.warning("deep analysis error for %s: %s", symbol, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("deep analysis unexpected error for %s: %s", symbol, exc)
        return None

    return map_tradingagents_output(final_state, rating, symbol, kind)


def _fetch_ohlcv_summary(symbol: str, kind: MarketKind, days: int) -> str:
    end = pd.Timestamp.now().strftime("%Y-%m-%d")
    start = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        df = fetch_ohlcv(symbol, kind, start_date=start, end_date=end)
    except Exception as exc:  # noqa: BLE001
        raise AnalysisError(f"market data error for {symbol}: {exc}") from exc

    recent = df.tail(15)

    # --- Indicators the strategy rules depend on (EMA-50 slope, 20d volume, ATR) ---
    close = df["close"]
    volume = df["volume"]
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema50_prev = ema50.shift(1)
    ema50_slope_up = bool(ema50.iloc[-1] > ema50.iloc[-2])
    ema50_slope = (ema50.iloc[-1] / ema50.iloc[-2] - 1) * 100 if ema50.iloc[-2] else 0.0
    vol20_avg = volume.tail(20).mean()
    vol_ratio = (volume.iloc[-1] / vol20_avg) if vol20_avg else 0.0
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - close.shift(1)).abs(),
            (df["low"] - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = tr.rolling(14).mean().iloc[-1]

    lines = [
        f"latest close: {close.iloc[-1]:g}",
        f"range (last {days}d): {df['low'].min():g} .. {df['high'].max():g}",
        f"avg daily volume (last 20d): {vol20_avg:.0f}",
        f"EMA-50: {ema50.iloc[-1]:g} (prior {ema50.iloc[-2]:g}) -> slope {'up' if ema50_slope_up else 'down'} ({ema50_slope:+.2f}%)",
        f"latest volume vs 20d avg: {volume.iloc[-1]:.0f} ({vol_ratio:.2f}x)",
        f"ATR(14): {atr14:g}",
    ]
    lines.append("last 15 closes:")
    for ts, row in recent.iterrows():
        chg = ""
        if row.get("close") is not None and df["close"].shift(1).get(ts) not in (None,):
            prev = df["close"].shift(1).loc[ts]
            if prev:
                chg = f" ({((row['close'] - prev) / prev) * 100:+.2f}%)"
        lines.append(f"  {ts:%Y-%m-%d} {row['close']:g}{chg}")
    return "\n".join(lines)


def _retrieve(symbol: str, kind: MarketKind, days: int) -> str:
    query = f"{symbol} {kind} trade setup, entry, stop-loss, risk management, strategy rules"
    try:
        hits = retrieve_context(query)
    except Exception as exc:  # noqa: BLE001
        log.warning("RAG retrieval failed for %s: %s", symbol, exc)
        return ""
    if not hits:
        return ""
    blocks = []
    for hit in hits:
        blocks.append(f"[{hit['source']} #{hit['chunk_index']}]\n{hit['text']}")
    return "\n\n".join(blocks)


def _parse_report(raw: str) -> dict[str, Any]:
    """Parse the model's JSON report, tolerating messy/partial JSON."""
    text = (raw or "").strip()
    if not text:
        raise AnalysisError("Model reasoning returned empty output.")

    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()

    # 1) Straight parse.
    try:
        report = json.loads(text)
        return _ensure_object(report, raw)
    except Exception:
        pass

    # 2) Repair pass (trailing commas, single quotes, unquoted keys...).
    try:
        from json_repair import loads as json_repair_loads, repair_json

        report = json_repair_loads(text)
        if isinstance(report, dict):
            return report
        repaired = repair_json(text)
        if repaired:
            report = json.loads(repaired)
            if isinstance(report, dict):
                return report
    except Exception:  # noqa: BLE001
        pass

    # 3) Extract the first balanced brace object and repair that.
    try:
        from json_repair import loads as json_repair_loads

        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            report = json_repair_loads(match.group(0))
            if isinstance(report, dict):
                return report
    except Exception:  # noqa: BLE001
        pass

    raise AnalysisError(f"reasoning output was not JSON:\n{raw[:500]}")


def _ensure_object(report: Any, raw: str) -> dict[str, Any]:
    if isinstance(report, dict):
        return report
    raise AnalysisError(f"reasoning output was not a JSON object:\n{raw[:300]}")
