# context.md — Project Context

**Purpose:** A concise snapshot of what this project is, its current state, and
what changed recently. Read this first before making changes so you don't guess
wrong. This is NOT a feature checklist — that lives in `implementation.md`.

---

## What this project is

A Telegram-operated, 24/7 AI trading backend covering NSE/BSE equities and
cryptocurrency. It:

1. Reasons about markets using a multi-agent LLM framework (TradingAgents),
   steered by the trader's own strategy documents via RAG.
2. Backtests each proposed trade and reports probabilities / R/R.
3. Enforces hard risk limits in code.
4. Paper-trades first; real money only behind explicit gates.
5. Learns from outcomes via a reflection/memory loop.
6. Routes all LLM traffic through the OmniRoute AI gateway.

## Current state (as of 2026-08-31)

- **Phase 4 complete (built 2026-08-31).** Phases 0, 1 (lean), 1b (deep, experimental), 2 (backtest-before-trade with fee/slippage), Decision Log (Postgres), Phase 3 (paper execution) done.
  - **Paper execution core**: in-house `PaperEngine` (MARKET/LIMIT, slippage+fees via `CostModel`), `TradeLog` (JSONL+SQLite), `PortfolioState` (cash/positions/equity), REST endpoints (`/paper/trade`, `/paper/portfolio`, `/paper/trades`).
  - **Binance Testnet adapter**: `BinanceTestnetClient` (async REST, HMAC SHA256), `BinanceTestnetPaperEngine` (fallback to local).
  - **Auto-risk engine**: `AutoRiskEngine` — per-fill validation, daily loss halt, position/exposure limits.
  - **Reflection/learning loop**: `ReflectionEngine` — post-trade lessons, JSONL, RAG formatting.
  - **24/7 Orchestrator**: Redis task queue, async workers, `MarketHours` (crypto 24/7, NSE/BSE 09:15-15:30 IST).
  - **Zerodha Kite adapter**: `KiteConnectClient` (async REST, HMAC signatures), `ZerodhaKitePaperEngine` with Kite fee structure (brokerage cap Rs 20, STT on sell, stamp on buy).
  - **Telegram commands (paper)**: `/analyze`, `/deep`, `/paper_trade`, `/paper_close`, `/positions`, `/portfolio`, `/trades`, `/risk`, `/kill`, `/unhalt`, `/status` — auth guard, needs `TELEGRAM_BOT_TOKEN`.
  - **Live execution engines**: `ZerodhaLiveEngine` (NSE/BSE), `BinanceLiveEngine` (crypto) — async REST with HMAC, live order management, risk integration.
  - **Telegram live commands**: `/live_trade`, `/approve`, `/reject`, `/pending`, `/mode <paper|live|hybrid>` — per-order approval workflow, risk pre-check, live/paper/hybrid mode switching.
  - **SEBI Audit Log**: `AuditLog` — immutable JSONL + SQLite, order lifecycle events (ORDER_PLACED, ORDER_FILLED, ORDER_CANCELLED, ORDER_REJECTED, APPROVAL_GRANTED, APPROVAL_DENIED), daily compliance summary.
  - **Full suite 70 tests passing** (28 prior + 11 paper + 10 risk/binance + 4 reflection + 6 orchestrator + 7 zerodha + 5 audit + 1 integration).
- **Phase: Fee/Slippage cost model (built 2026-08-31).** ...
- **Docker is RUNNING.** After the Windows restart cleared the pending-reboot
  flag, Docker Desktop installed successfully (v29.7.2). The three Phase 0
  services are up: `trading-postgres` (healthy), `trading-redis` (healthy),
  `trading-qdrant` (up; its `curl`-based healthcheck was removed from compose
  because the qdrant image ships no curl/wget — verified working via
  `http://localhost:6333/healthz`).
- **Foundational app code is built and verified:**
  - `backend/core/config.py` — typed settings (env/.env) + risk limits.
  - `backend/risk/gate.py` — hard risk limits, **8/8 unit tests passing**.
  - `backend/main.py` — FastAPI with `/healthz` and `/` (both verified 200).
  - `backend/telegram/bot.py` — Telegram skeleton (`/help`, `/ping`, auth
    guard); imports OK (needs a real token to run live).
  - `backend/data/market.py` — OpenBB data layer: `fetch_ohlcv()` normalizes to
    fixed lowercase OHLCV order. **VERIFIED live**: NSE `RELIANCE.NS` and crypto
    `BTC-USD` return real data via keyless yfinance provider. **BSE `.BO` has no
    yfinance data** (documented gap). 3 unit tests pass.
  - `docker-compose.yml`, `pyproject.toml`, `.env.example`, `.gitignore` written.
  - Python `.venv` at `backend/.venv` with base deps + OpenBB installed
    (note: OpenBB pinned fastapi 0.136.3 / uvicorn 0.40 — app still imports OK).

