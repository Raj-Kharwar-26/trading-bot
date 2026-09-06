"""Trade plan builder: AI analysis + mechanical risk framing + backtest validation.

Every ``build_trade_plan`` call produces a complete, actionable plan:
direction + entry + stop + targets + position sizing + backtest-validated grade.

The setup is built *hybrid*:
  1. The AI supplies bias, confidence, summary, key_levels, and *optional*
     entry / stop_loss / target.
  2. Any missing price level is filled mechanically from ATR, guaranteeing
     the backtest always has a complete setup to replay.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from backend.core.config import settings
from backend.data.market import MarketKind, MarketDataError, fetch_ohlcv
from backend.reasoning.llm import LLMError, complete
from backend.reasoning.prompts import ANALYSIS_SYSTEM, build_analysis_user_prompt

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OHLCV summary for the LLM (interval-aware, mirrors _fetch_ohlcv_summary)
# ---------------------------------------------------------------------------

def _ohlcv_summary_for_plan(df: pd.DataFrame, days: int, interval: str) -> str:
    """Build a concise OHLCV + indicator summary for the LLM prompt."""
    recent = df.tail(15)
    close = df["close"]
    volume = df["volume"]

    # EMA-50 (or all data if fewer than 50 bars)
    ema_span = min(50, len(close) - 1) if len(close) > 1 else 1
    ema = close.ewm(span=ema_span, adjust=False).mean()
    ema_slope_up = bool(ema.iloc[-1] > ema.iloc[-2]) if len(ema) > 1 else True
    ema_slope = ((ema.iloc[-1] / ema.iloc[-2] - 1) * 100) if (len(ema) > 1 and ema.iloc[-2]) else 0.0

    vol20_avg = volume.tail(min(20, len(volume))).mean()
    vol_ratio = (volume.iloc[-1] / vol20_avg) if vol20_avg else 0.0

    # ATR
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - close.shift(1)).abs(),
            (df["low"] - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_period = min(settings.atr_period, len(tr) - 1) if len(tr) > 1 else 1
    atr_val = tr.tail(atr_period).mean()

    lines = [
        f"style: {interval} bars, last {days} period(s)",
        f"latest close: {close.iloc[-1]:g}",
        f"range (last window): {df['low'].min():g} .. {df['high'].max():g}",
        f"avg volume (last {min(20, len(volume))} bars): {vol20_avg:.0f}",
        f"EMA-{ema_span}: {ema.iloc[-1]:g} (prior {ema.iloc[-2]:g}) -> slope {'up' if ema_slope_up else 'down'} ({ema_slope:+.2f}%)",
        f"latest volume vs avg: {volume.iloc[-1]:.0f} ({vol_ratio:.2f}x)",
        f"ATR({atr_period}): {atr_val:g}",
    ]
    lines.append("last 15 closes:")
    for ts, row in recent.iterrows():
        chg = ""
        if row.get("close") is not None:
            try:
                prev = close.shift(1).loc[ts]
                if prev:
                    chg = f" ({((row['close'] - prev) / prev) * 100:+.2f}%)"
            except KeyError:
                pass
        lines.append(f"  {ts} {row['close']:g}{chg}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

def compute_atr(df: pd.DataFrame, period: int | None = None) -> float:
    """Average True Range from OHLCV DataFrame."""
    p = period or settings.atr_period
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    if len(close) < 2:
        return 0.0
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    p = min(p, len(tr))
    return float(np.mean(tr[-p:])) if p > 0 else 0.0


# ---------------------------------------------------------------------------
# Hybrid setup builder (AI + mechanical fallback)
# ---------------------------------------------------------------------------

def _as_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _resolve_entry(report: dict, last_close: float) -> tuple[float, str]:
    """Return (entry_price, source) — 'ai' or 'mechanical'."""
    ai_entry = _as_float(report.get("entry_zone") or report.get("entry"))
    if ai_entry is not None and ai_entry > 0:
        # Accept AI entry if within 5% of last close (sanity band)
        if abs(ai_entry - last_close) / last_close < 0.05:
            return round(ai_entry, 6), "ai"
    return round(last_close, 6), "mechanical"


def _resolve_stop(report: dict, entry: float, direction: str, atr: float) -> tuple[float, str]:
    """Return (stop_price, source) — 'ai' or 'mechanical'."""
    ai_stop = _as_float(report.get("stop_loss"))
    if ai_stop is not None and ai_stop > 0:
        # Validate: stop must be on the correct side of entry
        if direction == "LONG" and ai_stop < entry:
            return round(ai_stop, 6), "ai"
        if direction == "SHORT" and ai_stop > entry:
            return round(ai_stop, 6), "ai"
    # Mechanical: entry +/- atr_multiplier * ATR
    stop = entry - settings.atr_multiplier * atr if direction == "LONG" else entry + settings.atr_multiplier * atr
    return round(stop, 6), "mechanical"


def _build_targets(direction: str, entry: float, stop: float) -> dict[str, float]:
    """Generate T1/T2/T3 at 1R/2R/3R from entry."""
    risk = abs(entry - stop)
    if risk <= 0:
        return {}
    targets: dict[str, float] = {}
    for i, rr in enumerate([1, 2, 3], 1):
        if direction == "LONG":
            targets[f"T{i}"] = round(entry + rr * risk, 6)
        else:
            targets[f"T{i}"] = round(entry - rr * risk, 6)
    return targets


# ---------------------------------------------------------------------------
# Backtest (reuses the existing simulator)
# ---------------------------------------------------------------------------

def _run_plan_backtest(
    symbol: str,
    market: str,
    direction: str,
    entry: float,
    stop: float,
    target: float,
) -> dict[str, Any]:
    """Run the backtest with the plan's complete setup. Always returns a block."""
    from backend.backtest.runner import build_backtest_from_report

    if direction not in ("LONG", "SHORT"):
        return {"status": "skipped", "message": "No directional signal (NEUTRAL)"}

    if entry is None or stop is None or target is None:
        return {"status": "skipped", "message": "Incomplete setup — cannot backtest"}

    # Build a synthetic report that build_backtest_from_report can consume
    synthetic_report: dict[str, Any] = {
        "signal": direction,
        "entry_zone": entry,
        "entry": entry,
        "stop_loss": stop,
        "target": target,
    }
    try:
        return build_backtest_from_report(
            symbol, market, synthetic_report, days=settings.backtest_window_days
        )
    except Exception as exc:
        log.warning("plan backtest failed for %s: %s", symbol, exc)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def _compute_sizing(
    symbol: str,
    market: str,
    entry: float,
    stop: float,
) -> dict[str, Any]:
    """Compute position size: qty, capital at risk, order value.

    Uses ``max_loss_per_trade`` (default 1% of paper equity) as the risk
    budget, and rounds to the exchange's lot precision where possible.
    """
    from backend.execution.state import get_portfolio_state

    state = get_portfolio_state()
    equity = float(state.engine.cash)
    # Add unrealized P&L from open positions (use entry_price as approximation)
    for sym, pos in state.engine.positions.items():
        if pos.qty != 0:
            equity += pos.qty * pos.avg_entry_price

    risk_budget = equity * settings.max_loss_per_trade
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return {"qty": 0, "risk_budget": risk_budget, "note": "degenerate setup"}

    qty = risk_budget / risk_per_unit

    # Clamp to the hard per-symbol exposure cap (max_position_pct of equity),
    # matching the risk gate, so the suggested size is always executable.
    if settings.max_position_pct > 0 and entry > 0:
        cap_qty = equity * settings.max_position_pct / entry
        qty = min(qty, cap_qty)

    # Round to exchange precision
    if market == "CRYPTO":
        try:
            from backend.data.binance_prices import BINANCE_PUBLIC_BASE_URL, _exchange_info_cache

            prec = _exchange_info_cache.precision_for(symbol, BINANCE_PUBLIC_BASE_URL)
            qty = round(qty, prec.get("qty_decimals", 6))
        except Exception:
            qty = round(qty, 6)
    else:
        qty = max(1, int(qty))  # NSE: integer shares

    order_value = qty * entry
    return {
        "qty": qty,
        "equity": round(equity, 2),
        "risk_budget": round(risk_budget, 2),
        "risk_per_unit": round(risk_per_unit, 6),
        "order_value": round(order_value, 2),
        "pct_of_equity": round(order_value / equity * 100, 2) if equity else 0,
    }


