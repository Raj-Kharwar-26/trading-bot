# Running the Trading Bot — Step-by-Step

This guide explains how to get the Telegram-operated trading bot up and running
end-to-end on **Windows (PowerShell)**, using **OmniRoute** as the LLM gateway,
**Docker** for Postgres/Redis/Qdrant, and **Binance Testnet** for paper crypto.

> This is the runbook for the whole project. If you only care about chatting
> with the bot over Telegram, jump to **Section 5**.

---

## 1. Prerequisites

| Tool | Purpose | Check |
|------|---------|-------|
| Python 3.12 | App runtime | `python --version` |
| Node.js / npm | OmniRoute (global npm install) | `npm --version` |
| Docker Desktop | Postgres, Redis, Qdrant | `docker --version` |
| Telegram | Bot credentials + chat client | n/a |

The project already ships a Python virtualenv at `backend/.venv`. Recreate it
only if missing:

```powershell
cd D:\Projects\trading-bot
python -m venv backend/.venv
backend/.venv\Scripts\pip install -e .
```

---

## 2. Environment variables

All config lives in `.env` at the **project root** (`D:\Projects\trading-bot\.env`).
`backend/core/config.py` resolves this file automatically (do NOT rely on CWD).

Copy the template first if yours is missing:

```powershell
cd D:\Projects\trading-bot
Copy-Item .env.example .env
```

Key variables:

| Var | What it is |
|-----|-----------|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `OMNIROUTE_BASE_URL` | `http://localhost:20128/v1` |
| `REASONING_MODEL` | Primary LLM (e.g. `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`) |
| `REASONING_MODEL_FALLBACK` | Fallback LLM (e.g. `openrouter/minimax/minimax-m3:free`) |
| `PAPER_ENABLED` | `true` = paper trading on |
| `LIVE_TRADING_ENABLED` | Keep `false` unless you want real money |
| `BINANCE_TESTNET_API_KEY` / `_SECRET` | Binance Testnet credentials (paper crypto) |
| `ALLOWED_TELEGRAM_USER_IDS` | Empty = all users allowed (no auth gate) |

> **Secrets warning:** never commit `.env`. It is git-ignored.

---

## 3. Start the infrastructure (Docker)

Postgres (decision log), Redis (orchestrator queue) and Qdrant (RAG vector
store) run as containers. Start them once:

```powershell
cd D:\Projects\trading-bot
docker compose up -d
```

Verify:

```powershell
docker ps
# trading-postgres (healthy), trading-redis (healthy), trading-qdrant
```

Check Qdrant health:

```powershell
Invoke-WebRequest http://localhost:6333/healthz
# -> 200
```

---

## 4. Start OmniRoute (the LLM gateway)

OmniRoute proxies requests to OpenRouter/other providers. It is installed
globally via npm. Start it (keeps running in the terminal):

```powershell
omniroute serve --no-open
```

> Default port is **20128**. You can run it as a background daemon with
> `omniroute serve --no-open --daemon`.

Verify it is up:

```powershell
Invoke-WebRequest http://localhost:20128/healthz
# -> 200 ok
```

If the OpenRouter provider is not active yet, open `http://localhost:20128`
and add/activate it (API key + model list). Model IDs use the form
`openrouter/<upstream-id>` (including the `:free` suffix where applicable).

> **Do not skip this step.** The bot cannot run `/analyze` without OmniRoute.

---

## 5. Start the Telegram bot

Run a **single** poller. (Running more than one causes `409 Conflict` —
see troubleshooting.)

```powershell
cd D:\Projects\trading-bot
Start-Process backend/.venv/Scripts/python.exe -ArgumentList "-m","backend.telegram.bot" -WorkingDirectory (Get-Location) -RedirectStandardOutput "bot_out.log" -RedirectStandardError "bot_err.log"
```

Or in the foreground:

```powershell
backend/.venv/Scripts/python.exe -m backend.telegram.bot
```

Verify it is polling (no conflicts) — the log should show only
`getUpdates ... 200 OK`:

```powershell
Get-Content bot_err.log -Tail 5
```

Confirm exactly one bot process:

```powershell
Get-CimInstance Win32_Process | Where-Object {$_.Name -eq "python.exe" -and $_.CommandLine -like "*backend.telegram.bot*" } | Select-Object ProcessId
```

---

## 6. Ingest strategy documents (RAG)

Strategy docs (`.md`, `.txt`, `.pdf`) in `docs/strategies/` are embedded into
Qdrant for retrieval. Re-run whenever you add/change a document:

