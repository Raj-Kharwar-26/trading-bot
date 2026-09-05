# PRD — Product Requirements Document

## 24/7 AI Trading System (NSE/BSE + Crypto)

**Product owner:** User (trader)
**Status:** v0 draft — requirements capture
**Last updated:** 2026-08-28

---

## 1. Problem Statement

A retail trader wants a backend system that trades 24/7 across Indian equity
(NSE/BSE) and cryptocurrency markets, driven by an AI that:

- Is trained/steered by the trader's own strategy documents (PDFs, research)
- Learns from its own mistakes over time
- Reads trading charts and analyzes them
- Designs trades with a minimal, favorable risk-to-reward ratio
- Backtests each proposed trade in real time and reports probabilities
- Communicates entirely through Telegram (user sends a message, system replies)
- Uses "unlimited" AI via an aggregator gateway (OmniRoute)

## 2. Goals

1. A single place (Telegram) where the user commands the system and receives
   trade analyses, backtest probabilities, positions, and P&L.
2. Autonomous 24/7 scanning and signaling.
3. Backtest-before-trade: every live proposal carries a historical probability
   estimate.
4. Strategy-aware reasoning guided by the user's own documents (RAG).
5. Learning from outcomes (reflection + outcome-feedback loop).
6. Rigorous, code-enforced risk controls with hard limits.
7. Paper trading first; real money only behind explicit gates.

## 3. Non-Goals (v0)

- Not building a full LLM research training pipeline (Trading-R1 style RL) in v0.
- Not providing financial advice; system is an assistant to the trader.
- Not promising risk-free profit or guaranteeing any strategy will win.
- Not implementing every possible exchange/broker in v0.
- Not replacing the trader's judgment — approvals gate real-money trades.

## 4. Personas

- **Primary:** The trader (single user). Interacts via Telegram bot.
- **Secondary (later):** Themselves running multiple accounts/markets.

## 5. User Stories / Core Scenarios

| ID | As a trader I want to... | So that... | Priority |
|----|--------------------------|------------|----------|
| US1 | Send `/analyze RELIANCE` on Telegram | I get a full multi-agent analysis | P0 |
| US2 | See backtest probability + R/R for a proposed trade | I can judge risk before acting | P0 |
| US3 | Have the system scan markets 24/7 for setups | I don't miss opportunities | P1 |
| US4 | Have the system paper-trade its own signals | I validate it without real risk | P1 |
| US5 | Have the system remember past trades and their outcomes | It learns from mistakes | P1 |
| US6 | Upload my strategy PDFs so the system follows them | The system trades my way, not generically | P1 |
| US7 | Have hard risk limits enforced automatically | I never blow up the account | P0 |
| US8 | Approve real-money orders via Telegram | I keep control over live capital | P2 |

## 6. Functional Requirements

### FR-1 Telegram Front-End
- FR-1.1 Bot responds to commands: `/help`, `/analyze <symbol>`, `/positions`,
  `/trade <symbol>`, `/summary`, `/risk`, `/strategy`, `/backtest <symbol>`.
- FR-1.2 Bot replies with human-readable, formatted reports.
- FR-1.3 Bot can send proactive alerts (trade signals, risk breaches, fills).

### FR-2 Multi-Agent Reasoning (TradingAgents core)
- FR-2.1 Run a multi-agent analysis (fundamental, sentiment, news, technical;
  bull/bear debate; trader; risk; portfolio manager).
- FR-2.2 Inject retrieved strategy context from the RAG knowledge base.

### FR-3 Knowledge Base / RAG
- FR-3.1 Ingest PDFs/docs from `docs/strategies/` into a vector store.
- FR-3.2 Retrieve relevant strategy excerpts for a given symbol/setup.
- FR-3.3 Re-ingest when documents are added or changed.

### FR-4 Backtest-Before-Trade
- FR-4.1 For a proposed trade, run a historical backtest of that setup.
- FR-4.2 Report: entry, stop-loss, target, R/R ratio, win-rate, expectancy,
  max drawdown, sample count, Sharpe.
- FR-4.3 Backtest must be causal (no look-ahead) and include realistic costs.

### FR-5 Risk Engine
- FR-5.1 Enforce hard limits: max position size, max daily loss, min R/R,
  max # concurrent positions, max exposure.
- FR-5.2 Reject proposed trades that violate limits before execution.
- FR-5.3 Provide a kill switch to halt all activity.

### FR-6 Execution
- FR-6.1 Paper execution via broker testnets / in-house simulator.
- FR-6.2 Broker abstraction so live vs paper is a config flag.
- FR-6.3 (Later) NSE/BSE live via Zerodha Kite Connect, gated.

### FR-7 Learning / Memory
- FR-7.1 Log every closed trade with realized P&L.
- FR-7.2 Generate a reflection after each outcome.
- FR-7.3 Feed lessons back into future analyses (RLAIF-style loop).

### FR-8 AI Routing (OmniRoute)
- FR-8.1 All LLM calls go through the OmniRoute gateway.
- FR-8.2 Critical decision calls route to a reliable provider; shallow tasks
  may use the free pool. Config-driven.

## 7. Non-Functional Requirements

- **Availability:** 24/7 scanning (scheduler + heartbeat); graceful recovery.
- **Reliability:** State persisted (Postgres); task queue (Redis); retries.
- **Safety:** All risk limits enforced in code, not just in prompts.
- **Observability:** Structured logs; trade log; decision log.
- **Security:** Broker/API secrets in `.env`, never committed.
- **Testability:** Backtests are deterministic and reproducible.
- **Cost:** Prefer free/cheap AI tiers; track token spend.

## 8. Success Metrics

- End-to-end `/analyze` returns in reasonable time with a grounded report.
- Every proposed trade includes a backtest probability + R/R.
- Risk gate rejects every violating setup (unit-tested).
- Paper-trading loop closes real (simulated) trades and logs outcomes.
- Learning loop demonstrably changes future analysis based on outcomes.

## 9. Known Risks / Honest Constraints

1. **"Unlimited AI"** — OmniRoute free tiers are real but quota/rate-limited and
   may be unreliable for strict structured JSON. Critical decisions use a
   stable provider.
2. **TradingView has no official paper-trading API.** MCP gives charts/TA/
   backtest only. Paper *execution* uses broker testnets.
3. **LLM "learning"/RL** — true RL (Trading-R1 style) is research-grade and
   compute-heavy. v0 uses the practical reflection + outcome-feedback loop.
4. **NSE/BSE live** requires SEBI algo registration + static IP (2026 rules).
   Paper trading unaffected.
5. Markets are inherently unpredictable; backtest win-rates are estimates, not
   guarantees.

## 10. Success Criteria for Phase Completion
- Phase 0: repo + Docker + OmniRoute + data verify + Telegram skeleton.
- Phase 1: `/analyze` returns a grounded multi-agent report on Telegram.
- Phase 2: proposed trade includes backtest probability + R/R, gated.
- Phase 3: paper trading loop + learning loop + 24/7 scheduler.

## 11. Out of Scope (future)
- Multi-user / multi-account productization.
- Full on-chain options Greeks modeling.
- Alternative data (satellite/credit-card) signals.
- Mobile/desktop dashboards beyond Telegram.
