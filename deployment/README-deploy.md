# Deploying the Trading Bot to the Cloud (Railway / Render / Fly.io)

This moves the whole system off your local machine so it runs **always-on** in
the cloud with no need to keep a laptop/PC powered on. It uses
**containerized OmniRoute** for the LLM gateway, exactly as planned.

> The local machine runs everything via PowerShell + `Start-Process`. The cloud
> runs the exact same code, containerized, with platform-managed restarts.

---

## Architecture (what gets deployed)

```
                Telegram  <--pull- (long polling, no webhook needed)
                    |
           ┌────────┴───────────┐
           │   telegram-bot     │  python -m backend.telegram.bot  (1 instance)
           └────────┬───────────┘
                    | OMNIRUTE_BASE_URL=http://omniroute:20128/v1
           ┌────────┴───────────┐
           │      omniroute     │  npm gateway -> OpenRouter/your providers
           └────────────────────┘
     Postgres  (decision log)     Redis  (orchestrator queue)
     Qdrant   (RAG vector store)  [API/worker services optional]
```

- **`telegram-bot`** — built from `backend/Dockerfile`. `telegram` is the
  **default** build target (a bare `docker build` yields the bot, which is what
  Render does). The bot is a persistent background service (`pserv`) that
  long-polls Telegram outbound and needs no public port.
- **`omniroute`** — built from `deployment/omniroute/Dockerfile`.
- **Postgres / Redis** — platform-managed (Render `databases:` / Railway plugin).
- **Qdrant** — a persistent service with a disk volume.
- Optional: the FastAPI worker + API replicas from `deployment/docker-compose.prod.yml`
  are not required for Telegram-only operation; add them if you want the REST API
  or the 24/7 orchestrator workers.

---

## Platform: Render

1. Create a **Blueprint** from this repository (GitHub).
2. Point it at `deployment/render.yaml`. Render will propose the services.
3. Set these **env vars** per service in the dashboard (marked `sync:false` / blank):
   - `telegram-bot`: `TELEGRAM_BOT_TOKEN`, `ALLOWED_TELEGRAM_USER_IDS`
   - `omniroute`: `STORAGE_ENCRYPTION_KEY` — **must equal your local
     `~/.omniroute/.env` key** so the existing OpenRouter config decrypts.
4. **Persist OmniRoute config**: on first deploy OmniRoute creates a fresh
   storage.sqlite. Either
   - copy your local providers by importing them through the OmniRoute UI once,
     or
   - restore `storage.sqlite` from `~/.omniroute` into the `omniroute-data`
     disk (matches the key above).
5. Render wires `OMNIRUTE_BASE_URL`, `POSTGRES_DSN`, `REDIS_URL`,
   `VECTOR_STORE_URL` via `fromService` references automatically.

## Platform: Railway

- Add a **PostgreSQL** plugin → its connection string becomes `POSTGRES_DSN`.
- Add a **Redis** plugin → becomes `REDIS_URL`. Add a **Qdrant** service
  (`qdrant/qdrant` image) with a volume at `/qdrant/storage`.
- Add an **OmniRoute** service from `deployment/omniroute/Dockerfile`; set
  `PORT=20128`, `STORAGE_ENCRYPTION_KEY`, and mount a volume at `/root/.omniroute`.
- Add the **telegram-bot** service from `backend/Dockerfile` with
  `railway.json`'s start command; set `OMNIRUTE_BASE_URL`,
  `TELEGRAM_BOT_TOKEN`, and the DB URLs.
- Service-to-service: Railway exposes `http://<service-name>:<port>` on an
  internal private network.

## Platform: Fly.io

- Roughly the same services as Railway; use `fly.toml` per app or one `fly launch`
  per service. OmniRoute + bot each are an app. Managed Postgres via
  `fly postgres create`, Redis via Upstash or `fly redis`, Qdrant as a volume mount.

---

## Critical environment variables

These must all be set (secrets via the platform dashboard, not in git):

| Var | Where | Notes |
|-----|-------|-------|
| `TELEGRAM_BOT_TOKEN` | bot | BotFather token |
| `ALLOWED_TELEGRAM_USER_IDS` | bot | empty = allow all |
| `OMNIRUTE_BASE_URL` | bot | internal URL to the omniroute service |
| `POSTGRES_DSN` | bot | decision log |
| `REDIS_URL` | bot | orchestrator queue |
| `VECTOR_STORE_URL` | bot | qdrant host/port |
| `CRYPTO_PRICE_PROVIDER` | bot | `binance` (default; keyless, all alts) |
| `LIVE_TRADING_ENABLED` | bot | keep `false` for paper |
| `STORAGE_ENCRYPTION_KEY` | omniroute | must match local key |
| `QDRANT__SERVICE__GRPC_PORT` | qdrant | 6334 |

Your `backend/.env` is **never** committed (see `backend/.dockerignore`).

---

## Before you deploy

1. **Test locally** — the code is already verified (alt-coin data via Binance,
   full test suite passing).
2. **Ingest strategy docs once** on the deployed bot. From a shell in the
   `telegram-bot` container:
   ```bash
   python -c "from backend.rag.ingest import ingest_strategy_docs; print(ingest_strategy_docs(force_rebuild=True))"
   ```
   This populates Qdrant (volume-backed, persists).
3. Confirm `/ping` then `/analyze BTC-USD CRYPTO` in Telegram.

## Monitoring / secrets hygiene

- The repo has Prometheus/Grafana/Alertmanager configs in `deployment/`. Those
  target the self-hosted compose; on a PaaS you typically use the platform's
  built-in logs + the `/healthz` route instead of running extra exporters.
- Never store secrets in `npm run` args or compose `environment:` — use the
  platform secret vault.

---

## Migrating OmniRoute config to the cloud (one-time)

OmniRoute keeps your **provider credentials** (OpenRouter key, and the
OpenRouter provider you set up) in `~/.omniroute/storage.sqlite`, encrypted with
`STORAGE_ENCRYPTION_KEY`. To avoid re-entering everything on the cloud:

1. Get your local key:
   ```
   Get-Content C:\Users\rajkh\.omniroute\.env   # STORAGE_ENCRYPTION_KEY=...
   ```
2. Set that exact `STORAGE_ENCRYPTION_KEY` on the deployed `omniroute` service
   (it must match, or the existing config won't decrypt).
3. Either:
   - **Prefer UI:** open the deployed OmniRoute UI once and re-add the OpenRouter
     provider there (simplest, safe), **or**
   - **Restore the DB:** upload your `~/.omniroute/storage.sqlite` into the
     `omniroute-data` volume at `/root/.omniroute/storage.sqlite` (with the same
     key). Use the platform's volume/file tooling or a one-shot `fly sftp` /
     Railway volume upload.
4. Confirm: hit the omniroute service `POST /v1/chat/completions` (model
   `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`, `stream:false`) and
   expect `200`. (Do not rely on `/healthz` — it can be slow on OmniRoute.)

## RAG dependency note

`/analyze` needs `fastembed`, `qdrant-client`, and `onnxruntime` for
embeddings + vector retrieval. These were added to `requirements.txt`, so the
`backend/Dockerfile` installs them. If you ever add a new RAG doc, re-ingest
once on the deployed bot:

```bash
python -c "from backend.rag.ingest import ingest_strategy_docs; print(ingest_strategy_docs(force_rebuild=True))"
```