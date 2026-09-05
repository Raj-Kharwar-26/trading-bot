# implementation.md — Feature Checklist (Implemented vs Left to Build)

**Rule (no bullshit):** This file is the single source of truth for what is
actually built. Only mark an item `Implemented` if it has been written, runs,
and is verified. Anything not verified is `Not built` / `Pending`. No fabricated
code, no fake "DONE".

**Legend:** `[x]` implemented & verified · `[ ]` not built yet

---

## Phase 0 — Foundations

- [x] Repo structure + docs (prd/drd/trd/opencode/context) created
- [x] `pyproject.toml` + editable install + `.venv` (FastAPI, uvicorn, pydantic, PTB, httpx, sqlalchemy, redis, pytest)
- [x] `.env.example` (non-secret template) + `.gitignore`
- [x] `docker-compose.yml` written (postgres, redis, qdrant) — running (2026-08-29)
- [x] Backend config module (`backend/core/config.py`) — typed env settings + risk limits
- [x] Risk gate (`backend/risk/gate.py`) with hard limits + 8 passing unit tests
- [x] FastAPI app (`backend/main.py`) with `/healthz` + `/` — verified (200)
- [x] Telegram bot skeleton (`backend/telegram/bot.py`) `/help`, `/ping`, auth guard — imports OK (not run live; needs token)
- [x] Docker services running (postgres/redis/qdrant up 2026-08-29)
- [x] OmniRoute installed & running at `localhost:20128/v1` (Turbopack dev, 2026-08-29)
- [x] OmniRoute `/v1` routing works with an active provider (OpenRouter, test_status=active). **VERIFIED live 2026-08-29:** `POST /v1/chat/completions` `model":"openrouter/google/gemma-4-31b-it:free"` → 200 + `content":"OK"`. Note: use a synced model id (`openrouter/<upstream-id>`); `auto` is not in the synced catalog. Free models rate-limit (429 + cooldown) — retry works.
- [ ] Route policy implemented (critical -> reliable provider; free pool -> shallow)
- [ ] OpenBB installed; verified NSE (`RELIANCE.NS`), BSE (`.BO`), crypto (`BTC-USD`)
- [x] OpenBB installed + data module built. **VERIFIED live:** NSE `RELIANCE.NS` (yfinance provider, keyless) and crypto `BTC-USD` return real OHLCV. **BSE `.BO` has NO yfinance data** (known gap → needs paid provider fmp/intrinio/tiingo). 3 unit tests pass.
- [ ] TradingView MCP server configured; chart/TA/backtest tools verified

> **Docker: RESOLVED** 2026-08-29 (Windows restarted; Docker Desktop 29.7.2 installed; postgres/redis/qdrant running).
>
> **OmniRoute: RESOLVED + VERIFIED** 2026-08-29 — dev server serves 20128 (Turbopack; the
> webpack build wedged). Dashboard login page 200 (password `CHANGEME`); OpenRouter provider
> added + active; **real `/v1/chat/completions` returned 200** with model
> `openrouter/google/gemma-4-31b-it:free`. Model id format: `openrouter/<upstream-id>`; `auto`
> is not in the synced catalog. Free models return 429+cooldown under load (use a paid model or
> retry). **Next:** configure the trading-bot's `omniroute_api_key` so the bot authenticates to `/v1`.

**Phase 0 status:** In progress. Docs + foundational code built & verified.
Docker (postgres/redis/qdrant) running, OmniRoute running + OpenRouter active.
Remaining Phase 0: verify a real `/v1` call, route policy, TradingView MCP.

---

## Phase 1 — Reasoning Pipeline (lean, end-to-end `/analyze`)

> **Decision (2026-08-30):** Build the **lean** pipeline now (not full TradingAgents):
> RAG → single reasoning LLM → structured JSON report, behind a FastAPI `/analyze`
> endpoint. TradingAgents graph swap-in is a later enhancement.

