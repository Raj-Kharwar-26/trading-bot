# Deploying the Trading Bot 24/7 on Render (FREE)

This runbook puts the Telegram bot online **around the clock for $0/month** so it
stops needing your PC. It runs one always-on Render **web service** (the only
free piece that stays awake) and points at free-tier external services for
Postgres, Redis and the Qdrant vector store.

> Read the whole file once before starting. Prefer the most thorough first
> read: getting the secrets right saves a redeploy cycle at the end.

---

## 1. Why this architecture (free-tier truth)

Render's free tier (2026):

- 🟢 **Free web services** exist, but spin down after **15 min without inbound
  traffic**. A free periodic ping ("heartbeat") keeps one awake.
- 🔴 **Background workers/`pserv` are NOT free** (~$7/mo each) — so the original
  `deployment/render.yaml` (pserv for bot/qdrant/redis) would cost ~$28/mo.
- 🔴 Free web services have an **ephemeral filesystem** (no persistent disk) and
  one workspace gets **750 free instance-hours/mo** (≈ one always-on service).
- 🔴 Free **Render Postgres expires after 30 days** → we use **Neon** instead.
- 🔴 An **OmniRoute container** would need a persistent disk + its own service →
  we point the bot **directly at the OpenRouter API** (OpenAI-compatible; same
  provider OmniRoute was proxying).

Resulting topology:

```
 Telegram ──(long poll)──► Render free WEB service (trading-telegram-bot)
                           ├─ uvicorn :PORT  ── /healthz  (health + heartbeat target)
                           ├─ telegram poller backend.run_cloud
                           ├─ POSTGRES_DSN  -> Neon free Postgres   (decision log)
                           ├─ REDIS_URL     -> Render free Key Value (orchestrator queue)
                           ├─ QDRANT_URL    -> Qdrant Cloud free cluster (RAG vectors)
                           └─ OMNIRUTE_BASE_URL -> https://openrouter.ai/api/v1
                              + OPENROUTER_API_KEY (your existing OpenRouter key)
```

`backend/run_cloud.py` runs FastAPI **and** the Telegram poller in one process so
a single web service is healthy, answerable, and kept awake by a heartbeat.

---

## 2. Prerequisites (accounts + keys)

Create these (all free, no card for the free tiers):

| Account / service | Purpose | Signup |
|---|---|---|
| GitHub | repo + Render deploys from it | github.com |
| Render | the bot host | render.com |
| Neon (Postgres) | decision log (persists past 30d) | neon.tech |
| Qdrant Cloud | RAG vector store (persists) | qdrant.io/cloud |
| Upstash or nothing | alternative Redis if you skip Render KV | upstash.com (optional) |
| OpenRouter | LLM API (you already have a key) | openrouter.ai |

**Your OpenRouter API key** is already configured locally in OmniRoute and was
verified working directly against `https://openrouter.ai/api/v1`:
`openai/gpt-4o-mini` returned a completion. You can re-print it anytime from
`https://openrouter.ai/settings/keys` (or decrypt it from `~/.omniroute` as we
did during setup). It must be set as the `OPENROUTER_API_KEY` secret on Render.

> The bot's `.env` is git-ignored. Real keys live in Render's secret vault, not
> in the repo.

---

## 3. Push the repo to GitHub (one-time)

The repo is already git-initialized with one commit (`f11f08e`). You push with
your own credentials:

```powershell
cd D:\Projects\trading-bot
git remote add origin https://github.com/<USER>/trading-bot.git
git push -u origin master
```

> Create the repo privately on github.com first (empty, no README/.gitignore).

---

## 4. Create the free external services

### 4a. Neon Postgres (decision log)
1. neon.tech → New project → region close to Render region (Singapore).
2. Copy the **connection string** (psql) → it looks like
   `postgresql://user:pass@ep-xxx.eu-central-1.aws.neon.tech/neondb`. Keep it
   for `POSTGRES_DSN`.

### 4b. Qdrant Cloud (RAG vectors)
1. qdrant.io/cloud → start a **free cluster** (0.5–1 GB).
2. Copy the cluster **URL** (https) and an **API key** from the dashboard →
   `QDRANT_URL` and `QDRANT_API_KEY`.

