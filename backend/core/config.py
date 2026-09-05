"""Application configuration loaded from environment variables (.env).

All settings are typed via pydantic-settings. Secrets must come from the
environment / .env and are never committed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = backend/core/config.py -> up three levels -> <repo>/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    telegram_bot_token: str = ""  # set via TELEGRAM_BOT_TOKEN env
    allowed_telegram_user_ids: str = ""  # comma-separated; empty = allow all (dev only)

    # --- AI Router (OmniRoute) ---
    omniroute_base_url: str = "http://localhost:20128/v1"
    omniroute_api_key: str = ""

    # --- Critical LLM (reliable provider for structured/risk decisions) ---
    critical_llm_base_url: str = ""
    critical_llm_api_key: str = ""
    critical_llm_model: str = ""

    # --- Shallow task model (may be free pool / cheap) ---
    shallow_llm_model: str = ""

    # --- Market data ---
    openbb_pat_token: str = ""
    marketaux_api_key: str = ""

    # --- Storage ---
    postgres_dsn: str = "postgresql://trading:trading@localhost:5432/trading"
    redis_url: str = "redis://localhost:6379/0"
    vector_store_url: str = ""
    chroma_path: str = "./data/chroma"

    # --- Reasoning / RAG (Phase 1) ---
    # gpt-4o-mini: cheap ($0.15/M in, $0.60/M out), reliable structured JSON,
    # 16k output cap — added to OmniRoute synced catalog 2026-08-30.
    reasoning_model: str = "openrouter/openai/gpt-4o-mini"
    # Fallback reasoning model if the primary is rate-limited/unavailable.
    reasoning_model_fallback: str = "openrouter/openai/gpt-4.1-mini"
    reasoning_max_tokens: int = 1800
    reasoning_temperature: float = 0.2
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""  # required for hosted Qdrant (e.g. Qdrant Cloud)
    qdrant_collection: str = "strategy_docs"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    rag_top_k: int = 4
    rag_score_threshold: float = 0.25
    strategy_docs_dir: str = "./docs/strategies"

    # --- Deep reasoning (TradingAgents multi-agent, on-demand) ---
    # Controls the deep-analysis engine used when REASONING_ENGINE=deep or a
    # request sets deep=true. Kept separate from the lean path so the default
    # stays cheap/fast; TradingAgents only runs when explicitly requested.
    reasoning_engine: str = "lean"  # "lean" | "deep"
    # Provider must be OpenAI-compatible with the OmniRoute gateway (keyless,
    # tool_choice suppressed) -> "openai_compatible".
    tradingagents_provider: str = "openai_compatible"
    tradingagents_deep_model: str = "openrouter/openai/gpt-4o-mini"
    tradingagents_quick_model: str = "openrouter/openai/gpt-4o-mini"
    tradingagents_temperature: float = 0.2
    tradingagents_max_retries: int = 3
    tradingagents_max_debate_rounds: int = 1
    tradingagents_max_risk_rounds: int = 1
    tradingagents_max_recur_limit: int = 100
    # Where TradingAgents writes artifacts (memory log, caches, reports).
    # Pointed at a scratch dir so the deep path doesn't litter the user home.
    tradingagents_home: str = "./.tradingagents"

    # --- Paper Trading (Phase 3) ---
    paper_enabled: bool = True
    paper_initial_cash: float = 1_000_000.0
    paper_log_dir: str = "./data/paper_logs"
    reflection_log_dir: str = "./data/reflections"

    # --- Live Trading (Phase 4) ---
    live_trading_enabled: bool = False
    live_trading_mode: str = "paper"  # "paper" | "live" | "hybrid"
    approval_required: bool = True
    approval_timeout_seconds: int = 300  # 5 min to approve
    max_order_value: float = 1_000_000.0  # max single order value
    max_daily_orders: int = 100

    # --- Audit / Compliance (Phase 4) ---
    audit_log_dir: str = "./data/audit_logs"

    # --- Execution (Phase 3+) ---
    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""
    binance_testnet_base_url: str = "https://testnet.binance.vision"

    # Binance Live (Phase 4)
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_base_url: str = "https://api.binance.com"

    zerodha_api_key: str = ""
    zerodha_api_secret: str = ""
    zerodha_access_token: str = ""

    # --- Risk limits (hard, code-enforced) ---
    max_loss_per_trade: float = 0.01      # 1% of equity
    max_daily_loss: float = 0.03          # 3% of equity -> halt
    min_rr_ratio: float = 1.5             # minimum risk:reward (reward/risk)
    max_position_pct: float = 0.05        # 5% of equity per symbol
    max_concurrent_positions: int = 10
    max_total_exposure: float = 0.30      # 30% of equity

    # --- Market data (Phase 5: alt-coin coverage) ---
    # Crypto analysis source: 'binance' (keyless Binance public API, covers all
    # alt-coins) or 'yfinance' (OpenBB default). Falls back gracefully.
    crypto_price_provider: str = "binance"

    # --- Backtest (Phase 2) ---
    backtest_enabled: bool = True
    backtest_window_days: int = 365   # trailing window replayed for the setup
    backtest_max_hold_bars: int = 20  # bars before a max-hold close-out
    backtest_min_trades: int = 5      # min trades before stats count as "structured"

    # --- Backtest costs (Phase 3 prep: fees + slippage) ---
    # All figures are fractions of traded notional unless stated (bps).
    backtest_costs_enabled: bool = True
    slippage_bps: float = 15.0        # adverse slippage per fill, both markets
    # Equity (NSE/BSE) per-leg components (delivery-style):
    equity_brokerage_pct: float = 0.0003
    equity_stt_pct: float = 0.001          # 0.1% -- charged on the SELL side only (delivery)
    equity_transaction_pct: float = 0.0000345
    equity_sebi_pct: float = 0.000001
    equity_stamp_duty_pct: float = 0.00015 # charged on the BUY side only
    equity_gst_pct: float = 0.18           # GST on (brokerage + transaction + sebi)
    # Crypto (spot taker):
    crypto_taker_pct: float = 0.001        # 0.1% per side

    # --- Safety / approval ---
    approval_required: bool = True
    live_trading_enabled: bool = False

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.allowed_telegram_user_ids:
            return set()
        return {int(x.strip()) for x in self.allowed_telegram_user_ids.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()


settings = get_settings()
