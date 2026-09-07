"""Telegram bot with paper trading commands.

Run in dev/polling mode with:
    python -m backend.telegram.bot

Requires TELEGRAM_BOT_TOKEN and optional ALLOWED_TELEGRAM_USER_IDS in .env.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from backend.core.config import settings
from backend.execution.orchestrator import TaskType, create_orchestrator
from backend.execution.risk_engine import AutoRiskEngine, create_risk_engine
from backend.execution.state import get_portfolio_state

log = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 AI Trading Bot — Paper & Live Trading Commands\n\n"
    "📊 <b>Analysis &amp; Execution</b>\n"
    "<code>/analyze &lt;symbol&gt; [market] [style]</code> — full trade plan (entry/stop/targets/backtest/sizing)\n"
    "   styles: <code>swing</code> (default, daily) | <code>intraday</code> (1h crypto / 15m NSE)\n"
    "<code>/deep &lt;symbol&gt; [market]</code> — TradingAgents deep analysis\n"
    "<code>/paper_trade &lt;symbol&gt; &lt;LONG|SHORT&gt; &lt;qty&gt; &lt;entry_price&gt; [market]</code> — open paper position\n"
    "<code>/paper_close &lt;symbol&gt; &lt;LONG|SHORT&gt; &lt;exit_price&gt; [market]</code> — close paper position\n"
    "<code>/live_trade &lt;symbol&gt; &lt;BUY|SELL&gt; &lt;qty&gt; &lt;price&gt; [market]</code> — place live order (needs approval)\n\n"
    "📈 <b>Portfolio &amp; Positions</b>\n"
    "<code>/positions</code> — show open positions (paper + live)\n"
    "<code>/portfolio [symbol] [market]</code> — portfolio equity &amp; PnL\n"
    "<code>/trades [symbol] [limit]</code> — recent trade history\n\n"
    "⚠️ <b>Risk &amp; Safety</b>\n"
    "<code>/risk</code> — current risk metrics &amp; limits\n"
    "<code>/kill</code> — EMERGENCY HALT all trading\n"
    "<code>/unhalt</code> — resume trading after /kill\n"
    "<code>/approve &lt;order_id&gt;</code> — approve pending live order\n"
    "<code>/reject &lt;order_id&gt;</code> — reject pending live order\n"
    "<code>/pending</code> — show pending approvals\n\n"
    "🔧 <b>System</b>\n"
    "<code>/help</code> — this message\n"
    "<code>/ping</code> — health check\n"
    "<code>/status</code> — bot &amp; system status\n"
    "<code>/mode &lt;paper|live|hybrid&gt;</code> — switch trading mode\n\n"
    "Markets: NSE (default), BSE, CRYPTO\n"
    "Example: <code>/live_trade RELIANCE.NS BUY 100 2500 NSE</code>"
)


def _is_authorized(user_id: int | None) -> bool:
    allowed = settings.allowed_user_ids
    if not allowed:
        return True  # dev default: no restriction
    return user_id in allowed


def _guard(handler):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        uid = update.effective_user.id if update.effective_user else None
        if not _is_authorized(uid):
            await update.message.reply_text("⛔ You are not authorized to use this bot.")
            return
        cleaned = _clean_args(ctx.args)
        if cleaned != ctx.args:
            log.info("sanitized command args %r -> %r", ctx.args, cleaned)
        ctx.args = cleaned
        await handler(update, ctx)

    wrapper.__name__ = getattr(handler, "__name__", "wrapped")
    return wrapper


_ARG_SPLIT_RE = re.compile(r"[\s\u00a0\u200b\u200c\u200d\u2060\ufeff]+")


def _clean_args(args: list[str] | None) -> list[str]:
    """Split command args on any Unicode separator (incl. zero-width space).

    Telegram messages copied from elsewhere can contain ZWSP/NBSP/BOM between
    words; plain ``str.split`` only handles ASCII whitespace, which silently
    glues args together (e.g. ``/paper_trade`` followed by a glued multi-arg
    tail that used to collapse to a single token).
    """
    if not args:
        return []
    return [a for a in _ARG_SPLIT_RE.split(" ".join(args)) if a]


def _parse_market(args: list[str], default_idx: int = 1) -> str:
    """Extract market from args, default NSE."""
    if len(args) > default_idx:
        m = args[default_idx].upper()
        if m in ("NSE", "BSE", "CRYPTO"):
            return m
    return "NSE"


def _crypto_symbols(symbol: str, market: str) -> tuple[str, str]:
    """Map a user symbol to (analysis/execution symbol, display label).

    For CRYPTO, auto-converts any spelling (``UNI-USD``/``UNI``/``UNIUSDT``)
    to Binance spot format (``UNIUSDT``) for data + execution, while returning
    a friendly ``UNI-USD`` label for replies. Non-crypto is passed through
    unchanged.
    """
    if market == "CRYPTO":
        from backend.data.binance_prices import to_binance_symbol, to_display_symbol

        return to_binance_symbol(symbol), to_display_symbol(symbol)
    return symbol.upper(), symbol.upper()


async def _mirror_to_binance_testnet(
    *,
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
) -> str:
    """Mirror a paper/trade to Binance Testnet for a real mock fill.

    Returns an informational suffix to append to the confirmation (or "").
    Never raises into the caller beyond a logged warning.
    """
    try:
        from backend.execution.binance_testnet import BinanceTestnetClient, BinanceConfig
        from backend.execution.paper import OrderSide, OrderType
        from backend.core.config import settings as _s

        cfg = BinanceConfig(
            api_key=_s.binance_testnet_api_key,
            api_secret=_s.binance_testnet_api_secret,
            base_url=_s.binance_testnet_base_url,
        )
        binance_side = OrderSide.BUY if side.upper() == "LONG" else OrderSide.SELL
        client = BinanceTestnetClient(cfg)
        try:
            resp = await client.create_order(
                symbol=symbol,
                side=binance_side,
                order_type=OrderType.LIMIT,
                quantity=qty,
                price=limit_price,
            )
            oid = resp.get("orderId")
            status = resp.get("status")
            return f"\n🧪 Binance Testnet: {status} (id {oid})"
        finally:
            await client.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("testnet mirror failed for %s %s: %s", side, symbol, exc)
        reason = str(exc).strip().replace("\n", " ")
        if len(reason) > 160:
            reason = reason[:160] + "..."
        return f"\n⚠️ Binance Testnet mirror failed: {reason or type(exc).__name__}"


# --- Commands ---

async def _cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def _cmd_ping(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🏓 pong")


async def _cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /analyze <symbol> [market] [swing|intraday]")
        return

    symbol = args[0].upper()
    market = _parse_market(args)  # index 1: NSE/BSE/CRYPTO, default NSE
    style = "swing"
    if len(args) > 2 and args[2].lower() in ("swing", "intraday"):
        style = args[2].lower()

    exec_symbol, display_symbol = _crypto_symbols(symbol, market)
    await update.message.reply_text(f"🔍 Building {display_symbol} ({market}) {style} plan...")

    try:
        from backend.reasoning.planner import build_trade_plan

        plan = build_trade_plan(exec_symbol, market, style=style)
    except Exception as exc:
        await update.message.reply_text(f"❌ Planning failed: {exc}")
        return

    if "error" in plan:
        await update.message.reply_text(f"❌ {plan['error']}")
        return

    lines = _format_plan(plan, exec_symbol, display_symbol, market, style)
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def _format_plan(
    plan: dict,
    exec_symbol: str,
    display_symbol: str,
    market: str,
    style: str,
) -> list[str]:
    """Render a trade plan into HTML reply lines."""
    import html

    esc = html.escape
    header = f"📊 <b>{esc(display_symbol)} ({market} · {style.upper()})</b>"
    conf = plan.get("confidence", 0)
    direction = plan.get("direction", "NEUTRAL")

    lines: list[str] = [header]

    if direction == "NEUTRAL":
        lines.append("Signal: <b>NEUTRAL</b> — no trade")
        if plan.get("last_close") is not None:
            lines.append(f"Last close: {plan['last_close']:g}")
        if plan.get("summary"):
            lines.append(f"Summary: {esc(plan['summary'])}")
        for c in plan.get("caveats", []):
            lines.append(f"⚠️ {esc(c)}")
        return lines

    # Directional plan
    lines.append(f"Signal: <b>{direction}</b> ({conf:.0%} confidence)")
    lines.append(
        f"Entry: <b>{plan['entry']:g}</b> <i>({plan.get('entry_source', 'ai')})</i>"
    )
    lines.append(
        f"Stop: <b>{plan['stop_loss']:g}</b> <i>({plan.get('stop_source', 'mechanical')})</i>"
    )
    targets = plan.get("targets", {})
    t1, t2, t3 = targets.get("T1"), targets.get("T2"), targets.get("T3")
    target_str = (
        f"Targets: {t1:g} / <b>{t2:g}</b> / {t3:g}"
        if all(v is not None for v in (t1, t2, t3))
        else "Targets: n/a"
    )
    lines.append(target_str)
    lines.append(f"R/R: {plan.get('risk_reward', 1.0):.1f}:1 · ATR: {plan.get('atr', 0):g}")

    sizing = plan.get("sizing") or {}
    if sizing.get("qty"):
        lines.append(
            f"Position: <b>{sizing['qty']:g}</b> ≈ {sizing.get('order_value', 0):,.0f} "
            f"({sizing.get('pct_of_equity', 0):.2f}% of {sizing.get('equity', 0):,.0f}) "
            f"· risk {sizing.get('risk_budget', 0):,.0f}"
        )

    bt = plan.get("backtest") or {}
    if bt.get("status") == "ok":
        r = bt["result"]
        pf = r.get("profit_factor")
        maxdd = r.get("max_drawdown_pct")
        maxdd_s = f"{maxdd:.1%}" if maxdd is not None else "N/A"
        lines.append(
            f"Backtest: {r['n_trades']} trades · Win {r['win_rate']:.0%} · "
            f"Avg R {r['avg_r_per_trade']:+.2f} · PF {pf if pf else 'N/A'} · MaxDD {maxdd_s}"
        )
    elif bt.get("status") == "skipped":
        lines.append(f"Backtest: skipped ({esc(bt.get('message', ''))})")

    grade = plan.get("grade", "N/A")
    if grade == "PASS":
        lines.append("Grade: ✅ <b>PASS</b>")
    elif grade == "CAUTION":
        lines.append("Grade: ⚠️ <b>CAUTION</b>")
    elif grade == "AVOID":
        lines.append("Grade: 🛑 <b>AVOID</b>")
    else:
        lines.append(f"Grade: {esc(str(grade))}")

    if plan.get("summary"):
        lines.append(f"Summary: {esc(plan['summary'])}")

    for c in plan.get("caveats", []):
        lines.append(f"⚠️ {esc(c)}")

    if sizing.get("qty"):
        side = "LONG" if direction == "LONG" else "SHORT"
        lines.append(
            f"<code>/paper_trade {exec_symbol} {side} {sizing['qty']:g} {plan['entry']:g} {market}</code>"
        )
    return lines


async def _cmd_deep(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /deep <symbol> [market]")
        return

    symbol = args[0].upper()
    market = _parse_market(args)
    exec_symbol, display_symbol = _crypto_symbols(symbol, market)
    await update.message.reply_text(f"🧠 Deep analysis {display_symbol} ({market})... (this takes ~2-3 min)")

    try:
        from backend.reasoning.agent_runner import analyse_symbol
        report = analyse_symbol(exec_symbol, market, days=30, deep=True)
        signal = report.get("signal", "?")
        conf = report.get("confidence", 0)
        summary = report.get("summary", "")
        await update.message.reply_text(
            f"📊 <b>{display_symbol} ({market}) DEEP</b>\n"
            f"Signal: <b>{signal}</b>  Confidence: {conf:.0%}\n"
            f"Engine: {report.get('_meta', {}).get('engine', '?')}\n"
            f"Summary: {summary}",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Deep analysis failed: {e}")


async def _cmd_paper_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if len(args) < 4:
        await update.message.reply_text(
            "Usage: /paper_trade <symbol> <LONG|SHORT> <qty> <entry_price> [market]"
        )
        return

    symbol = args[0].upper()
    side = args[1].upper()
    if side not in ("LONG", "SHORT"):
        await update.message.reply_text("Side must be LONG or SHORT")
        return

    try:
        qty = float(args[2])
        entry_price = float(args[3])
    except ValueError:
        await update.message.reply_text("qty and entry_price must be numbers")
        return

    market = _parse_market(args, default_idx=4)
    exec_symbol, display_symbol = _crypto_symbols(symbol, market)

    await update.message.reply_text(f"📝 Opening paper {side} {qty} {display_symbol} @ {entry_price} ({market})...")

    try:
        from backend.backtest.costs import cost_model_for_market
        from backend.execution.state import get_portfolio_state

        portfolio = get_portfolio_state()
        cost_model = cost_model_for_market(market, settings)

        result = portfolio.place_and_fill(
            symbol=display_symbol,
            market=market,
            side=side,
            qty=qty,
            entry_price=entry_price,
            cost_model=cost_model,
        )

        if result["success"]:
            fill = result["fill"]
            extra = ""
            if market == "CRYPTO" and settings.binance_testnet_api_key:
                extra = await _mirror_to_binance_testnet(
                    symbol=exec_symbol, side=side, qty=qty, limit_price=entry_price
                )
            await update.message.reply_text(
                f"✅ <b>Paper Trade Opened</b>\n"
                f"{side} {qty} {display_symbol} ({market})\n"
                f"Fill: {fill['price']:.2f} | Fee: {fill['fee']:.2f}\n"
                f"Order ID: {result['order_id']}{extra}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"❌ Failed: {result.get('error', 'Unknown error')}")
    except Exception as e:
        await update.message.reply_text(f"❌ Paper trade failed: {e}")


async def _cmd_paper_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: /paper_close <symbol> <LONG|SHORT> <exit_price> [market]"
        )
        return

    symbol = args[0].upper()
    side = args[1].upper()
    if side not in ("LONG", "SHORT"):
        await update.message.reply_text("Side must be LONG or SHORT")
        return

    try:
        exit_price = float(args[2])
    except ValueError:
        await update.message.reply_text("exit_price must be a number")
        return

    market = _parse_market(args, default_idx=3)

    await update.message.reply_text(f"📝 Closing paper {side} {symbol} @ {exit_price} ({market})...")

    try:
        from backend.backtest.costs import cost_model_for_market
        from backend.execution.state import get_portfolio_state

        portfolio = get_portfolio_state()
        cost_model = cost_model_for_market(market, settings)

        result = portfolio.close_position(
            symbol=symbol,
            market=market,
            side=side,
            exit_price=exit_price,
            cost_model=cost_model,
            exit_reason="MANUAL",
        )

        if result["success"]:
            fill = result["fill"]
            trade = result.get("trade", {})
            await update.message.reply_text(
                f"✅ <b>Paper Trade Closed</b>\n"
                f"{side} {symbol} ({market})\n"
                f"Exit: {fill['price']:.2f} | Fee: {fill['fee']:.2f}\n"
                f"PnL: {trade.get('net_pnl', 'N/A')} | Net R: {trade.get('net_r', 'N/A')}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"❌ Failed: {result.get('error', 'Unknown error')}")
    except Exception as e:
        await update.message.reply_text(f"❌ Paper close failed: {e}")


async def _cmd_positions(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from backend.execution.state import get_portfolio_state
    portfolio = get_portfolio_state()
    positions = portfolio.get_open_positions()

    if not positions:
        await update.message.reply_text("📭 No open positions")
        return

    lines = ["📈 <b>Open Positions</b>"]
    for sym, p in positions.items():
        lines.append(f"{sym}: {p['qty']:.0f} @ {p['avg_entry']:.2f} | Realized PnL: {p['realized']:.2f}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from backend.execution.state import get_portfolio_state
    from backend.data.market import fetch_ohlcv

    args = ctx.args
    symbol = args[0].upper() if args else None
    market = _parse_market(args, default_idx=1) if args else "NSE"

    portfolio = get_portfolio_state()

    prices = {}
    if symbol:
        try:
            from datetime import date, timedelta
            end = date.today()
            start = end - timedelta(days=1)
            df = fetch_ohlcv(symbol, market, str(start), str(end))
            if not df.empty:
                prices[symbol] = float(df["close"].iloc[-1])
        except Exception:
            pass

    snap = portfolio.get_portfolio(prices)

    lines = [
        f"💼 <b>Portfolio Snapshot</b>",
        f"Cash: <b>{snap['cash']:,.2f}</b>",
        f"Total Equity: <b>{snap['total_equity']:,.2f}</b>",
        f"Unrealized PnL: {snap['unrealized_pnl']:+,.2f}",
        f"Realized PnL: {snap['realized_pnl']:+,.2f}",
    ]

    if snap["positions"]:
        lines.append("\nPositions:")
        for sym, p in snap["positions"].items():
            lines.append(f"  {sym}: {p['qty']:.0f} @ {p['avg_entry']:.2f} | Unreal: {p['unrealized']:+.2f}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from backend.execution.trade_log import get_trade_log

    args = ctx.args
    symbol = args[0].upper() if args else None
    limit = int(args[1]) if len(args) > 1 else 10

    log = get_trade_log()
    trades = log.get_recent(limit=limit, symbol=symbol)
    stats = log.stats(symbol=symbol)

    if not trades:
        await update.message.reply_text("📭 No trades found")
        return

    lines = [f"📜 <b>Recent Trades</b> (limit {limit})"]
    if stats.get("n_trades"):
        lines.append(
            f"Stats: {stats['n_trades']} trades | Win {stats.get('win_rate', 0):.0%} | "
            f"Avg Net R: {stats.get('avg_net_r', 0):.2f} | "
            f"Total Net PnL: {stats.get('total_net_pnl', 0):+.2f}"
        )

    for t in trades[:5]:
        pnl_sign = "+" if t.net_pnl >= 0 else ""
        lines.append(
            f"{t.symbol} {t.side} | {t.entry_price:.2f}→{t.exit_price:.2f} | "
            f"Net: {pnl_sign}{t.net_pnl:.0f} ({pnl_sign}{t.net_r:.2f}R) | {t.exit_reason}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_risk(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from backend.execution.risk_engine import AutoRiskEngine, create_risk_engine
    from backend.execution.state import get_portfolio_state
    from backend.data.market import fetch_ohlcv

    portfolio = get_portfolio_state()
    risk = create_risk_engine(portfolio.engine)

    # Get prices for open positions
    prices = {}
    for sym in portfolio.engine.positions:
        if portfolio.engine.positions[sym].qty != 0:
            try:
                from datetime import date, timedelta
                end = date.today()
                start = end - timedelta(days=1)
                df = fetch_ohlcv(sym, "NSE", str(start), str(end))
                if not df.empty:
                    prices[sym] = float(df["close"].iloc[-1])
            except Exception:
                pass

    metrics = risk.get_risk_metrics(prices)

    lines = [
        f"⚠️ <b>Risk Metrics</b>",
        f"Equity: {metrics['equity']:,.2f}",
        f"Daily PnL: {metrics['daily_pnl']:+,.2f} ({metrics['daily_pnl_pct']:.2%})",
        f"Daily Limit: {metrics['daily_loss_limit']:.0%}",
        f"Open Positions: {metrics['open_positions']} / {metrics['max_positions']}",
        f"Exposure: {metrics['total_exposure']:,.2f} ({metrics['exposure_pct']:.1%})",
        f"Exposure Limit: {metrics['exposure_limit']:.0%}",
        f"Halted: {'🔴 YES' if metrics['halted'] else '🟢 NO'}",
    ]
    if metrics.get("halt_reason"):
        lines.append(f"Halt Reason: {metrics['halt_reason']}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_kill(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from backend.execution.risk_engine import AutoRiskEngine, create_risk_engine
    from backend.execution.state import get_portfolio_state

    portfolio = get_portfolio_state()
    risk = create_risk_engine(portfolio.engine)
    risk._halt(trading_halt=True, reason="Manual /kill command")
    await update.message.reply_text("🛑 <b>TRADING HALTED</b> — /kill executed. Use /unhalt to resume.", parse_mode="HTML")


async def _cmd_unhalt(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from backend.execution.risk_engine import AutoRiskEngine, create_risk_engine
    from backend.execution.state import get_portfolio_state

    portfolio = get_portfolio_state()
    risk = create_risk_engine(portfolio.engine)
    risk.reset_halt()
    await update.message.reply_text("✅ Trading resumed — /unhalt executed.")


# --- Live Trading & Approval Commands ---

async def _cmd_live_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not settings.live_trading_enabled:
        await update.message.reply_text("❌ Live trading is disabled. Enable with /mode live")
        return

    args = ctx.args
    if len(args) < 4:
        await update.message.reply_text(
            "Usage: /live_trade <symbol> <BUY|SELL> <qty> <price> [market]"
        )
        return

    symbol = args[0].upper()
    side = args[1].upper()
    if side not in ("BUY", "SELL"):
        await update.message.reply_text("Side must be BUY or SELL")
        return

    try:
        qty = int(args[2])
        price = float(args[3])
    except ValueError:
        await update.message.reply_text("qty must be integer, price must be number")
        return

    market = _parse_market(args, default_idx=4)

    # Determine which live engine to use
    if market == "CRYPTO":
        from backend.execution.binance_live import get_binance_live_engine
        from backend.execution.risk_engine import create_risk_engine
        from backend.execution.state import get_portfolio_state

        portfolio = get_portfolio_state()
        risk = create_risk_engine(portfolio.engine)
        engine = await get_binance_live_engine(risk_engine=risk)

        result = await engine.place_order(
            symbol=symbol,
            side="BUY" if side == "BUY" else "SELL",
            qty=qty,
            order_type="LIMIT",
            limit_price=price,
            require_approval=settings.approval_required,
        )
    else:
        from backend.execution.zerodha_live import get_zerodha_live_engine
        from backend.execution.risk_engine import create_risk_engine
        from backend.execution.state import get_portfolio_state

        portfolio = get_portfolio_state()
        risk = create_risk_engine(portfolio.engine)
        engine = await get_zerodha_live_engine(risk_engine=risk)

        result = await engine.place_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="LIMIT",
            limit_price=price,
            require_approval=settings.approval_required,
        )

    if result.get("status") == "PENDING_APPROVAL":
        await update.message.reply_text(
            f"⏳ <b>Order Pending Approval</b>\n"
            f"Order ID: <code>{result['order_id']}</code>\n"
            f"{side} {qty} {symbol} @ {price} ({market})\n"
            f"Use /approve {result['order_id']} to confirm",
            parse_mode="HTML"
        )
    elif result.get("status") == "REJECTED":
        await update.message.reply_text(f"❌ Order rejected: {result.get('reason', 'Risk check failed')}")
    else:
        await update.message.reply_text(
            f"✅ <b>Live Order Placed</b>\n"
            f"Order ID: <code>{result.get('order_id', 'N/A')}</code>\n"
            f"{side} {qty} {symbol} @ {price} ({market})",
            parse_mode="HTML"
        )


async def _cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /approve <order_id>")
        return

    order_id = args[0]

    # Try Binance first, then Zerodha
    approved = False
    result = None

    # Check Binance pending
    from backend.execution.binance_live import get_binance_live_engine
    from backend.execution.risk_engine import create_risk_engine
    from backend.execution.state import get_portfolio_state

    portfolio = get_portfolio_state()
    risk = create_risk_engine(portfolio.engine)

    # Binance
    binance_engine = await get_binance_live_engine(risk_engine=risk)
    if order_id in binance_engine._pending_approvals:
        order = await binance_engine.approve_order(order_id)
        approved = True
        result = {"order_id": order_id, "status": order.status, "fill": getattr(order, 'avg_fill_price', None)}

    # Zerodha
    if not approved:
        from backend.execution.zerodha_live import get_zerodha_live_engine
        zerodha_engine = await get_zerodha_live_engine(risk_engine=risk)
        if order_id in zerodha_engine._pending_approvals:
            result = await zerodha_engine.approve_order(order_id)
            approved = True

    if not approved:
        await update.message.reply_text(f"❌ Order {order_id} not found in pending approvals")
        return

    if result.get("status") in ("FILLED", "PLACED"):
        await update.message.reply_text(
            f"✅ <b>Order Approved & Executed</b>\n"
            f"Order ID: <code>{order_id}</code>\n"
            f"Status: {result.get('status', 'UNKNOWN')}",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"⏳ <b>Order Approved</b>\n"
            f"Order ID: <code>{order_id}</code>\n"
            f"Status: {result.get('status', 'PLACED')}",
            parse_mode="HTML"
        )


async def _cmd_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /reject <order_id>")
        return

    order_id = args[0]
    rejected = False

    # Try Binance
    from backend.execution.binance_live import get_binance_live_engine
    from backend.execution.risk_engine import create_risk_engine
    from backend.execution.state import get_portfolio_state

    portfolio = get_portfolio_state()
    risk = create_risk_engine(portfolio.engine)

    binance_engine = await get_binance_live_engine(risk_engine=risk)
    if order_id in binance_engine._pending_approvals:
        await binance_engine.reject_order(order_id)
        rejected = True

    # Try Zerodha
    if not rejected:
        from backend.execution.zerodha_live import get_zerodha_live_engine
        zerodha_engine = await get_zerodha_live_engine(risk_engine=risk)
        if order_id in zerodha_engine._pending_approvals:
            await zerodha_engine.reject_order(order_id)
            rejected = True

    if rejected:
        await update.message.reply_text(f"✅ Order <code>{order_id}</code> rejected.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"❌ Order {order_id} not found in pending approvals")


async def _cmd_pending(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from backend.execution.binance_live import get_binance_live_engine
    from backend.execution.zerodha_live import get_zerodha_live_engine
    from backend.execution.risk_engine import create_risk_engine
    from backend.execution.state import get_portfolio_state

    portfolio = get_portfolio_state()
    risk = create_risk_engine(portfolio.engine)

    lines = ["⏳ <b>Pending Approvals</b>"]

    # Binance
    binance_engine = await get_binance_live_engine(risk_engine=risk)
    for oid, pending in binance_engine._pending_approvals.items():
        o = pending["order"]
        lines.append(f"<code>{oid}</code>: {pending['side']} {pending['qty']} {pending['symbol']} @ {pending.get('limit_price', 'MARKET')}")

    # Zerodha
    zerodha_engine = await get_zerodha_live_engine(risk_engine=risk)
    for oid, pending in zerodha_engine._pending_approvals.items():
        lines.append(f"<code>{oid}</code>: {pending['side']} {pending['qty']} {pending['symbol']} @ {pending.get('limit_price', 'MARKET')}")

    if len(lines) == 1:
        lines.append("No pending approvals")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from backend.execution.orchestrator import create_orchestrator
    from backend.storage.db import ping

    db_ok = ping()
    lines = [
        f"🤖 <b>Bot Status</b>",
        f"DB: {'🟢 OK' if db_ok else '🔴 Unreachable'}",
        f"Mode: {settings.live_trading_mode.upper()}",
        f"Paper Trading: {'Enabled' if settings.paper_enabled else 'Disabled'}",
        f"Live Trading: {'Enabled' if settings.live_trading_enabled else 'Disabled'}",
        f"Approval Required: {'Yes' if settings.approval_required else 'No'}",
        f"Initial Cash: {settings.paper_initial_cash:,.0f}",
        f"Risk Engine: Active",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text(
            f"Current mode: {settings.live_trading_mode}\n"
            "Usage: /mode <paper|live|hybrid>"
        )
        return

    mode = args[0].lower()
    if mode not in ("paper", "live", "hybrid"):
        await update.message.reply_text("Mode must be: paper, live, or hybrid")
        return

    settings.live_trading_mode = mode
    settings.live_trading_enabled = mode in ("live", "hybrid")
    settings.paper_enabled = mode in ("paper", "hybrid")

    await update.message.reply_text(
        f"✅ Mode switched to: <b>{mode}</b>\n"
        f"Live: {'Enabled' if settings.live_trading_enabled else 'Disabled'}\n"
        f"Paper: {'Enabled' if settings.paper_enabled else 'Disabled'}",
        parse_mode="HTML"
    )


async def _unknown(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Unknown command. Use /help for available commands.")


def build_app() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set. Add it to .env or environment.")

    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("help", _guard(_cmd_help)))
    app.add_handler(CommandHandler("ping", _guard(_cmd_ping)))
    app.add_handler(CommandHandler("analyze", _guard(_cmd_analyze)))
    app.add_handler(CommandHandler("deep", _guard(_cmd_deep)))
    app.add_handler(CommandHandler("paper_trade", _guard(_cmd_paper_trade)))
    app.add_handler(CommandHandler("paper_close", _guard(_cmd_paper_close)))
    app.add_handler(CommandHandler("positions", _guard(_cmd_positions)))
    app.add_handler(CommandHandler("portfolio", _guard(_cmd_portfolio)))
    app.add_handler(CommandHandler("trades", _guard(_cmd_trades)))
    app.add_handler(CommandHandler("risk", _guard(_cmd_risk)))
    app.add_handler(CommandHandler("kill", _guard(_cmd_kill)))
    app.add_handler(CommandHandler("unhalt", _guard(_cmd_unhalt)))
    app.add_handler(CommandHandler("status", _guard(_cmd_status)))
    app.add_handler(CommandHandler("mode", _guard(_cmd_mode)))

    # Live trading & approval
    app.add_handler(CommandHandler("live_trade", _guard(_cmd_live_trade)))
    app.add_handler(CommandHandler("approve", _guard(_cmd_approve)))
    app.add_handler(CommandHandler("reject", _guard(_cmd_reject)))
    app.add_handler(CommandHandler("pending", _guard(_cmd_pending)))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _guard(_unknown)))

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = build_app()
    log.info("Starting bot in polling mode (requires TELEGRAM_BOT_TOKEN).")
    app.run_polling()


if __name__ == "__main__":
    main()