### 4c. Render Key Value (Redis, orchestrator queue only)
Render dashboard → **New + → Key Value** (free, in-memory 25 MB). Copy the
**internal connection string** → `REDIS_URL` (e.g.
`redis://default:...@<internal-host>:6379`). Losing it on a restart only clears
the task queue — nothing critical.

### 4d. (Interactive) account note on Regions
Keep Neon, Qdrant and Render in the **same region** (e.g. Singapore) to minimize
latency on `/analyze` data + LLM calls.

---

## 5. Deploy the Render Blueprint

1. Render dashboard → **New + → Blueprint** → connect your GitHub account and pick
   the `trading-bot` repo.
2. Render proposes one service definition from `deployment/render.yaml`
   (`trading-telegram-bot`, web, free, `backend/Dockerfile`).
3. **Create** → the image builds (3–8 min; installs the venv deps incl.
   fastembed, onnxruntime, RapidOCR, python-docx).

> The Dockerfile default build target is now `cloud`
> (`python -m backend.run_cloud`) which serves `/healthz` **and** answers
> Telegram. No workers/separate services are deployed.

---

## 6. Set the secrets (Render dashboard)

Open the service → **Environment**. Add (`sync: false` = filled by you):

| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | your BotFather token (same as local `.env`) |
| `ALLOWED_TELEGRAM_USER_IDS` | empty (allow all) or your comma-separated Telegram user ids |
| `OPENROUTER_API_KEY` | the verified key (see §2) |
| `POSTGRES_DSN` | Neon connection string (§4a) |
| `QDRANT_URL` | Qdrant Cloud cluster URL (§4b) |
| `QDRANT_API_KEY` | Qdrant Cloud API key (§4b) |
| `REDIS_URL` | Render Key Value string (§4c) |
| `BINANCE_TESTNET_API_KEY` | paper crypto keys (from your local `.env`) |
| `BINANCE_TESTNET_API_SECRET` | same |
| `REASONING_MODEL` | `openai/gpt-4o-mini` (direct ids have NO `openrouter/` prefix) |
| `REASONING_MODEL_FALLBACK` | `openai/gpt-4.1-mini` |

Pre-set (already in `render.yaml`): `OMNIRUTE_BASE_URL=https://openrouter.ai/api/v1`,
`LIVE_TRADING_ENABLED=true`, `LIVE_TRADING_MODE=paper`, `APPROVAL_REQUIRED=true`,
risk limits, `CRYPTO_PRICE_PROVIDER=binance`, `BACKTEST_ENABLED=true`,
`PAPER_INITIAL_CASH=1000000.0`.

> **Model id gotcha:** when talking to OpenRouter **directly**, model ids are
> `openai/gpt-4o-mini` — NOT `openrouter/openai/gpt-4o-mini`. The `openrouter/`
> prefix is only used behind the OmniRoute gateway.

Optional (only when you want real live orders later):
`BINANCE_API_KEY`/`BINANCE_API_SECRET` (Binance), `ZERODHA_API_KEY`/
`ZERODHA_API_SECRET`/`ZERODHA_ACCESS_TOKEN` (Kite).

**Save**, Render restarts the service with the new secrets.

---

## 7. Keep it awake (heartbeat, free)

Without inbound traffic the free service sleeps after 15 min. A free ping every
~10 min counts as inbound traffic and keeps it running continuously.

- **UptimeRobot** (free): monitor type **HTTP(S)**, URL =
  `https://trading-telegram-bot.onrender.com/healthz`, interval **10 min**.
  (Or use **healthchecks.io** free with a similar GET ping.)
- Optional: the same monitor doubles as your uptime alerting.

> Note: the 750 free hours/month equal roughly one always-on service — this is
> why the whole stack is exactly one service. A second always-on service would
> suspend at month-end ("out of instance hours").

---

## 8. Verify (from Telegram)

After deploy + secrets:

| Command | Expected |
|---|---|
| `/ping` | 🏓 pong |
| `/status` | DB 🟢, Mode PAPER, Live disabled, Approval required Yes |
| `/analyze BTC-USD CRYPTO` | JSON report incl. `rag_hits` ≥ 1 and a `backtest` block |
| `/paper_trade BTC-USD LONG 0.001 <price> CRYPTO` | paper order + `🧪 Binance Testnet: ...` |
| `/positions`, `/portfolio`, `/trades` | paper account state |
| `/mode hybrid` | live enabled per-order; every live order needs `/approve` |

