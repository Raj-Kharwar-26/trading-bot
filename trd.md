# TRD — Technical / Test Requirements Document

**Status:** v0 draft — target technical & test requirements
**Last updated:** 2026-08-28

> This defines *target* technical and testing requirements. It is a spec, not a
> record of shipped code. See `implementation.md` for actual build status.

---

## 1. Python Environment

- Python 3.12.
- Dependency management: `pip` with a `pyproject.toml`/`requirements.txt`.
- A `virtualenv` under `backend/.venv`.

## 2. Configuration

- `.env` for all secrets: Telegram token, OmniRoute URL/key, provider keys,
  broker/paper keys, Postgres/Redis DSNs.
- `.env.example` committed; `.env` git-ignored.
- Pydantic `Settings` for typed config and risk limits.

## 3. Data Layer

- OpenBB installed and verified for:
  - NSE equity (e.g. `RELIANCE.NS`), BSE (`.BO`), and crypto (`BTC-USD`).
- TradingView MCP server registered and returns chart/TA/backtest data.
- Historical data cached to Postgres to avoid rate-limit problems.

## 4. AI Routing

- OmniRoute reachable at `http://localhost:20128/v1`.
- Reasoning core uses `openai_compatible` provider → OmniRoute.
- Route policy: reliable provider for critical decisions; free pool for shallow
  tasks. Structured outputs always parsed with retries + validation.

## 5. Backtesting Correctness

- **No look-ahead:** signals computed from data available up to the trade bar.
- **Realistic costs:** brokerage, slippage, STT (India), crypto fees.
- **Metrics:** win-rate, expectancy, R/R, max drawdown, Sharpe, sample count.
- **Reproducible:** fixed seed / deterministic where applicable.
- Reject strategies with too few samples (statistical significance).

## 6. Risk Gate (hard, code-enforced)

Config-driven limits (defaults shown; trader can change):
| Limit | Default |
|-------|---------|
| Max loss per trade | 1% of equity |
| Max daily loss | 3% of equity (halts trading) |
| Min R/R ratio | 1:1.5 |
| Max position size | 5% of equity per symbol |
| Max concurrent positions | 10 |
| Max total exposure | 30% of equity |

Risk gate is unit-tested: every violating proposal is rejected.

## 7. Execution

- Broker ABC interface (place/close/positions/funds).
- Paper execution default: `paper_sim.py` + Binance Testnet.
- Live execution OFF by default; requires `APPROVAL_REQUIRED=true` and per-order
  Telegram confirmation.

## 8. Testing Strategy

- **Unit tests (pytest):** risk gate, R/R math, backtest causality (peek test),
  formatters, parsers.
- **Integration tests:** OpenBB fetch, TradingView MCP tool, OmniRoute route,
  paper broker fill/P&L.
- **Leak test:** assert a backtest using only past bars never references future
  data (shift check).
- **CI-lite:** `pytest` must pass before Phase gates are considered complete.

## 9. Observability

- Structured JSON logs (stdout) + file logs.
- Decision log (every agent decision persisted).
- Trade log (every fill/close with P&L).
- Health endpoint `/healthz`; on-call kill switch.

## 10. Phase Test Gates

| Phase | Required proof |
|-------|----------------|
| 0 | Docker services up; OmniRoute responds; OpenBB fetch returns data; TradingView MCP returns TA; Telegram bot confirms `/help`. |
| 1 | `/analyze RELIANCE` returns a grounded multi-agent report on Telegram. |
| 2 | Proposed trade includes backtest probability + R/R; risk gate rejects violations; leak test passes. |
| 3 | Paper trade opens→closes with logged P&L; a reflection is generated; scheduler runs unattended. |

## 11. Definition of Done (general)

- Code runs without fabricated placeholders.
- Any "not yet built" item is explicitly marked in `implementation.md`, never
  silently claimed.
- `.env` is never committed.