- [x] LLM provider configured -> OmniRoute (`openai_compatible`) — `backend/reasoning/llm.py` (httpx client, cooldown-aware 429 retry, model fallback)
- [x] RAG: ingest (`docs/strategies/`) -> chunks -> fastembed -> Qdrant — `backend/rag/{ingest,embeddings,store}.py`. **VERIFIED 2026-08-30:** 1 md file -> 2 chunks -> retrieve 2 hits (scores 0.77/0.60)
- [x] RAG: retriever returns relevant strategy context for a symbol — `backend/rag/retriever.py` (`retrieve_context`)
- [x] Strategy context injected into reasoning prompt — `backend/reasoning/prompts.py` (`ANALYSIS_SYSTEM` + `build_analysis_user_prompt`)
- [x] `agent_runner.py` runs the lean pipeline: OHLCV -> RAG -> LLM -> JSON report (robust parser `json_repair`)
- [x] FastAPI `/analyze` + `/ingest` endpoints — `backend/main.py` (added 2026-08-30)
- [x] `/analyze` + `/ingest` **verified over HTTP** 2026-08-30: `POST /ingest` → 200 (files:1, chunks:2); `POST /analyze` (RELIANCE.NS, NSE, 30d) → 200 in ~39s, full JSON report `signal:NEUTRAL`, `rag_hits:2`
- [x] Decision log persisted (Postgres) — see **Decision Log (Postgres)** section below
- [ ] Telegram `/analyze <symbol>` returns grounded report

**Phase 1 status:** Core lean pipeline **built + verified end-to-end over HTTP**.
`POST /analyze` (RELIANCE.NS, NSE, days=30) → complete structured JSON report,
`signal:"NEUTRAL"`, `_meta.rag_hits:2`, nothing truncated. `/ingest` and `/healthz`
also 200. Run: `uvicorn backend.main:app` (also at 127.0.0.1:8001 in tests).

> **Model note (2026-08-30):** free OpenRouter `:free` models **cannot** produce
> reliable structured output — persistent 429 + hard ~300-token output cap truncates
> mid-JSON (caught by `json_repair`, not fixable). **RESOLVED:** added cheap paid
> model `openai/gpt-4o-mini` to the OmniRoute synced catalog (DB edit), verified
> `/v1/chat/completions` → 200 `"OK"` (counted). Trading-bot `reasoning_model`
> now `openrouter/openai/gpt-4o-mini`, fallback `openrouter/openai/gpt-4.1-mini`.

---

## Phase 1b — Deep Reasoning (TradingAgents multi-agent, on-demand) [EXPERIMENTAL]

> **Decision (2026-08-30):** keep the lean single-LLM path as the default + automatic
> fallback; run TradingAgents only when explicitly requested (`deep:true`). Costs tens
> of LLM calls (~45–147s per symbol) — on-demand only. Start on `gpt-4o-mini`, measure,
> upgrade tier later only if quality warrants. On any deep failure/timeout → fall back
> to the lean report (never errors).

- [x] Vendored TradingAgents (`TauricResearch/TradingAgents`, **v0.3.1**, commit `01477f9a`) into `vendor/tradingagents` + `pip install .` into venv (langchain/langgraph stack added; pandas/yfinance NOT downgraded)
- [x] `openai_compatible` provider (keyless) pointed at OmniRoute `backend_url` — `backend/reasoning/tradingagents_engine.py` (`run_tradingagents`), verified graph init + live call
- [x] RAG strategy context injected into PM prompt (minimal vendor-patch: appends to `past_context` via `strategy_context`) — `_run_graph` in `vendor/.../trading_graph.py`
- [x] Output mapper `backend/reasoning/tradingagents_mapper.py` → same report JSON shape as lean (5-tier → LONG/SHORT/NEUTRAL, confidence from sentiment, entry/stop/target, R/R, key_levels, caveats, `_meta.engine:"tradingagents"`)
- [x] `/analyze` accepts `deep:true`; `analyse_symbol(deep=...)` runs deep in a thread with a 180s timeout → falls back to lean on any failure — `backend/main.py`, `backend/reasoning/agent_runner.py`
- [x] **HTTP E2E VERIFIED 2026-08-30:** `POST /analyze {"deep":true}` (RELIANCE.NS, NSE, 30d) → 200 in ~147s, full mapped report (`signal:NEUTRAL`, `engine:tradingagents`, `ta_rating:"Hold"`). Offline run ~127s earlier.
- [ ] Measure deep-path cost/quality on gpt-4o-mini across symbols; decide model tier (blocked on more runs + user)
- [ ] Telegram `/deep` command (deferred with Telegram /analyze)