## Phase 1 (lean reasoning pipeline) — built 2026-08-30

- **Reasoning:** `backend/reasoning/{llm, prompts, agent_runner}.py` —
  httpx client to OmniRoute `/v1/chat/completions` (cooldown-aware 429 retry,
  model fallback), terse JSON-only prompts, and `analyse_symbol()` = OHLCV →
  RAG → LLM → robust JSON parse (`json_repair`).
- **RAG:** `backend/rag/{embeddings, store, ingest, retriever}.py` —
  fastembed `bge-small-en-v1.5` (384-dim), Qdrant collection `strategy_docs`,
  ingest from `./docs/strategies`, `retrieve_context()`.
- **VERIFIED E2E 2026-08-30:** `analyse_symbol("RELIANCE.NS","NSE",days=30)`
  returns a complete structured JSON report (`signal:NEUTRAL`, `_meta.rag_hits:2`),
  nothing truncated.
- **Model change (2026-08-30):** free OpenRouter `:free` models persistently
  429 + hard ~300-token output cap → truncated JSON (unrecoverable). **RESOLVED**
  by adding cheap paid model `openai/gpt-4o-mini` to the OmniRoute synced catalog
  (direct DB edit, backup in `~/.omniroute/storage.backup-before-inject-gpt4omini.sqlite`)
  and verified `/v1/chat/completions` → 200 counted. Config now:
  `reasoning_model=openrouter/openai/gpt-4o-mini`, fallback
  `openrouter/openai/gpt-4.1-mini`.
- **FastAPI endpoints added:** `POST /analyze` and `POST /ingest` in `main.py`.
  **HTTP-E2E VERIFIED 2026-08-30:** `POST /ingest` → 200 (files:1, chunks:2);
  `POST /analyze` (RELIANCE.NS, NSE, 30d) → 200 in ~39s, full JSON report
  `signal:NEUTRAL`, `rag_hits:2`.

## Phase 1b (deep: TradingAgents multi-agent) — built 2026-08-30 [EXPERIMENTAL]

- **Vendored** `TauricResearch/TradingAgents` v0.3.1 into `vendor/tradingagents`
  (pinned commit `01477f9a`) + `pip install .` into venv (langchain/langgraph
  stack added; pandas/yfinance NOT downgraded).
- **Engine:** `backend/reasoning/tradingagents_engine.py` — runs the graph via
  `openai_compatible` keyless provider → OmniRoute `backend_url`
  (`localhost:20128/v1`); models default to `openrouter/openai/gpt-4o-mini`.
- **RAG injection:** minimal vendor-patch (`strategy_context` appended to
  `past_context` in `trading_graph._run_graph`) feeds our retrieved strategy
  docs into the Portfolio Manager prompt.
- **Mapper:** `backend/reasoning/tradingagents_mapper.py` projects the 5-tier
  TA output into the lean report JSON shape (LONG/SHORT/NEUTRAL, sentiment-
  derived confidence, entry/stop/target, R/R, key_levels, caveats,
  `_meta.engine:"tradingagents"`).
- **Wiring:** `/analyze` accepts `deep:true`; deep runs in a thread with a 180s
  timeout and falls back to the lean report on any failure.
- **HTTP-E2E VERIFIED 2026-08-30:** `POST /analyze {"deep":true}` (RELIANCE.NS,
  NSE, 30d) → 200 in ~147s, full mapped report (`signal:NEUTRAL`,
  `engine:tradingagents`, `ta_rating:"Hold"`, `rag_hits:2`).
- **Known limits (honest):** TradingAgents does NOT validate tickers upstream
  (bogus symbol → deep report, not fallback); its social/news vendors
  (StockTwits/Reddit) fail for Indian tickers → confidence often low; Hold
  ratings often lack entry/stop/target. Keep `reasoning_engine=lean` as the
  operating default; deep is on-demand only.

## Phase 2 (backtest-before-trade) — built 2026-08-30

