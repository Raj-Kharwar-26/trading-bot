"""24/7 Orchestrator: Redis-backed task queue + scheduler.

Runs the trading loop: analyze -> backtest -> risk gate -> paper execute -> reflect.
Market-hour aware: crypto 24/7, NSE/BSE 09:15-15:30 IST.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from enum import Enum
from typing import Any, Callable

import redis

from backend.core.config import settings
from backend.execution.paper import OrderSide, PaperEngine
from backend.execution.state import PortfolioState
from backend.reasoning.agent_runner import analyse_symbol


class TaskType(str, Enum):
    ANALYZE = "analyze"
    BACKTEST = "backtest"
    EXECUTE = "execute"
    REFLECT = "reflect"
    HEALTH_CHECK = "health_check"
    EQUITY_SNAPSHOT = "equity_snapshot"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    id: str
    type: TaskType
    payload: dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    result: dict[str, Any] | None = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()


class MarketHours:
    """Market hours configuration and helpers."""

    # IST timezone (UTC+5:30)
    IST = timezone(timedelta(hours=5, minutes=30))

    # NSE/BSE: 09:15 - 15:30 IST
    EQUITY_OPEN = dt_time(9, 15)
    EQUITY_CLOSE = dt_time(15, 30)

    # Crypto: 24/7

    @classmethod
    def now_ist(cls) -> datetime:
        return datetime.now(cls.IST)

    @classmethod
    def is_equity_open(cls) -> bool:
        now = cls.now_ist().time()
        return cls.EQUITY_OPEN <= now <= cls.EQUITY_CLOSE

    @classmethod
    def is_crypto_open(cls) -> bool:
        return True  # 24/7

    @classmethod
    def is_market_open(cls, market: str) -> bool:
        if market in ("NSE", "BSE"):
            return cls.is_equity_open()
        return cls.is_crypto_open()

    @classmethod
    def next_equity_open(cls) -> datetime:
        now = cls.now_ist()
        today_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        if now.time() < cls.EQUITY_OPEN:
            return today_open
        # Next business day
        next_day = today_open + timedelta(days=1)
        while next_day.weekday() >= 5:  # Skip weekends
            next_day += timedelta(days=1)
        return next_day


class Orchestrator:
    """
    Main orchestrator for the 24/7 trading loop.

    Uses Redis for:
    - Task queue (LPUSH/BRPOP)
    - Task status tracking (HASH)
    - Pub/Sub for real-time updates
    """

    def __init__(
        self,
        portfolio: PortfolioState | None = None,
        paper_engine: PaperEngine | None = None,
        redis_url: str | None = None,
    ):
        self.redis_url = redis_url or settings.redis_url
        self.portfolio = portfolio
        self.paper_engine = paper_engine
        self._redis: redis.Redis | None = None
        self._running = False
        self._worker_tasks: list[asyncio.Task] = []
        self._handlers: dict[TaskType, Callable] = {}

    async def connect(self) -> None:
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
        await self._redis.ping()

    async def close(self) -> None:
        self._running = False
        for task in self._worker_tasks:
            task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        if self._redis:
            await self._redis.close()

    def register_handler(self, task_type: TaskType, handler: Callable) -> None:
        self._handlers[task_type] = handler

    async def enqueue(self, task_type: TaskType, payload: dict[str, Any]) -> str:
        """Add a task to the queue."""
        task = Task(
            id=str(uuid.uuid4())[:8],
            type=task_type,
            payload=payload,
        )
        await self._redis.lpush("orchestrator:queue", json.dumps(asdict(task)))
        await self._redis.hset(f"orchestrator:task:{task.id}", mapping={
            "status": TaskStatus.PENDING.value,
            "created_at": str(task.created_at),
            "payload": json.dumps(payload),
        })
        return task.id

    async def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        data = await self._redis.hgetall(f"orchestrator:task:{task_id}")
        if not data:
            return None
        return {k: json.loads(v) if k in ("payload", "result") else v for k, v in data.items()}

    async def _worker(self, worker_id: int) -> None:
        while self._running:
            try:
                # Blocking pop with 5s timeout
                result = await self._redis.brpop("orchestrator:queue", timeout=5)
                if not result:
                    continue

                _, task_json = result
                task_data = json.loads(task_json)
                task = Task(**task_data)

                await self._redis.hset(f"orchestrator:task:{task.id}", "status", TaskStatus.RUNNING.value)
                await self._redis.hset(f"orchestrator:task:{task.id}", "started_at", str(time.time()))

                try:
                    handler = self._handlers.get(task.type)
                    if not handler:
                        raise ValueError(f"No handler for task type: {task.type}")

                    task.result = await handler(task.payload)
                    task.status = TaskStatus.COMPLETED
                except Exception as e:
                    task.status = TaskStatus.FAILED
                    task.error = str(e)

                task.completed_at = time.time()
                await self._redis.hset(f"orchestrator:task:{task.id}", mapping={
                    "status": task.status.value,
                    "completed_at": str(task.completed_at),
                    "error": task.error or "",
                    "result": json.dumps(task.result or {}),
                })

            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log but don't crash worker
                await asyncio.sleep(1)

    async def start_workers(self, num_workers: int = 2) -> None:
        self._running = True
        for i in range(num_workers):
            t = asyncio.create_task(self._worker(i))
            self._worker_tasks.append(t)

    # --- Default task handlers ---

    async def handle_analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the lean analysis pipeline."""
        symbol = payload["symbol"]
        market = payload.get("market", "NSE")
        days = payload.get("days", 30)
        deep = payload.get("deep", False)

        if not MarketHours.is_market_open(market):
            return {"skipped": True, "reason": f"Market {market} closed"}

        report = analyse_symbol(symbol, market, days=days, deep=deep)
        return {"report": report}

    async def handle_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a paper trade based on analysis report."""
        if not self.portfolio:
            return {"error": "No portfolio configured"}

        from backend.backtest.costs import cost_model_for_market

        symbol = payload["symbol"]
        market = payload.get("market", "NSE")
        side = payload["side"]  # LONG/SHORT
        qty = payload["qty"]
        entry_price = payload["entry_price"]
        action = payload.get("action", "OPEN")

        cost_model = cost_model_for_market(market, settings)

        if action == "OPEN":
            result = self.portfolio.place_and_fill(
                symbol=symbol, market=market, side=side, qty=qty,
                entry_price=entry_price, cost_model=cost_model,
            )
        else:
            exit_price = payload.get("exit_price")
            if not exit_price:
                return {"error": "exit_price required for CLOSE"}
            result = self.portfolio.close_position(
                symbol=symbol, market=market, side=side,
                exit_price=exit_price, cost_model=cost_model,
            )
        return result

    async def handle_reflect(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate reflection for a closed trade."""
        from backend.execution.reflection import ReflectionEngine
        from backend.execution.trade_log import get_trade_log

        trade_id = payload["trade_id"]
        log = get_trade_log()
        trades = log.get_recent(1000)
        trade = next((t for t in trades if t.trade_id == trade_id), None)

        if not trade:
            return {"error": f"Trade {trade_id} not found"}

        engine = ReflectionEngine()
        reflection = engine.generate_reflection(trade)
        return {"reflection_id": reflection.reflection_id, "key_lesson": reflection.key_lesson}

    async def handle_equity_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Record equity curve point."""
        if not self.portfolio:
            return {"error": "No portfolio"}

        # Get current prices for open positions
        prices = {}
        # In production, fetch from market data
        self.portfolio.record_equity_point(prices)
        return {"recorded": True}


async def create_orchestrator(
    portfolio: PortfolioState | None = None,
    paper_engine: PaperEngine | None = None,
) -> Orchestrator:
    """Factory for orchestrator with default handlers."""
    orch = Orchestrator(portfolio=portfolio, paper_engine=paper_engine)
    await orch.connect()

    orch.register_handler(TaskType.ANALYZE, orch.handle_analyze)
    orch.register_handler(TaskType.EXECUTE, orch.handle_execute)
    orch.register_handler(TaskType.REFLECT, orch.handle_reflect)
    orch.register_handler(TaskType.EQUITY_SNAPSHOT, orch.handle_equity_snapshot)

    return orch