If `rag_hits: 0`, the RAG seed in `backend/run_cloud.py` fills the collection on
the next start (it seeds only when empty).

---

## 9. Going live (paper ↔ real) — hybrid mode

This deploy is **hybrid-ready and approval-gated**:

- Default mode is `paper`: orders are simulated and mirrored to **Binance
  Testnet** using the testnet keys.
- Switch with `/mode live` or `/mode hybrid`. In `live`/`hybrid`, orders go to
  the **real** broker only after you add the real keys to Render secrets and
  approve each order via `/live_trade <...>` then `/approve <id>` (300 s window).
- `APPROVAL_REQUIRED=true` means nothing real ever fires without your explicit
  Telegram approval.
- Hard risk limits (`MAX_LOSS_PER_TRADE=1%`, `MAX_DAILY_LOSS=3%`,
  `MIN_RR_RATIO=1.5`, `MAX_POSITION_PCT=5%`, exposure 30%) are code-enforced in
  `backend/risk/gate.py` / the auto-risk engine.

---

## 10. Updating strategy docs (PDF / Word / images)

The bot reads `.txt`, `.md`, `.pdf`, `.docx` (paragraphs + tables), legacy `.doc`
(best-effort RTF), and images (`.png/.jpg/.jpeg/.bmp/.webp/.tiff`) via RapidOCR.

**Option A — push a doc, redeploy (auto-seed):**
1. Put the file in `docs/strategies/` in the repo.
2. Commit + push → Render rebuilds; on boot `run_cloud.py` re-ingests, wiping
   stale chunks (`force_rebuild` on an empty/needing collection).

**Option B — live one-shot, no redeploy:**
In Render → service → **Shell**:
```bash
python -c "from backend.rag.ingest import ingest_strategy_docs; print(ingest_strategy_docs(force_rebuild=True))"
```
Safe to re-run anytime (idempotent). RAG vectors persist in Qdrant Cloud across
restarts, so this is only needed when you actually change a document.

---

## 11. Honest limitations of the free setup

- **Ephemeral local disk**: paper-trade logs, audit logs and reflection files
  under `/app/data` reset on redeploys/restarts. The decision log (Neon) and RAG
  vectors (Qdrant Cloud) persist. For real trades the broker is the source of
  truth, so nothing trading-critical is lost.
- **Redeploy = ~1 min downtime**; Render may restart free services at any time
  (Telegram long-polling reconnects automatically).
- Free external tiers have quotas (Neon storage, Qdrant free cluster size,
  OpenRouter free-model rate limits). Free `:free` OpenRouter models are flaky →
  use the configured paid models for reliability.
- Out-of-instance-hours only if you add a second always-on service. Verify
  `/ping` still works after month boundaries once.

---

## 12. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Deploy succeeded but no reply in Telegram | `TELEGRAM_BOT_TOKEN` missing/wrong secret; check service logs (Render → Logs). |
| `/analyze` errors with `rag_hits: 0` | `QDRANT_URL`/`QDRANT_API_KEY` wrong, or collection empty — check Logs, then run the shell-ingest ( §10 B). |
| `/status` shows DB 🔴 | `POSTGRES_DSN` malformed or Neon paused (free tiers sleep) — Reactivate Neon, re-save DSN. |
| LLM call returns `No models provided` | Model id has the `openrouter/` prefix — use direct ids (`openai/gpt-4o-mini`). |
| Model rate-limited | `:free` models 429. Set `REASONING_MODEL`/`REASONING_MODEL_FALLBACK` to reliable paid ids. |
| Service keeps sleeping | Heartbeat not set ( §7). Add UptimeRobot/healthchecks.io on `/healthz`. |
| Testnet order rejected | Keys wrong/revoked — regenerate at testnet.binance.vision. |
| Rebuild much slower than 5 min | onnxruntime/fastembed download once; subsequent builds are faster. |

---

## 13. Files that matter for this deploy

- `deployment/render.yaml` — the single-service Blueprint (web, free).
- `backend/Dockerfile` — `cloud` target is the default build (uvicorn + bot).
- `backend/run_cloud.py` — combined entrypoint + RAG auto-seed on boot.
- `backend/rag/ingest.py` — supported formats incl. docx + image OCR.
- `backend/rag/store.py` — Qdrant client honours `QDRANT_API_KEY`.
- `backend/core/config.py` — typed env settings.