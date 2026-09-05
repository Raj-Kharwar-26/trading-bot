"""Prometheus metrics for the trading bot.

Custom metrics for trading operations, risk, and system health.
"""

from __future__ import annotations

from functools import wraps
from time import perf_counter
from typing import Callable

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# Registry for custom metrics
registry = CollectorRegistry()

# --- HTTP / API Metrics ---
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
    registry=registry,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)

# --- Analysis / Reasoning Metrics ---
analysis_requests_total = Counter(
    "analysis_requests_total",
    "Total analysis requests",
    ["engine", "market", "signal"],
    registry=registry,
)

analysis_duration_seconds = Histogram(
    "analysis_duration_seconds",
    "Analysis duration in seconds",
    ["engine", "market"],
    buckets=(1, 5, 10, 30, 60, 120, 180, 300),
    registry=registry,
)

analysis_confidence = Summary(
    "analysis_confidence",
    "Analysis confidence score",
    ["engine", "signal"],
    registry=registry,
)

# --- Backtest Metrics ---
backtest_runs_total = Counter(
    "backtest_runs_total",
    "Total backtest runs",
    ["status", "market"],
    registry=registry,
)

backtest_trades = Histogram(
    "backtest_trades",
    "Number of trades in backtest",
    ["market"],
    buckets=(0, 1, 2, 3, 5, 10, 20, 50, 100),
    registry=registry,
)

backtest_avg_r = Gauge(
    "backtest_avg_r",
    "Backtest average R per trade",
    ["market"],
    registry=registry,
)

backtest_win_rate = Gauge(
    "backtest_win_rate",
    "Backtest win rate",
    ["market"],
    registry=registry,
)

backtest_cost_drag = Gauge(
    "backtest_cost_drag",
    "Backtest cost drag (gross R - net R)",
    ["market"],
    registry=registry,
)

# --- Paper/Live Trading Metrics ---
trade_orders_total = Counter(
    "trade_orders_total",
    "Total trade orders placed",
    ["mode", "market", "side", "status"],
    registry=registry,
)

trade_fills_total = Counter(
    "trade_fills_total",
    "Total trade fills",
    ["mode", "market", "side"],
    registry=registry,
)

trade_pnl = Histogram(
    "trade_pnl",
    "Trade PnL (net)",
    ["mode", "market", "side"],
    buckets=(-10000, -5000, -1000, -500, -100, -50, -10, 0, 10, 50, 100, 500, 1000, 5000, 10000),
    registry=registry,
)

trade_r_multiple = Histogram(
    "trade_r_multiple",
    "Trade R multiple (net)",
    ["mode", "market", "side"],
    buckets=(-5, -3, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3, 5),
    registry=registry,
)

open_positions = Gauge(
    "open_positions",
    "Number of open positions",
    ["mode", "market"],
    registry=registry,
)

portfolio_equity = Gauge(
    "portfolio_equity",
    "Current portfolio equity",
    ["mode"],
    registry=registry,
)

portfolio_cash = Gauge(
    "portfolio_cash",
    "Current portfolio cash",
    ["mode"],
    registry=registry,
)

portfolio_unrealized_pnl = Gauge(
    "portfolio_unrealized_pnl",
    "Portfolio unrealized PnL",
    ["mode"],
    registry=registry,
)

portfolio_realized_pnl = Gauge(
    "portfolio_realized_pnl",
    "Portfolio realized PnL",
    ["mode"],
    registry=registry,
)

# --- Risk Metrics ---
risk_daily_pnl_pct = Gauge(
    "risk_daily_pnl_pct",
    "Daily PnL as percentage of equity",
    registry=registry,
)

risk_exposure_pct = Gauge(
    "risk_exposure_pct",
    "Total exposure as percentage of equity",
    registry=registry,
)

risk_halted = Gauge(
    "risk_halted",
    "Whether trading is halted (1) or not (0)",
    registry=registry,
)

risk_violations_total = Counter(
    "risk_violations_total",
    "Total risk violations",
    ["rule", "severity"],
    registry=registry,
)

# --- Approval Metrics ---
approval_requests_total = Counter(
    "approval_requests_total",
    "Total approval requests",
    ["status"],  # pending, approved, rejected, expired
    registry=registry,
)

approval_duration_seconds = Histogram(
    "approval_duration_seconds",
    "Time from request to approval/rejection",
    buckets=(10, 30, 60, 120, 300, 600, 1800),
    registry=registry,
)

# --- LLM / AI Metrics ---
llm_requests_total = Counter(
    "llm_requests_total",
    "Total LLM requests",
    ["provider", "model", "status"],
    registry=registry,
)

