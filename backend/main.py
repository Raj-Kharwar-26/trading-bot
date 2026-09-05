"""FastAPI application entrypoint.

Serves a REST API + healthcheck now; Telegram webhook + orchestrator wiring
will attach here as phases land.
"""

from __future__ import annotations

from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.monitoring.metrics import (
    get_metrics,
    CONTENT_TYPE_LATEST,
    observe_http_request,
)

app = FastAPI(
    title="Trading Bot API",
    version="0.1.0",
    description="24/7 AI trading backend — NSE/BSE + crypto, paper-first, risk-gated.",
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Middleware to track HTTP request metrics."""
    start = perf_counter()
    response = await call_next(request)
    duration = perf_counter() - start
    observe_http_request(
        method=request.method,
        endpoint=request.url.path,
        status=response.status_code,
        duration=duration,
    )
    return response


@app.on_event("startup")
def _startup() -> None:
    """Create the decision-log tables at startup (idempotent, non-fatal)."""
    from backend.storage.db import init_db

    init_db()


class AnalyseRequest(BaseModel):
    symbol: str = Field(..., min_length=1, example="RELIANCE.NS")
    market: Literal["NSE", "BSE", "CRYPTO"] = "NSE"
    days: int = Field(30, ge=5, le=365)
    deep: bool = Field(False, description="Run the TradingAgents multi-agent deep analysis (on-demand; falls back to lean on failure).")


class IngestResponse(BaseModel):
    files: int
    chunks: int
    collection: str
    docs_dir: str


# --- Paper Trading Models ---

class PaperTradeRequest(BaseModel):
    symbol: str = Field(..., min_length=1, example="RELIANCE.NS")
    market: Literal["NSE", "BSE", "CRYPTO"] = "NSE"
    side: Literal["LONG", "SHORT"] = Field(..., description="Trade direction")
    qty: float = Field(..., gt=0, description="Quantity to trade")
    entry_price: float = Field(..., gt=0, description="Entry price (current market)")
    exit_price: float | None = Field(None, gt=0, description="Exit price (if closing)")
    action: Literal["OPEN", "CLOSE"] = Field("OPEN", description="Open new position or close existing")


class PaperTradeResponse(BaseModel):
    success: bool
    order_id: str | None = None
    fill: dict | None = None
    trade: dict | None = None
    error: str | None = None


class PortfolioResponse(BaseModel):
    timestamp: str
    cash: float
    total_equity: float
    unrealized_pnl: float
    realized_pnl: float
    positions: dict


class TradesResponse(BaseModel):
    trades: list[dict]
    stats: dict


@app.get("/")
def root() -> dict:
    return {"service": "trading-bot", "status": "ok", "version": "0.1.0"}


@app.get("/healthz")
def healthz() -> dict:
    """Liveness probe. Reports config-driven readiness + optional DB reachability."""
    try:
        from backend.storage.db import ping

        db_ok = ping()
    except Exception:  # noqa: BLE001 - healthz never raises
        db_ok = False
    return {
        "status": "ok",
        "live_trading_enabled": settings.live_trading_enabled,
        "approval_required": settings.approval_required,
        "db": "ok" if db_ok else "unreachable",
    }


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=get_metrics(), media_type=CONTENT_TYPE_LATEST)


@app.post("/analyze")
def analyze(req: AnalyseRequest) -> dict:
    """Run the lean reasoning pipeline for one symbol and return a structured report."""
    from backend.reasoning.agent_runner import AnalysisError, analyse_symbol

    try:
        report = analyse_symbol(req.symbol, req.market, days=req.days, deep=req.deep)
    except AnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface unexpected failures cleanly
        raise HTTPException(status_code=500, detail=f"analysis error: {exc}") from exc
    return report


@app.get("/decisions")
def decisions(symbol: str | None = None, limit: int = 20) -> dict:
    """Return the most recent persisted decision-log rows (latest first)."""
    from backend.storage.decision_log import count_decisions, latest_decisions

    rows = latest_decisions(symbol=symbol, limit=limit)
    return {"count": count_decisions(), "rows": rows}


@app.post("/ingest", response_model=IngestResponse)
def ingest() -> IngestResponse:
    """(Re)build the strategy-doc vector store from ./docs/strategies."""
    from backend.rag.ingest import ingest_strategy_docs

    try:
        summary = ingest_strategy_docs(force_rebuild=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"ingest error: {exc}") from exc
    return IngestResponse(**summary)


# --- Paper Trading Endpoints ---

@app.post("/paper/trade", response_model=PaperTradeResponse)
def paper_trade(req: PaperTradeRequest) -> PaperTradeResponse:
    """Open or close a paper trade."""
    from backend.backtest.costs import cost_model_for_market
    from backend.execution.state import get_portfolio_state

    portfolio = get_portfolio_state()
    cost_model = cost_model_for_market(req.market, settings)

    if req.action == "OPEN":
        result = portfolio.place_and_fill(
            symbol=req.symbol,
            market=req.market,
            side=req.side,
            qty=req.qty,
            entry_price=req.entry_price,
            cost_model=cost_model,
        )
        if result["success"]:
            return PaperTradeResponse(
                success=True,
                order_id=result["order_id"],
                fill=result["fill"],
            )
        return PaperTradeResponse(success=False, error=result["error"])

    else:  # CLOSE
        if req.exit_price is None:
            return PaperTradeResponse(success=False, error="exit_price required for CLOSE")
        result = portfolio.close_position(
            symbol=req.symbol,
            market=req.market,
            side=req.side,
            exit_price=req.exit_price,
            cost_model=cost_model,
            exit_reason="MANUAL",
        )
        if result["success"]:
            return PaperTradeResponse(
                success=True,
                fill=result["fill"],
                trade=result["trade"],
            )
        return PaperTradeResponse(success=False, error=result["error"])


@app.get("/paper/portfolio", response_model=PortfolioResponse)
def paper_portfolio(symbol: str | None = None, market: Literal["NSE", "BSE", "CRYPTO"] = "NSE") -> PortfolioResponse:
    """Get current paper portfolio state. Optionally pass symbol for mark-to-market."""
    from backend.execution.state import get_portfolio_state
    from backend.data.market import fetch_ohlcv

    portfolio = get_portfolio_state()
    prices = {}
    if symbol:
        try:
            from datetime import date, timedelta
            end = date.today()
            start = end - timedelta(days=1)
            df = fetch_ohlcv(symbol, market, str(start), str(end))
            if not df.empty:
                prices[symbol] = float(df["close"].iloc[-1])
        except Exception:
            pass

    snap = portfolio.get_portfolio(prices)
    return PortfolioResponse(**snap.__dict__)


@app.get("/paper/trades", response_model=TradesResponse)
def paper_trades(symbol: str | None = None, limit: int = 100) -> TradesResponse:
    """Get paper trade history and aggregate stats."""
    from backend.execution.trade_log import get_trade_log

    log = get_trade_log()
    trades = log.get_recent(limit=limit, symbol=symbol)
    stats = log.stats(symbol=symbol)
    return TradesResponse(trades=[t.__dict__ for t in trades], stats=stats)