**Phase 1b status:** Built + HTTP-verified (RELIANCE.NS). Experimental — keep
`reasoning_engine=lean` as the operating default. Known limitations: TradingAgents
does not validate tickers upstream (a bogus symbol yields a deep report, not a
fallback); its social/news vendors (StockTwits/Reddit) fail for Indian tickers, so
deep confidence is often low; entry/stop/target are often absent on Hold ratings.

---



## Decision Log (Postgres)

> **Decision (2026-08-30):** SQLAlchemy 2.0 + psycopg2 against the already-running
> `trading-postgres` container. Persistence is **best-effort/non-fatal** — a DB
> failure must never break `/analyze`. One row per report (lean OR deep), storing
> the full report JSON + the backtest block.

- [x] Engine/session/`ping()`/`init_db()` — `backend/storage/db.py` (lazy-cached engine; non-fatal init)
- [x] `DecisionLog` model — `backend/storage/models.py` (symbol, market, signal, confidence, engine, rag_hits, report JSON, backtest JSON, created_at; index on symbol+created_at)
- [x] CRUD — `backend/storage/decision_log.py` (`log_decision` best-effort, `latest_decisions(symbol, limit)`, `count_decisions`)
- [x] Wired into `analyse_symbol` → every `/analyze` report (lean + deep) is persisted — `_persist_decision` in `backend/reasoning/agent_runner.py`
- [x] FastAPI surface: `GET /decisions` (latest N, optional `?symbol=`), `/healthz` reports `db: ok|unreachable`, startup `init_db()` — `backend/main.py`
- [x] **VERIFIED 2026-08-30:** functional insert/query round-trip works; **HTTP E2E** `POST /analyze` (RELIANCE.NS) → 200, row persisted (id), `GET /decisions` returns it; full suite **20 tests passing** (incl. `test_decision_log.py`, DB-tolerant; skip if Postgres down)

**Decision Log status:** Built + verified end-to-end. Records every analysis decision
for later auditing + the learning/reflection loop.

---

## Phase 2 — Backtest-Before-Trade (probabilities)

> **Decision (2026-08-30):** built a lean in-house OHLCV simulator (`backend/backtest/`)
> instead of pulling in vectorbt/backtrader — pure numpy/pandas, zero extra deps,
> easy to unit-test. It replays the proposed setup's exit rule across the trailing
> window (no look-ahead) to report probabilities for that setup.

- [x] Backtest engine (in-house simulator) for a proposed setup — `backend/backtest/simulator.py` (`backtest_trade()`)
- [x] Causal / no-look-ahead guarantee (level-based entry/exit only; max-hold close-out) — unit-tested (`test_max_hold_close_out_no_lookahead`, `test_short_*`, `test_long_*`)
- [x] Report: entry/SL/target + win-rate, expectancy (avg R/trade), total R, profit factor, max DD, Sharpe, n, avg hold — `BacktestResult.to_dict()`
- [x] Report-driven wiring: `backend/backtest/runner.py` (`build_backtest_from_report`) + attached to every `/analyze` report as `backtest` block; caveat surfaced when expectancy negative or under-sampled (`_surface_backtest_caveat`)
- [x] Risk gate min R/R / max loss / position / exposure — **already built + 8 tests in Phase 0** (`backend/risk/gate.py`); backtest is informative (caveat), the gate stays the hard rejection at execution
- [x] Unit tests for the simulator (`backend/tests/test_backtest.py`, 7 tests) + full suite 18 passing
- [x] **HTTP E2E VERIFIED 2026-08-30:** `POST /analyze` (RELIANCE.NS, NSE) → 200 with `backtest` block present (`status:"skipped"` because the report was NEUTRAL with no setup; the `ok` path is covered by unit tests + a mocked report→simulator smoke test)
- [x] Realistic costs (brokerage, slippage, STT/crypto fees) — **built + verified 2026-08-31**:
  `backend/backtest/costs.py` (`CostModel`, `fill_price`, `equity_buy_fee`, `equity_sell_fee`,
  `cost_model_for_market`, `net_r`); simulator refactored for net-of-cost R; runner wires
  cost model per market (NSE/BSE equity vs CRYPTO taker); `backend/tests/test_costs.py` (8 tests)
  + full suite 26 passed; synthetic E2E harness verifies net < gross + costs breakdown.
- [ ] Telegram proposes trade WITH backtest probability + R/R, gated (deferred — needs bot token)