llm_request_duration_seconds = Histogram(
    "llm_request_duration_seconds",
    "LLM request latency",
    ["provider", "model"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
    registry=registry,
)

llm_tokens_used = Histogram(
    "llm_tokens_used",
    "LLM tokens used per request",
    ["provider", "model", "type"],  # prompt, completion, total
    buckets=(100, 500, 1000, 2000, 4000, 8000, 16000),
    registry=registry,
)

# --- RAG Metrics ---
rag_retrievals_total = Counter(
    "rag_retrievals_total",
    "Total RAG retrievals",
    ["status"],
    registry=registry,
)

rag_retrieval_duration_seconds = Histogram(
    "rag_retrieval_duration_seconds",
    "RAG retrieval latency",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
    registry=registry,
)

rag_context_chunks = Histogram(
    "rag_context_chunks",
    "Number of context chunks retrieved",
    buckets=(0, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20),
    registry=registry,
)

# --- Market Data Metrics ---
market_data_requests_total = Counter(
    "market_data_requests_total",
    "Total market data requests",
    ["provider", "market", "status"],
    registry=registry,
)

market_data_latency_seconds = Histogram(
    "market_data_latency_seconds",
    "Market data fetch latency",
    ["provider", "market"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30),
    registry=registry,
)

# --- System Metrics ---
active_users = Gauge(
    "active_users",
    "Number of active Telegram users",
    registry=registry,
)

db_connections_active = Gauge(
    "db_connections_active",
    "Active database connections",
    registry=registry,
)

redis_connected = Gauge(
    "redis_connected",
    "Redis connection status (1=connected, 0=disconnected)",
    registry=registry,
)


def observe_http_request(method: str, endpoint: str, status: int, duration: float):
    """Record HTTP request metrics."""
    http_requests_total.labels(method=method, endpoint=endpoint, status=str(status)).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)


def observe_analysis(engine: str, market: str, signal: str, duration: float, confidence: float):
    """Record analysis metrics."""
    analysis_requests_total.labels(engine=engine, market=market, signal=signal).inc()
    analysis_duration_seconds.labels(engine=engine, market=market).observe(duration)
    analysis_confidence.labels(engine=engine, signal=signal).observe(confidence)


def observe_backtest(market: str, status: str, trades: int, avg_r: float, win_rate: float, cost_drag: float):
    """Record backtest metrics."""
    backtest_runs_total.labels(status=status, market=market).inc()
    backtest_trades.labels(market=market).observe(trades)
    backtest_avg_r.labels(market=market).set(avg_r)
    backtest_win_rate.labels(market=market).set(win_rate)
    backtest_cost_drag.labels(market=market).set(cost_drag)


def observe_trade(mode: str, market: str, side: str, status: str, pnl: float | None = None, r_multiple: float | None = None):
    """Record trade metrics."""
    trade_orders_total.labels(mode=mode, market=market, side=side, status=status).inc()
    if status == "filled":
        trade_fills_total.labels(mode=mode, market=market, side=side).inc()
    if pnl is not None:
        trade_pnl.labels(mode=mode, market=market, side=side).observe(pnl)
    if r_multiple is not None:
        trade_r_multiple.labels(mode=mode, market=market, side=side).observe(r_multiple)


def observe_portfolio(mode: str, equity: float, cash: float, unrealized: float, realized: float, open_pos: int):
    """Record portfolio metrics."""
    portfolio_equity.labels(mode=mode).set(equity)
    portfolio_cash.labels(mode=mode).set(cash)
    portfolio_unrealized_pnl.labels(mode=mode).set(unrealized)
    portfolio_realized_pnl.labels(mode=mode).set(realized)
    open_positions.labels(mode=mode, market="all").set(open_pos)


def observe_risk(daily_pnl_pct: float, exposure_pct: float, halted: bool):
    """Record risk metrics."""
    risk_daily_pnl_pct.set(daily_pnl_pct)
    risk_exposure_pct.set(exposure_pct)
    risk_halted.set(1 if halted else 0)


def observe_violation(rule: str, severity: str):
    """Record risk violation."""
    risk_violations_total.labels(rule=rule, severity=severity).inc()


def observe_approval(status: str, duration: float | None = None):
    """Record approval metrics."""
    approval_requests_total.labels(status=status).inc()
    if duration is not None:
        approval_duration_seconds.observe(duration)


def observe_llm(provider: str, model: str, status: str, duration: float, tokens: dict[str, int] | None = None):
    """Record LLM metrics."""
    llm_requests_total.labels(provider=provider, model=model, status=status).inc()
    llm_request_duration_seconds.labels(provider=provider, model=model).observe(duration)
    if tokens:
        for token_type, count in tokens.items():
            llm_tokens_used.labels(provider=provider, model=model, type=token_type).observe(count)


def observe_rag(status: str, duration: float, chunks: int):
    """Record RAG metrics."""
    rag_retrievals_total.labels(status=status).inc()
    rag_retrieval_duration_seconds.observe(duration)
    rag_context_chunks.observe(chunks)


def observe_market_data(provider: str, market: str, status: str, duration: float):
    """Record market data metrics."""
    market_data_requests_total.labels(provider=provider, market=market, status=status).inc()
    market_data_latency_seconds.labels(provider=provider, market=market).observe(duration)


# --- Decorator for timing ---
def timed(metric: Histogram | Summary, labels: dict | None = None):
    """Decorator to time a function and record to a metric."""
    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                duration = perf_counter() - start
                if labels:
                    metric.labels(**labels).observe(duration)
                else:
                    metric.observe(duration)
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                duration = perf_counter() - start
                if labels:
                    metric.labels(**labels).observe(duration)
                else:
                    metric.observe(duration)
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator


def get_metrics() -> bytes:
    """Generate Prometheus metrics output."""
    return generate_latest(registry)