- **Decision 2026-08-30:** lean in-house OHLCV simulator (`backend/backtest/`) instead
  of vectorbt/backtrader — pure numpy/pandas, zero extra deps, easy to unit-test.
- **Simulator:** `backend/backtest/simulator.py` → `backtest_trade()` replays the
  proposed setup's exit rule (level-based entry/stop/target + max-hold close-out)
  across the trailing OHLCV window. **No look-ahead:** each bar uses only current/prior
  data; unit-tested. Reports win-rate, expectancy (avg R/trade), total R, profit factor,
  max DD, Sharpe, n, avg hold.
- **Wiring:** `backend/backtest/runner.py` (`build_backtest_from_report`) extracts
  signal/entry/stop/target/direction from the report and fetches OHLCV; `_attach_backtest`
  in `agent_runner.py` attaches a `backtest` block (`ok`/`skipped`/`error`) to every
  `/analyze` report (lean + deep), and `_surface_backtest_caveat` adds a caveat when the
  setup is negative-expectancy or under-sampled.
- **Config:** `backtest_enabled`, `backtest_window_days` (#365), `backtest_max_hold_bars`
  (20), `backtest_min_trades` (5) in `config.py`. Runtime-verified via `Settings`.
- **Tests:** `backend/tests/test_backtest.py` (7 new). Full suite **18 passing**.
- **HTTP E2E VERIFIED 2026-08-30:** `POST /analyze` (RELIANCE.NS, NSE) → 200 with
  `backtest` block present (`status:"skipped"` because the report was NEUTRAL with no
  setup; the `ok` path is covered by unit tests + a mocked report→simulator smoke test).
- **Honest gaps:** fee/slippage not modelled (exits are gross); backtest is informative
  (caveat) not binding — the hard risk gate (`backend/risk/gate.py`) remains the rejection
  authority at execution. Telegram surfacing deferred (needs bot token).

## Data access contract (verified)

- **NSE** equity: `RELIANCE.NS` via `obb.equity.price.historical(..., provider='yfinance')` — works keyless.
- **Crypto**: `BTC-USD` via `obb.crypto.price.historical(..., provider='yfinance')` — works keyless.
- **BSE**: `RELIANCE.BO` via yfinance = **empty** (no coverage). BSE needs a paid
  provider (fmp/intrinio/tiingo with API key) or another source. Flagged, not solved.
- OpenBB returns lowercase columns; our `market.py` normalizes to a fixed order
  `[open, high, low, close, volume]`.

## What is NOT true yet (be honest here)

- Docker services are running (postgres/redis/qdrant up as of 2026-08-29).
- OmniRoute IS running locally on `localhost:20128` (dev server, 2026-08-29, Turbopack).
  Login: http://localhost:20128/login (initial password `CHANGEME`). Server serves HTTP 200
  and `/v1` routing works; **OpenRouter provider is added and active** (test_status=active).
  Model id format is `openrouter/<upstream-id>` (`auto` not in synced catalog). Free
  `:free` models 429+cooldown and truncate JSON — use paid. **Verified 2026-08-30:** paid
  `openrouter/openai/gpt-4o-mini` → 200 counted.
- No Telegram bot running live (no token provided yet).
- No TradingView MCP integration.
- No live trading deployed — live engines built but not deployed (need API keys/tokens).
  (The Phase 4 live execution engines ARE built: `ZerodhaLiveEngine`, `BinanceLiveEngine` with HMAC REST, approval workflow, risk integration. The Phase 3 paper execution IS fully built. The Phase 2 backtest engine WITH fee/slippage costs AND the Postgres decision log ARE built and wired into `/analyze`; persistence verified end-to-end.)
- **Full HTTP `/analyze` + `/ingest` are VERIFIED 200** (2026-08-30) — Phase 1 close-out done.
- **Deep (`deep:true`) is HTTP-verified but EXPERIMENTAL** (2026-08-30): quality/cost on
  gpt-4o-mini not yet proven across symbols; TradingAgents does not validate tickers
  (bogus symbol → deep report, not fallback); social/news vendors fail for Indian tickers.
  Lean is the operating default.
- The risk gate + its tests and the health endpoints are verified real logic; the
  reasoning pipeline is verified off-HTTP too.

## Decisions that must not change casually

- **Paper-first.** No real-money orders unless user flips a live flag AND
  approves each order on Telegram.
- **Risk limits are code-enforced**, not prompt-based.
- **Critical LLM decisions** use a reliable provider; OmniRoute free pool only
  for shallow tasks. Never let a structured parse silently fall to an
  unreliable route.
- **TradingView MCP is for charts/TA/backtest only.** Paper *execution* goes
  through broker testnets (Binance Testnet) or the in-house paper simulator.

## External requirements still needed (not yet provided)

- Telegram bot token.
- Binance Testnet API keys (Phase 3).
- OmniRoute running locally (done 2026-08-29; verify /v1 with a sample call).
- Strategy PDFs (user will add later; design is generic).

## Recent changes / sessions

- 2026-08-31: **Phase 4 Live Trading BUILT + VERIFIED.**
  - **Zerodha Kite Live**: `backend/execution/zerodha_live.py` — `KiteLiveClient` (async REST, HMAC), `ZerodhaLiveEngine` with Kite fees, approval workflow, live order management.
  - **Binance Live**: `backend/execution/binance_live.py` — `BinanceLiveClient` (async REST, HMAC SHA256), `BinanceLiveEngine` with approval workflow, risk integration.
  - **Telegram Approval Flow**: `/live_trade`, `/approve`, `/reject`, `/pending` commands. Per-order approval workflow with timeout, risk pre-check, audit logging.
  - **Live/Paper Mode Switch**: `/mode <paper|live|hybrid>` command, `live_trading_mode` config, `live_trading_enabled` flag, `approval_required`.
  - **SEBI Audit Log**: `backend/execution/audit_log.py` — immutable JSONL + SQLite, order lifecycle events (ORDER_PLACED, ORDER_FILLED, ORDER_CANCELLED, ORDER_REJECTED, APPROVAL_GRANTED, APPROVAL_DENIED), daily compliance summary. 5 unit tests (`test_audit_log.py`).
  - **Full suite 70 passed** (28 prior + 11 paper + 10 risk/binance + 4 reflection + 6 orchestrator + 7 zerodha + 5 audit + 1 integration).
- 2026-08-31: **Phase 3 complete (built 2026-08-31).** Paper execution core, Binance Testnet, Auto-risk, Reflection, 24/7 Orchestrator, Zerodha Kite paper, Telegram commands. **Full suite 65 passed**.
- 2026-08-31: **Fee/slippage cost model BUILT + VERIFIED.** Added `backend/backtest/costs.py`: `CostModel` (slippage_bps, buy_fee_pct, sell_fee_pct), adverse slippage `fill_price` (BUY higher, SELL lower), equity buy/sell fee fractions (brokerage+GST+stamp vs STT on sell), crypto symmetric taker fee, `cost_model_for_market(NSE|BSE|CRYPTO)`, `net_r` = gross R − cost drag in R. Simulator `backtest_trade` refactored for optional `CostModel`, computes per-trade fills + (gross R, cost R, net R); `BacktestResult` extended with `avg_gross_r_per_trade`, `avg_cost_r_per_trade`. Runner `build_backtest_from_report` builds cost model from market (respects `backtest_costs_enabled`), passes to simulator, exposes `costs` breakdown. 8 new cost tests (`backend/tests/test_costs.py`); **full suite 26 passed**. Synthetic E2E harness (`verify_cost_e2e.py`) fetches real NSE OHLCV (TCS.NS, 180d), constructs LONG report, runs backtest → 23 trades, net R < gross R, cost drag positive, costs breakdown present. Backward-compat: cost=None reproduces exact gross values (existing 7 backtest tests unchanged). Config: `slippage_bps`, `equity_brokerage_pct`, `equity_stt_pct`, `equity_transaction_pct`, `equity_sebi_pct`, `equity_stamp_duty_pct`, `equity_gst_pct`, `crypto_taker_pct` in `config.py`. Docs updated.

## Where to go next

Follow `implementation.md` and `opencode.md`. **Phase 1 (lean), Phase 1b (deep,
experimental), Phase 2 (backtest-before-trade with fee/slippage), Decision Log (Postgres),
and Phase 3 (paper execution with Binance/Zerodha adapters, risk, reflection, orchestrator, Telegram commands) are built and verified.** Next concrete actions (build in phases):
1. **Live trading bridge** — Zerodha Kite live mode + Binance live mode + per-order Telegram approval (`APPROVAL_REQUIRED=true`) + live/paper switch.
2. **Monitoring/alerting** — Prometheus metrics, Grafana dashboards, Telegram alerts for risk events.
3. **Deep path optimization** — measure cost/quality on gpt-4o-mini across symbols; evaluate model tier upgrade.
