# Telegram Bot Command Guide

The backend is operated from Telegram via the poller (`backend/telegram/bot.py`).
Every command is gated by an **authorization guard**: only allow-listed Telegram
user IDs can talk to the bot. Unknown users get `⛔ You are not authorized to use this bot.`

Symbols are uppercased automatically. For the optional `market` argument the accepted
values are `NSE`, `BSE`, or `CRYPTO`, and it **defaults to `NSE`** when omitted.

---

## Paper / Analysis commands

### `/help`
Prints the full command help (HTML formatted).

### `/ping`
Health/latency check. Replies `🏓 pong`.

### `/analyze <symbol> [market]`
Lean single-pass LLM analysis with backtest + RAG.

- `symbol` (required) e.g. `RELIANCE.NS`, `BTC-USD`.
- `market` (optional, default `NSE`).
- Replies `🔍 Analyzing <symbol> (<market>)...` then the report:
  signal, confidence, summary, entry/SL/target/R:R, and backtest stats (trades,
  win rate, avg R, profit factor) plus cost model when applicable.
- Uses the configured primary model (`reasoning_model`), falling back to the
  fallback model on a non-transient failure.

### `/deep <symbol> [market]`
Deep TradingAgents multi-agent analysis (experimental, slow).

- Same arguments as `/analyze`.
- Replies `🧠 Deep analysis ... (this takes ~2-3 min)` then a report with the
  `engine` noted in `_meta`. Falls back to the lean report on any failure/timeout.

### `/paper_trade <symbol> <LONG|SHORT> <qty> <entry_price> [market]`
Open a paper (simulated) position.

- `symbol`, `side` (`LONG`/`SHORT`), `qty` (number), `entry_price` (number).
- `market` optional (default `NSE`).
- Applies slippage/fees via the cost model and reports the fill price, fee, and
  order ID.

### `/paper_close <symbol> <LONG|SHORT> <exit_price> [market]`
Close an open paper position.

- `symbol`, `side`, `exit_price` (number); `market` optional.

### `/positions`
List all currently open paper positions.

### `/portfolio [symbol]`
Show cash, equity, and positions. With an optional `symbol`, focus that symbol.

### `/trades [symbol]`
Show the paper trade history. With an optional `symbol`, filter to that symbol.

### `/risk`
Summarize the active risk state and limits.

### `/kill`
Trigger the trading halt (stop new paper/live activity).

### `/unhalt`
Resume trading after a halt.

---

## Status / mode

### `/status`
Show system health: DB reachability, mode, paper-enabled, live-enabled,
approval-required, initial cash, risk-engine status.

### `/mode <paper|live|hybrid>`
Switch the operating mode.

- `paper` — paper trading only (live disabled).
- `live` — live trading only (paper disabled).
- `hybrid` — both.

Note: `/mode live` and `/mode hybrid` enable live trading. Live orders cannot be
placed while `live_trading_enabled` is false (see `/live_trade`).

---

## Live trading & approval workflow

Live orders are placed per-order and routed through a risk pre-check. If
`APPROVAL_REQUIRED` is set (default for live), orders enter a **pending** queue
and must be approved or rejected on Telegram.

### `/live_trade <symbol> <BUY|SELL> <qty> <price> [market]`
Place a live LIMIT order.

- `symbol`, `side` (`BUY`/`SELL`), `qty` (integer), `price` (number),
  `market` optional (default `NSE`; use `CRYPTO` for Binance).
- Blocked with a message unless live trading is enabled.
- Outcome:
  - `⏳ Order Pending Approval` → approve/reject with the order ID.
  - `❌ Order rejected` → a live risk check failed.
  - `✅ Live Order Placed` → filled/placed immediately (approval disabled).

### `/approve <order_id>`
Approve a pending live order. Checks Binance then Zerodha pending queues.
Replies with the executed status on success.

### `/reject <order_id>`
Reject a pending live order. Confirms rejection if found in either engine's
pending queue.

### `/pending`
List all pending live approvals (both Binance and Zerodha).

---

## Configuration notes

- Model routing: `/analyze` uses `REASONING_MODEL` with fallback
  `REASONING_MODEL_FALLBACK`; deep analysis uses `TRADINGAGENTS_DEEP_MODEL` /
  `TRADINGAGENTS_QUICK_MODEL`. All LLM traffic flows through the OmniRoute
  gateway (`OMNIROUTE_BASE_URL`, keyless or `OMNIROUTE_API_KEY`).
- The gateway is configured to request non-streaming completions
  (`stream: false`); the client also tolerates SSE/streamed responses.
- Free `:free` models are reliable-enough for shallow calls but can transiently
  return empty / rate-limit (429). The pipeline retries with cooldown and only
  switches to the fallback model on a non-transient failure.

## Verification checklist

1. OmniRoute up: `http://localhost:20128/healthz` → `200 ok`.
2. Exactly one bot poller running (duplicate pollers cause `409 Conflict`).
3. `/ping` in Telegram → `🏓 pong`.
4. `/analyze BTC-USD CRYPTO` → full report (no truncation).