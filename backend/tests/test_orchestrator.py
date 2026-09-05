"""Unit tests for orchestrator and market hours."""

from __future__ import annotations

import pytest

from backend.execution.orchestrator import MarketHours, Task, TaskType, TaskStatus


class TestMarketHours:
    def test_timezone_ist(self):
        assert MarketHours.IST.utcoffset(None).total_seconds() == 5.5 * 3600

    def test_equity_hours(self):
        assert MarketHours.EQUITY_OPEN.hour == 9
        assert MarketHours.EQUITY_OPEN.minute == 15
        assert MarketHours.EQUITY_CLOSE.hour == 15
        assert MarketHours.EQUITY_CLOSE.minute == 30

    def test_crypto_24_7(self):
        assert MarketHours.is_crypto_open() is True

    def test_next_equity_open(self):
        next_open = MarketHours.next_equity_open()
        assert next_open.tzinfo is not None


class TestTask:
    def test_task_creation(self):
        task = Task(id="1", type=TaskType.ANALYZE, payload={"symbol": "TEST"})
        assert task.id == "1"
        assert task.type == TaskType.ANALYZE
        assert task.payload == {"symbol": "TEST"}
        assert task.status == TaskStatus.PENDING
        assert task.created_at > 0

    def test_task_serialization(self):
        task = Task(id="2", type=TaskType.EXECUTE, payload={"qty": 100})
        import json
        data = json.loads(json.dumps(task.__dict__, default=str))
        assert data["id"] == "2"
        assert data["type"] == "execute"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])