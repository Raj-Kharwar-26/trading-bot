"""Unit/functional tests for the decision-log persistence layer.

The round-trip test requires a reachable Postgres (the dev container). If the
DB is unavailable it is skipped so the suite stays green in minimal setups.
"""

from __future__ import annotations

import pytest

from backend.storage.db import init_db, ping
from backend.storage.decision_log import (
    count_decisions,
    latest_decisions,
    log_decision,
)


def _report(symbol: str, signal: str = "NEUTRAL") -> dict:
    return {
        "signal": signal,
        "confidence": 0.5,
        "summary": "test",
        "_meta": {"symbol": symbol, "market": "NSE", "rag_hits": 2, "engine": "test"},
        "backtest": {"status": "skipped", "message": "no setup"},
    }


@pytest.fixture(scope="module", autouse=True)
def _ensure_db():
    init_db()
    if not ping():
        pytest.skip("Postgres unreachable; skipping decision-log DB tests")
    yield


def test_decision_roundtrip():
    symbol = "TESTLOG.NS"
    before = count_decisions()
    rid = log_decision(_report(symbol, signal="LONG"))
    assert rid is not None

    rows = latest_decisions(symbol=symbol, limit=10)
    assert rows, "expected at least one row back"
    assert rows[0]["id"] == rid
    assert rows[0]["signal"] == "LONG"
    assert rows[0]["engine"] == "test"
    assert rows[0]["backtest"]["status"] == "skipped"

    # No cross-contamination from prior identical-symbol runs.
    assert len([r for r in rows if r["id"] == rid]) == 1


def test_count_increases_after_insert():
    c1 = count_decisions()
    rid = log_decision(_report("COUNTTEST.NS"))
    c2 = count_decisions()
    assert rid is not None
    assert c2 >= c1 + 1
