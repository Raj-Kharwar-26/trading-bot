# DRD — Design / Architecture Requirements Document

**Status:** v0 draft — target architecture
**Last updated:** 2026-08-28

> This describes the *target* design. It is a plan, not a record of shipped code.
> See `implementation.md` for what is actually built. Nothing in here is "false
> code" — where a component exists, it is marked.

---

## 1. System Context

A backend system that:
- Receives Telegram commands (synchronous request/response).
- Runs an Asynchronous 24/7 scanner producing trade ideas.
- Uses a multi-agent LLM reasoning core.
- Backtests candidate trades.
- Enforces risk limits.
- Executes paper (and eventually live) orders.
- Learns from outcomes via a memory + reflection store.
- Routes all LLM traffic through the OmniRoute gateway.

## 2. High-Level Architecture (Target)

```
┌──────────────────────────────────────────────────────────────┐
│                    TELEGRAM (user interface)                 │
│   /analyze <sym> · /positions · /trade <sym> · /risk ...    │
└───────────▲──────────────────────────────────────────────────┘
            │
┌───────────┴──────────────────────────────────────────────────┐
│                 FASTAPI  (backend/main.py)                   │
│   Telegram webhook · REST API · background task entry        │
└───────────▲───────────────▲───────────────▲──────────────────┘
            │               │               │
            │        ┌──────┴──────┐  ┌─────┴─────────────────┐
            │        │ Orchestrator│  │ Risk Gate            │
            │        │ (scheduler, │  │ (hard limits, kill)  │
            │        │  tasks, gov)│  └──────────────────────┘
            │        └──────┬──────┘
            ▼               ▼
┌───────────────────────────────────────────────┐
│  REASONING ENGINE  (TradingAgents graph)      │
│   analysts → bull/bear → trader → risk → PM   │
│   + RAG strategy context + memory injection   │
└───────▲───────────────────────────▲───────────┘
        │                           │
        │  strategy context         │  past outcomes / lessons
┌───────┴──────────┐        ┌───────┴──────────────┐
│  RAG vector store│        │  MEMORY + trade log  │
└───────▲──────────┘        └───────────────────────┘
        │ ingest
┌───────┴──────────┐
│ docs/strategies/ │  (PDFs, research)
└──────────────────┘
        │
        ▼
┌───────────────────────────────────────────────┐
│  AI ROUTER  →  OmniRoute  (localhost:20128/v1)│
│   reliable provider (critical) + free pool    │
└───────────────────────────────────────────────┘
        ▲
        │   LLM calls
        │
┌───────────────────────────────────────────────┐
│  DATA + EXECUTION LAYER                       │
│   OpenBB (market data) · TradingView MCP      │
│    (charts/TA/backtest) · broker adapters     │
│     (paper sim, Binance Testnet, Zerodha)     │
└───────────────────────────────────────────────┘
```

## 3. Component Design

### 3.1 Telegram Bot (`backend/telegram/`)
- Uses `python-telegram-bot` (or HTTP webhook).
- Command router → dispatches to orchestrator/tasks.
- Formatters produce readable text reports from structured results.

### 3.2 Orchestrator (`backend/orchestrator/`)
- **scheduler.py:** heartbeat loop; schedules scans by market hours
  (crypto 24/7; NSE/BSE 09:15–15:30 IST).
- **tasks.py:** discrete jobs (analyze, backtest, scan, close, reflect).
- **governance.py:** approval gates, budgets, kill switch, concurrency.

### 3.3 Reasoning Engine (`backend/reasoning/`)
- **agent_runner.py:** wraps `TradingAgents` graph as a library.
  Uses `openai_compatible` LLM provider pointed at OmniRoute.
- **strategy_loader.py:** pulls RAG context + memory into prompts.

### 3.4 RAG (`backend/rag/`)
- **ingest.py:** PDF/doc → text → chunk → embed → vector store.
- **retriever.py:** top-k relevant strategy excerpts for a symbol/setup.
- Vector store: Chroma/pgvector/Qdrant via Docker. Embeddings via router.

### 3.5 Backtest (`backend/backtest/`)
- Engine based on vectorbt/backtrader (vectorized, fast).
- Strictly causal: signals use only info up to the trade bar (no look-ahead).
- Applied costs: brokerage, slippage, STT (India), exchange fees (crypto).

### 3.6 Risk Gate (`backend/risk/gate.py`)
- Hard numeric limits from config: max position %, max daily loss, min R/R,
  max concurrent positions, max total exposure.
- Reject before execution. Every fill revalidated.
- Kill switch aborts all live activity.

### 3.7 Execution (`backend/execution/`)
- **base.py:** `Broker` ABC — `place_order`, `close_position`, `positions`, `funds`.
- **paper_sim.py:** in-house paper broker (matches fills, tracks P&L).
- **binance_testnet.py:** Binance Spot/Futures Testnet paper execution.
- **zerodha_kite.py:** NSE/BSE via Kite Connect (LIVE — gated, later).

### 3.8 Memory / Learning (`backend/memory/`)
- **trade_log.py:** persisted realized P&L per closed trade.
- **reflection.py:** LLM generates "what worked/what failed" on close.
- **learning.py:** outcome-feedback loop updates memory injected into prompts.
- Storage: Postgres + decision log file (extends TradingAgents' own memory).

### 3.9 Data (`backend/data/market.py`)
- OpenBB for OHLCV + fundamentals across NSE/BSE/crypto.
- TradingView MCP for charts/TA/backtest tooling.

## 4. Data Model (Postgres)
- `trades` (id, symbol, market, side, entry, sl, target, qty, status, pnl, ...)
- `signals` (id, symbol, timestamp, agent_decision, risk_verdict, ...)
- `decisions` (id, symbol, date, full_decision_json, ...)
- `reflections` (id, trade_id, lesson, generated_at)
- `strategies_docs` (id, filename, chunk, embedding_ref, ingested_at)

## 5. Tech Stack
- Python 3.12 · FastAPI · uvicorn
- TradingAgents (TauricResearch) · OpenBB · vectorbt
- OmniRoute (AI gateway) · python-telegram-bot
- Postgres · Redis · Chroma/Qdrant (Docker)
- Binance python SDK (Testnet) · pykiteconnect (Zerodha, later)

## 6. AI Routing Policy
- **Critical path (structured decision / risk):** reliable paid provider
  (explicitly configured), retries, strict JSON schema.
- **Shallow tasks (news summary, sentiment):** OmniRoute free pool / cheap model.
- Rule enforced in `reasoning/` so a structured parse can never silently fall
  to an unreliable route.

## 7. Security & Compliance
- Secrets in `.env` only; no secrets in code or committed files.
- SEBI algo trading (live NSE/BSE): static IP + algo registration + 2FA
  required from 2026. Wired for later; never enabled by default.
- Telegram approval required for live orders (`APPROVAL_REQUIRED=true`).

## 8. Deployment (Target)
- Local dev: Windows host, Docker Compose for services.
- Later: Linux VPS for 24/7 + static IP (needed for live NSE/BSE).