# ---------------------------------------------------------------------------
# Grade
# ---------------------------------------------------------------------------

def _compute_grade(backtest: dict) -> str:
    """Simple PASS / CAUTION / AVOID based on backtest stats."""
    if backtest.get("status") != "ok":
        return "N/A"
    r = backtest.get("result") or {}
    avg_r = r.get("avg_r_per_trade")
    win_rate = r.get("win_rate")
    n = r.get("n_trades", 0)
    structured = r.get("structured", False)

    if not structured:
        return "N/A (insufficient trades)"

    if avg_r is not None and avg_r > 0 and win_rate is not None and win_rate >= 0.35:
        return "PASS"
    if avg_r is not None and avg_r < -0.5:
        return "AVOID"
    return "CAUTION"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_trade_plan(
    symbol: str,
    market: str,
    style: str = "swing",
) -> dict[str, Any]:
    """Build a complete, backtest-validated trade plan.

    ``style`` is ``"swing"`` (daily bars) or ``"intraday"`` (intra-day bars).
    Returns a dict with all plan fields ready for formatting.
    """
    from backend.reasoning.agent_runner import _retrieve

    # ---- 1. Determine interval + days ----
    if style == "intraday":
        if market == "CRYPTO":
            interval = settings.intraday_interval_crypto
            days = settings.intraday_window_days_crypto
        else:
            interval = settings.intraday_interval_nse
            days = settings.intraday_window_days_nse
    else:
        interval = "1d"
        days = settings.backtest_window_days

    # ---- 2. Fetch OHLCV ----
    end = pd.Timestamp.now().strftime("%Y-%m-%d")
    start = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        df = fetch_ohlcv(symbol, market, start_date=start, end_date=end, interval=interval)
    except MarketDataError as exc:
        return {"error": str(exc)}

    last_close = float(df["close"].iloc[-1])
    atr = compute_atr(df)

    # ---- 3. RAG context ----
    from backend.reasoning.agent_runner import _retrieve
    context = _retrieve(symbol, market, days)
    rag_hits = len(context.split("\n\n")) if context else 0

    # ---- 4. LLM analysis ----
    ohlcv_summary = _ohlcv_summary_for_plan(df, days, interval)
    user_prompt = build_analysis_user_prompt(symbol, market, ohlcv_summary, context)
    try:
        raw = complete(user_prompt, system_prompt=ANALYSIS_SYSTEM)
    except LLMError as exc:
        return {"error": f"LLM failed: {exc}"}

    from backend.reasoning.agent_runner import _parse_report
    try:
        report = _parse_report(raw)
    except Exception:
        report = {"signal": "NEUTRAL", "summary": "LLM parse failed; using mechanical fallback.", "confidence": 0.3}

    # ---- 5. Direction ----
    direction = report.get("signal", "NEUTRAL")
    if direction not in ("LONG", "SHORT"):
        plan = {
            "direction": "NEUTRAL",
            "confidence": report.get("confidence", 0),
            "summary": report.get("summary", "No clear directional signal."),
            "last_close": last_close,
            "atr": atr,
            "caveats": ["No trade — signal is NEUTRAL. Monitor for a setup."],
            "grade": "N/A",
            "_meta": {
                "symbol": symbol,
                "market": market,
                "rag_hits": rag_hits,
                "engine": "plan",
                "style": style,
                "interval": interval,
                "days": days,
            },
        }
        _persist_plan(plan)
        return plan

    # ---- 6. Hybrid setup (AI + mechanical fallback) ----
    entry, entry_src = _resolve_entry(report, last_close)
    stop, stop_src = _resolve_stop(report, entry, direction, atr)
    targets = _build_targets(direction, entry, stop)

    if not targets:
        return {"error": "Could not build targets (degenerate setup)"}

    risk_reward = 2.0  # primary target is T2 (2R)

    # ---- 7. Backtest (runner builds its own cost model from the market) ----
    backtest = _run_plan_backtest(symbol, market, direction, entry, stop, targets["T2"])

    # ---- 8. Sizing ----
    sizing = _compute_sizing(symbol, market, entry, stop)

    # ---- 9. Caveats ----
    caveats = list(report.get("caveats", []))
    if entry_src == "mechanical":
        caveats.append("Entry is mechanical (AI entry was missing/invalid).")
    if stop_src == "mechanical":
        caveats.append("Stop is ATR-based (AI stop was missing/invalid).")

    if backtest.get("status") == "ok":
        bt = backtest["result"]
        if bt.get("avg_r_per_trade") is not None and bt["avg_r_per_trade"] < 0:
            caveats.append(
                f"Backtest expectancy is negative ({bt['avg_r_per_trade']:+.2f}R over {bt['n_trades']} trades)."
            )
        if not bt.get("structured"):
            caveats.append(f"Only {bt.get('n_trades', 0)} backtest trade(s) — stats are indicative, not reliable.")

    if style == "intraday" and market != "CRYPTO":
        caveats.append("NSE intraday data is best-effort (yfinance); use swing for high confidence.")

    # ---- 10. Grade ----
    grade = _compute_grade(backtest)

    plan = {
        "direction": direction,
        "confidence": report.get("confidence", 0),
        "summary": report.get("summary", ""),
        "key_levels": report.get("key_levels", []),
        "strategy_applied": report.get("strategy_applied", "none"),
        "last_close": last_close,
        "atr": atr,
        "entry": entry,
        "entry_source": entry_src,
        "stop_loss": stop,
        "stop_source": stop_src,
        "targets": targets,
        "risk_reward": risk_reward,
        "backtest": backtest,
        "sizing": sizing,
        "caveats": caveats,
        "grade": grade,
        "_meta": {
            "symbol": symbol,
            "market": market,
            "rag_hits": rag_hits,
            "engine": "plan",
            "style": style,
            "interval": interval,
            "days": days,
        },
    }
    _persist_plan(plan)
    return plan


def _persist_plan(plan: dict[str, Any]) -> None:
    """Best-effort write of a trade plan to the Postgres decision log."""
    try:
        from backend.storage.decision_log import log_decision

        meta = plan.get("_meta") or {}
        report: dict[str, Any] = {
            "signal": plan.get("direction"),
            "confidence": plan.get("confidence"),
            "summary": plan.get("summary", ""),
            "entry_zone": plan.get("entry"),
            "stop_loss": plan.get("stop_loss"),
            "target": (plan.get("targets") or {}).get("T2"),
            "key_levels": plan.get("key_levels", []),
            "strategy_applied": plan.get("strategy_applied"),
            "caveats": plan.get("caveats", []),
            "grade": plan.get("grade"),
            "backtest": plan.get("backtest"),
            "_meta": meta,
        }
        decision_id = log_decision(report)
        if decision_id:
            plan["_meta"]["decision_id"] = decision_id
    except Exception as exc:  # noqa: BLE001 - telemetry must never break the plan
        log.warning("plan persist failed (non-fatal): %s", exc)