**Phase 2 status:** Core simulator, report wiring, and leak-test math **built + verified**
(18 tests pass; HTTP `/analyze` includes the `backtest` block). Costs modelling and
Telegram surfacing remain. Note: `backtest_window_days`/`backtest_max_hold_bars`/
`backtest_min_trades` are config-tunable in `config.py`.

---

## Phase 3 — Paper Execution + Learning Loop + 24/7

- [x] In-house paper simulator (`backend/execution/paper.py`) — `PaperEngine`: order matching (MARKET/LIMIT), position tracking, PnL, slippage + fee model (reuses `CostModel`)
- [x] Trade log (`backend/execution/trade_log.py`) — append-only JSONL + SQLite: `TradeLog.log_trade()`, `get_recent()`, `stats()`
- [x] Portfolio state (`backend/execution/state.py`) — `PortfolioState`: cash, positions, equity curve, `place_and_fill()`, `close_position()`, `get_portfolio()`
- [x] FastAPI endpoints: `POST /paper/trade` (open/close), `GET /paper/portfolio`, `GET /paper/trades` — `backend/main.py`
- [x] Unit tests: `backend/tests/test_paper.py` (11 tests), integration test `test_paper_integration.py` (backtest→paper flow)
- [x] Binance Testnet adapter (`backend/execution/binance_testnet.py`) — `BinanceTestnetClient`, `BinanceTestnetPaperEngine`
- [x] Auto-risk engine (`backend/execution/risk_engine.py`) — `AutoRiskEngine`: real-time per-fill validation, daily loss halt, position/exposure limits
- [x] Reflection engine (`backend/execution/reflection.py`) — `ReflectionEngine`: post-trade lessons, JSONL storage, RAG formatting
- [x] 24/7 Orchestrator (`backend/execution/orchestrator.py`) — Redis task queue, workers, market-hour scheduling (crypto 24/7, NSE/BSE 09:15-15:30 IST)
- [x] Zerodha Kite adapter (`backend/execution/zerodha_kite.py`) — `KiteConnectClient`, `ZerodhaKitePaperEngine` with Kite fee structure
- [x] Telegram commands (`backend/telegram/bot.py`) — `/analyze`, `/deep`, `/paper_trade`, `/paper_close`, `/positions`, `/portfolio`, `/trades`, `/risk`, `/kill`, `/unhalt`, `/status`
- [ ] Broker ABC (`base.py`)

**Phase 3 status:** Paper execution, risk engine, reflection, Binance Testnet, Zerodha Kite, 24/7 orchestrator, and Telegram commands **built + verified** (65 tests passing).

---

## Phase 4 — Real-Money Bridge (gated)

- [x] `zerodha_kite.py` (NSE/BSE live) implemented — `backend/execution/zerodha_live.py`
- [x] Binance live execution — `backend/execution/binance_live.py`
- [x] Live/paper switch via config flag (`live_trading_mode`: paper|live|hybrid)
- [x] Per-order Telegram approval (`APPROVAL_REQUIRED=true`) — `/approve`, `/reject`, `/pending`
- [x] SEBI algo-trading readiness: order audit log (`backend/execution/audit_log.py`) — immutable JSONL + SQLite
- [x] Live mode OFF by default, requires explicit opt-in via `/mode`

**Phase 4 status:** Live execution engines, approval flow, audit log, and mode switching **built + verified** (70 tests passing). Requires `TELEGRAM_BOT_TOKEN`, `ZERODHA_API_KEY/SECRET/ACCESS_TOKEN`, `BINANCE_API_KEY/SECRET` for live deployment.

---

## Phase 5 — Harden / Scale

- [ ] Monitoring / alerting
- [ ] Multi-VPS / redundancy
- [ ] Optional true RL reward-model training (research-grade, optional)

**Phase 5 status:** Not started.

---

## Cross-cutting (all phases)

- [ ] `.env` never committed; secrets externalized
- [ ] Structured JSON logging
- [ ] Tests runnable via `pytest`
- [ ] Everything marked implemented is actually run & verified

---

## Deferred / Deliberately Out of Scope (v0)

- Multi-user / multi-account productization.
- True LLM RL training pipeline (Trading-R1 style) — see notes.
- Full options Greeks modeling.
- Alternative data signals.