```powershell
cd D:\Projects\trading-bot
backend/.venv/Scripts/python.exe -c "from backend.rag.ingest import ingest_strategy_docs; print(ingest_strategy_docs(force_rebuild=True))"
```

> On low-RAM machines, ingestion is slow and memory-hungry. The embedding is
> already configured to run in small serial batches to avoid OOM. Run it in a
> terminal and wait for the summary (e.g. `{"files":5,"chunks":247,...}`).

---

## 7. Start the FastAPI server (optional)

The app also exposes a REST API (`/analyze`, `/ingest`, `/healthz`). Start with
Uvicorn (in the venv):

```powershell
cd D:\Projects\trading-bot
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Verify:

```powershell
Invoke-WebRequest http://localhost:8000/healthz
```

---

## 8. Using the bot in Telegram

Chat with your bot in Telegram. All commands work once the bot + OmniRoute +
Postgres/Qdrant are up.

| Command | Action |
|---------|--------|
| `/help` | Show command help |
| `/ping` | Health check (replies `🏓 pong`) |
| `/analyze <symbol> [market]` | Lean AI analysis (signal, entry/SL/target, backtest) |
| `/deep <symbol> [market]` | Deep multi-agent analysis (slow, experimental) |
| `/paper_trade <sym> <LONG\|SHORT> <qty> <price> [market]` | Open a paper position |
| `/paper_close <sym> <LONG\|SHORT> <price> [market]` | Close a paper position |
| `/positions`, `/portfolio`, `/trades` | Paper account positions / P&L / history |
| `/risk`, `/kill`, `/unhalt`, `/status` | Risk view, halt, resume, system status |
| `/mode <paper\|live\|hybrid>` | Switch trading mode |
| `/live_trade ... /approve <id> /reject <id> /pending` | Live-order approval flow |

`market` is one of `NSE`, `BSE`, `CRYPTO` and defaults to `NSE`.

### Paper crypto on Binance Testnet

Set `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`, then:

```
/analyze BTC-USD CRYPTO     -> check for a clean setup (entry/SL/target + backtest)
/paper_trade BTC-USD LONG 0.001 75000 CRYPTO   -> places order + mirrors to Testnet
```

The confirmation line shows `🧪 Binance Testnet: <status> (id <num>)` when the
order was also placed on Testnet.

> **Safety:** `LIVE_TRADING_ENABLED=false` and mode `paper` keep everything
> simulated. Real money requires setting `LIVE_TRADING_ENABLED=true`, real
> Binance/Kite keys, and explicit per-order approval.

---

## 9. Verification checklist

Run these in order and confirm each "ok":

1. `docker ps` — postgres, redis, qdrant up.
2. `Invoke-WebRequest http://localhost:20128/healthz` → `200 ok` (OmniRoute).
3. `Invoke-WebRequest http://localhost:6333/healthz` → `200` (Qdrant).
4. Exactly **one** bot process, logging `getUpdates ... 200 OK` (no 409).
5. `/ping` in Telegram → `🏓 pong`.
6. `/analyze BTC-USD CRYPTO` → full JSON-backed report (no "data not supplied").

---

## 10. Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `409 Conflict` on `getUpdates` | Two bot pollers running. Kill all, start exactly one. |
| `/analyze` says model rate-limited / 502 | Free `:free` models are flaky. The fallback model should kick in; retry `/analyze`. |
| "reasoning output was not JSON" | Free model ignored the JSON prompt. Code now auto-retries via the fallback model; re-run if needed. |
| `data not supplied` caveat | Indicator summary is missing fields. Re-fetch: the code now supplies EMA-50 slope, 20-day volume ratio, ATR(14). |
| Embedding OOM during ingest | Low RAM. Ingestion is serial/small-batch; close heavy apps and re-run (Section 6). |
| `/analyze` hangs / no reply | OmniRoute down. Start it (Section 4) and re-send. |
| Binance Testnet order rejected | Keys wrong/revoked or symbol not spot-tradeable on Testnet. Regenerate keys at testnet.binance.vision. |

---

## 11. Project layout (quick map)

```
backend/
  core/config.py        Typed settings (reads project root .env)
  telegram/bot.py       Telegram commands + poller
  reasoning/            Lean + deep (TradingAgents) analysis, LLM client
  rag/                  Embedding + Qdrant ingest/retrieve
  execution/            Paper/live engines (Binance, Zerodha), risk, trade log
  data/market.py        OpenBB/yfinance OHLCV
docs/strategies/        Strategy documents (ingested into RAG)
.env                    Runtime configuration
```