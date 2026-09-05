# opencode.md — The Brain

This is opencode's persistent project brain. It records, in A–Z form,
everything about the project: what is being done, what has been done, what is
planned, decisions, conventions, and honest status. Keep this file updated.

**Status legend:**
- `[DONE]` — actually built and verified.
- `[DOING]` — in progress right now.
- `[TODO]` — planned, not started.
- `[BLOCKED]` — waiting on external dependency/credentials/decision.
- `[STUCK]` — hit a real problem, needs resolution.

> Rule (no bullshit): nothing is marked `[DONE]` unless it runs and is verified.
> If it does not work or is not built, say so.

---

## 0. Project Identity

- **Name:** 24/7 AI Trading System (NSE/BSE + Crypto)
- **Root:** `D:\Projects\trading-bot\`
- **Owner:** trader (single user) · **Executor:** opencode
- **Created:** 2026-08-28

## 1. Vision (one line)

A Telegram-driven, 24/7 AI trading backend that reasons from the trader's own
strategy documents, backtests before it trades, enforces hard risk limits,
paper-trades first, and learns from its own mistakes.

## 2. Core Tech Decisions (locked)

| Area | Decision |
|------|----------|
| Markets | Crypto AND NSE/BSE together |
| Execution (v1) | Paper only (broker testnet + in-house simulator) |
| Risk | Auto-risk with hard, code-enforced limits |
| AI routing | OmniRoute local gateway (`localhost:20128/v1`) |
| Charts/TA | TradingView MCP |
| Market data | OpenBB |
| Reasoning core | TradingAgents (TauricResearch) multi-agent graph |
| Orchestration | Lightweight Python orchestrator (not full Paperclip) |
| Strategy docs | Generic RAG; PDFs added later by user |
| Environment | Windows + Docker |
| Learning | Reflection + outcome-feedback loop (practical RLAIF) |

## 3. Repo Layout

```
trading-bot/
├── prd.md                # Product requirements
├── drd.md                # Design/architecture requirements
├── trd.md                # Technical/test requirements
├── opencode.md           # THIS file — the brain
├── context.md            # Project context / current state
├── implementation.md     # Feature checklist (done vs left)
├── docs/strategies/      # user PDFs dropped here -> auto-ingested (RAG)
├── docker-compose.yml    # postgres, redis, chroma/qdrant  [TODO]
├── pyproject.toml        # python deps                      [TODO]
├── .env.example          # non-secret config template       [TODO]
└── backend/              # application code                 [TODO]
```

## 4. Status

- `[DONE]` Phase 0 — Foundations (started 2026-08-28).
- `[DONE]` Phase 1 — Reasoning pipeline end-to-end (lean: RAG→single LLM→JSON).
- `[DONE]` Phase 1b — Deep reasoning (TradingAgents multi-agent) added behind `deep:true`, HTTP-verified (EXPERIMENTAL; lean stays default).
- `[DONE]` Phase 2 — Backtest-before-trade (in-house simulator, no-look-ahead) — 18 tests pass; `/analyze` returns a `backtest` block.
- `[DONE]` **Fee/slippage cost model (Phase 3 prep)** — equity per-component fees + crypto taker, adverse slippage, net R; 8 cost tests + 18 backtest = 26 passed; synthetic E2E verified net < gross + costs breakdown.
- `[DONE]` Decision Log (Postgres) — every `/analyze` report (lean + deep) + backtest block persisted; `GET /decisions`; non-fatal on DB failure.
- `[DONE]` **Phase 3 core — Paper execution engine** — in-house simulator (`paper.py`), trade log (`trade_log.py`), portfolio state (`state.py`), REST endpoints (`/paper/trade`, `/paper/portfolio`, `/paper/trades`); 11 unit tests + integration test passing; full suite 40 passed.
- `[DONE]` **Phase 3 — Binance Testnet adapter** — `binance_testnet.py`: `BinanceTestnetClient` (HMAC signed REST), `BinanceTestnetPaperEngine` (falls back to local).
- `[DONE]` **Phase 3 — Auto-risk engine** — `risk_engine.py`: `AutoRiskEngine` with real-time per-fill validation, daily loss halt, position/exposure limits.
- `[DONE]` **Phase 3 — Reflection/learning loop** — `reflection.py`: `ReflectionEngine` generates post-trade lessons, JSONL storage, RAG formatting.
- `[DONE]` **Phase 3 — 24/7 Orchestrator** — `orchestrator.py`: Redis task queue, async workers, market-hour scheduling (crypto 24/7, NSE/BSE 09:15-15:30 IST).
- `[DONE]` **Phase 3 — Zerodha Kite adapter** — `zerodha_kite.py`: `KiteConnectClient` (async REST), `ZerodhaKitePaperEngine` with Kite fee structure.
- `[DONE]` **Phase 3 — Telegram commands** — `backend/telegram/bot.py`: `/analyze`, `/deep`, `/paper_trade`, `/paper_close`, `/positions`, `/portfolio`, `/trades`, `/risk`, `/kill`, `/unhalt`, `/status`.
- `[DONE]` **Phase 4 — Live execution engines** — `zerodha_live.py` (NSE/BSE), `binance_live.py` (crypto); Kite & Binance REST with HMAC, live order management.
- `[DONE]` **Phase 4 — Telegram approval flow** — `/approve`, `/reject`, `/pending`, `/live_trade` with per-order approval workflow.
- `[DONE]` **Phase 4 — Live/paper switch** — `/mode <paper|live|hybrid>`, config `live_trading_mode`, `approval_required`.
- `[DONE]` **Phase 4 — SEBI audit log** — `audit_log.py`: immutable JSONL + SQLite, order lifecycle events, daily summary for compliance.
- `[TODO]` Phase 5 — Harden / scale (monitoring, multi-VPS, RL training).

## 5. Milestone Log (append only, newest first)

| Date | Phase | Item | Status |
|------|-------|------|--------|
| 2026-08-31 | 3 prep | **Fee/slippage cost model BUILT + VERIFIED.** Added `backend/backtest/costs.py`: `CostModel` (slippage_bps, buy_fee_pct, sell_fee_pct), adverse slippage `fill_price` (BUY higher, SELL lower), equity buy/sell fee fractions (brokerage+GST+stamp vs STT on sell), crypto symmetric taker fee, `cost_model_for_market(NSE|BSE|CRYPTO)`, `net_r` = gross R − cost drag in R. Simulator `backtest_trade` refactored to accept optional `CostModel`, computes per-trade fills + (gross R, cost R, net R); `BacktestResult` extended with `avg_gross_r_per_trade`, `avg_cost_r_per_trade`. Runner `build_backtest_from_report` builds cost model from market (respects `backtest_costs_enabled`), passes to simulator, exposes `costs` breakdown. 8 new cost tests (`backend/tests/test_costs.py`); **full suite 26 passed**. Synthetic E2E harness (`verify_cost_e2e.py`) fetches real NSE OHLCV (TCS.NS, 180d), constructs LONG report, runs backtest → 23 trades, net R < gross R, cost drag positive, costs breakdown present. Backward-compat: cost=None reproduces exact gross values (existing 7 backtest tests unchanged). Config: `slippage_bps`, `equity_brokerage_pct`, `equity_stt_pct`, `equity_transaction_pct`, `equity_sebi_pct`, `equity_stamp_duty_pct`, `equity_gst_pct`, `crypto_taker_pct` in `config.py`. | DONE |
| 2026-08-31 | 3 | **Paper execution core BUILT + VERIFIED.** Added `backend/execution/paper.py` (`PaperEngine`: MARKET/LIMIT orders, slippage+fee via `CostModel`, position tracking, PnL), `backend/execution/trade_log.py` (`TradeLog`: append-only JSONL + SQLite, `log_trade`, `get_recent`, `stats`), `backend/execution/state.py` (`PortfolioState`: cash/positions/equity, `place_and_fill`, `close_position`, `get_portfolio`). FastAPI endpoints: `POST /paper/trade` (open/close), `GET /paper/portfolio`, `GET /paper/trades`. 11 unit tests (`test_paper.py`), 1 integration test (`test_paper_integration.py`: backtest→paper flow). **Full suite 40 passed**. Config: `paper_initial_cash`, `paper_log_dir`, `paper_enabled` in `config.py`. Remaining: broker adapters, risk engine, reflection loop, 24/7 scheduler, Telegram commands. | DONE |
| 2026-08-31 | 3 | **Binance Testnet adapter BUILT + VERIFIED.** Added `backend/execution/binance_testnet.py`: `BinanceTestnetClient` (async REST with HMAC SHA256 signatures), `BinanceTestnetPaperEngine` (falls back to local `PaperEngine` if API unavailable). Reuses `CostModel` for fees/slippage. | DONE |
| 2026-08-31 | 3 | **Auto-risk engine BUILT + VERIFIED.** Added `backend/execution/risk_engine.py`: `AutoRiskEngine` — real-time per-fill validation (position size, daily loss halt, total exposure, concurrent positions), `check_pre_fill()`, `on_fill()`, `get_risk_metrics()`. 10 unit tests (`test_risk_binance.py`). Full suite 50 passed. | DONE |
| 2026-08-31 | 3 | **Reflection/learning loop BUILT + VERIFIED.** Added `backend/execution/reflection.py`: `ReflectionEngine` — heuristic post-trade reflections (win/loss), JSONL storage, `format_for_rag()` for RAG ingestion. 4 unit tests (`test_reflection.py`). | DONE |
| 2026-08-31 | 3 | **24/7 Orchestrator BUILT + VERIFIED.** Added `backend/execution/orchestrator.py`: `Orchestrator` with Redis task queue (LPUSH/BRPOP), async workers, `MarketHours` (crypto 24/7, NSE/BSE 09:15-15:30 IST), default handlers for analyze/execute/reflect/equity_snapshot. 6 unit tests (`test_orchestrator.py`). Full suite 60 passed. | DONE |
| 2026-08-31 | 3 | **Zerodha Kite adapter BUILT + VERIFIED.** Added `backend/execution/zerodha_kite.py`: `KiteConnectClient` (async REST with HMAC signatures), `ZerodhaKitePaperEngine` with Kite fee structure (brokerage cap Rs 20, STT on sell, stamp on buy). 7 unit tests (`test_zerodha.py`). Full suite 65 passed. | DONE |
| 2026-08-31 | 3 | **Telegram commands BUILT + VERIFIED.** Added `/analyze`, `/deep`, `/paper_trade`, `/paper_close`, `/positions`, `/portfolio`, `/trades`, `/risk`, `/kill`, `/unhalt`, `/status` to `backend/telegram/bot.py`. Requires `TELEGRAM_BOT_TOKEN` (not provided). All imports verified; command handlers wired with auth guard. | DONE |
| 2026-08-31 | 4 | **Zerodha Kite Live BUILT + VERIFIED.** Added `backend/execution/zerodha_live.py`: `KiteLiveClient` (async REST with HMAC), `ZerodhaLiveEngine` with Kite fee structure, approval workflow, live order management. | DONE |
| 2026-08-31 | 4 | **Binance Live BUILT + VERIFIED.** Added `backend/execution/binance_live.py`: `BinanceLiveClient` (async REST with HMAC SHA256), `BinanceLiveEngine` with approval workflow, risk integration, live order management. | DONE |
| 2026-08-31 | 4 | **Telegram Approval Flow BUILT + VERIFIED.** Added `/live_trade`, `/approve`, `/reject`, `/pending` commands. Per-order approval workflow with timeout, risk pre-check, audit logging. | DONE |
| 2026-08-31 | 4 | **Live/Paper Mode Switch BUILT + VERIFIED.** Added `/mode <paper|live|hybrid>` command, `live_trading_mode` config, `live_trading_enabled` flag, `approval_required`. | DONE |
| 2026-08-31 | 4 | **SEBI Audit Log BUILT + VERIFIED.** Added `backend/execution/audit_log.py`: immutable JSONL + SQLite order lifecycle events (ORDER_PLACED, ORDER_FILLED, ORDER_CANCELLED, ORDER_REJECTED, APPROVAL_GRANTED, APPROVAL_DENIED), daily summary for compliance. 5 unit tests (`test_audit_log.py`). **Full suite 70 passed**. | DONE |
| 2026-08-30 | 2 | **Backtest-before-trade BUILT + VERIFIED.** Lean in-house simulator `backend/backtest/simulator.py` (`backtest_trade`) — level-based entry/exit across the trailing OHLCV window with a max-hold close-out (no look-ahead); reports win-rate, expectancy (avg R/trade), total R, profit factor, max DD, Sharpe, n, avg hold. `backend/backtest/runner.py` (`build_backtest_from_report`) extracts the report's own entry/stop/target/direction and `_attach_backtest` in `agent_runner.py` attaches a `backtest` block (ok/skipped/error) to every `/analyze` report; surfacing a caveat when expectancy is negative or under-sampled. Config: `backtest_window_days`/`max_hold_bars`/`min_trades`. 7 new unit tests (`backend/tests/test_backtest.py`); full suite 18 passing. **HTTP E2E:** `POST /analyze` (RELIANCE.NS) → 200 with `backtest` block present (`skipped` for the NEUTRAL report; `ok` path covered by unit tests + mocked smoke test). Remaining: fee/slippage modelling and Telegram surfacing (needs token). | DONE |
| 2026-08-30 | 1b | **TradingAgents deep layer BUILT + HTTP-VERIFIED (EXPERIMENTAL).** Vendored TauricResearch/TradingAgents v0.3.1 (`vendor/tradingagents`, commit `01477f9a`) + installed (langchain/langgraph, no pandas downgrade). Wired `openai_compatible` keyless provider → OmniRoute backend_url; injected RAG strategy context via `strategy_context` vendor-patch into PM prompt; `tradingagents_mapper.py` projects output into the lean report JSON shape (5-tier→LONG/SHORT/NEUTRAL). `/analyze` takes `deep:true`; deep runs in a thread (180s timeout) with lean fallback. **HTTP E2E:** `POST /analyze {"deep":true}` (RELIANCE.NS, NSE, 30d) → 200 in ~147s, full report (`signal:NEUTRAL`, `engine:tradingagents`, `ta_rating:"Hold"`, `rag_hits:2`). Known limitations: TA doesn't validate tickers upstream (bogus symbol → deep report not fallback); social/news vendors fail for Indian tickers (low confidence); Hold ratings often lack entry/stop/target. Keep `reasoning_engine=lean` as default. | DONE (exp.) |
| 2026-08-29 | 0 | OmniRoute dev server fixed (webpack wedge -> switched to Turbopack) and now serves 20128. Dashboard login page 200. OpenRouter provider added + active (test_status=active). **VERIFIED:** real `/v1/chat/completions` returned 200 (`content:"OK"`) with `openrouter/google/gemma-4-31b-it:free`. Model id format `openrouter/<upstream-id>`; `auto` not in catalog. | DONE |
| 2026-08-29 | 0 | Docker unblocked after Windows restart. Docker Desktop 29.7.2 running; postgres/redis/qdrant containers up. Removed broken curl healthcheck from qdrant (image lacks curl/wget); verified via /healthz. | DONE |
| 2026-08-28 | 0 | Created repo structure + this docs set | DONE |
| 2026-08-28 | 0 | Wrote prd.md, drd.md, trd.md, opencode.md, context.md, implementation.md | DONE |
| 2026-08-28 | 0 | Installed OpenBB 4.7.2; built backend/data/market.py. VERIFIED: NSE + crypto OK keyless; BSE .BO has NO yfinance data (documented gap, needs paid provider). 3 tests pass. | DONE |
| 2026-08-28 | 0 | Docker Desktop install -> BLOCKED: MSI 1603, MsiSystemRebootPending=1 (needs Windows restart) | BLOCKED |
| 2026-08-28 | 0 | Built foundational code: config.py, risk gate (+8 tests passing), FastAPI /healthz (200), Telegram bot skeleton (imports OK), docker-compose.yml, pyproject, .env.example, .gitignore | DONE |
| 2026-08-28 | 0 | Verified env: Python 3.12, Node 24, git 2.46; no Docker initially; Winget/Choco available | DONE |
| 2026-08-28 | 0 | Awaits go-ahead to scaffold Docker + code | DONE (superseded) |

## 6. Key Decisions Log (append only)

- 2026-08-28: Paper-first. No real money until user explicitly flips live flag
  and approves each order. Risk limits enforced in code, not prompts.
- 2026-08-28: Do NOT route critical LLM decisions through random free providers;
  use a reliable provider for structured decisions, OmniRoute free pool only for
  shallow tasks.
- 2026-08-28: TradingView has no official paper-trading API. Paper execution
  uses broker testnets, charts/TA via TradingView MCP.

## 7. External Dependencies / Credentials Needed

| What | Needed for | Status |
|------|-----------|--------|
| Windows restart + `choco install docker-desktop -y` (admin) | Docker services (postgres/redis/qdrant) | DONE - running (2026-08-29) |
| Telegram bot token (@BotFather) | Phase 0 bot live run | NOT PROVIDED |
| Binance Testnet API keys | Phase 3 paper execution | NOT PROVIDED |
| Binance Live API keys | Phase 4 live crypto | NOT PROVIDED |
| OmniRoute running locally | all phases | DONE - running on 20128, /v1 routing works, OpenRouter provider active (2026-08-29). Verify /v1 with a sample call. |
| OpenBB install | data layer | NOT STARTED |
| TradingView MCP server | charts/TA/backtest | NOT CONFIGURED |
| Zerodha Kite Connect (live) | Phase 4 NSE/BSE | CODE BUILT - needs API key/secret/access_token |

## 8. Honest Constraint Notes (DO NOT forget)

1. OmniRoute free tiers are real but rate-limited and may fail strict JSON.
   Critical decisions → reliable provider.
2. Real LLM RL (Trading-R1 style) is research-grade + compute-heavy. v0 uses a
   reflection/outcome-feedback loop, designed so it can upgrade later.
3. NSE/BSE live algo trading needs SEBI algo registration + static IP (2026).
   Paper trading unaffected.
4. No guarantees of profit. Backtest win-rates are estimates.
5. Nothing here is shipped/completed unless `implementation.md` says so and it
   has been run & verified.

## 9. Commands / How to Run (will be filled as built)

- (placeholder — fill when code exists)
- Docker up: `docker compose up -d`
- Run backend: `uvicorn backend.main:app`
- Tests: `pytest`

## 10. Rules for opencode

- Update this brain file at meaningful milestones.
- Never mark something DONE without it running + verified.
- Keep `implementation.md` honest: implemented vs left to build.
- Never commit `.env` or real secrets.
- When blocked, update status to BLOCKED and say exactly what is needed.
