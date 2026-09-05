"""Unit tests for audit log."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from backend.execution.audit_log import AuditEvent, AuditLog, log_order_event


class TestAuditLog:
    def test_log_and_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = AuditLog(Path(tmp))
            event = AuditEvent(
                event_id="E1",
                timestamp=datetime.utcnow().isoformat(),
                event_type="ORDER_PLACED",
                order_id="ORD-001",
                client_order_id="CLIENT-001",
                symbol="RELIANCE",
                exchange="NSE",
                side="BUY",
                quantity=100,
                price=2500.0,
                order_type="LIMIT",
                product="CNC",
                status="PENDING",
            )
            log.log_event(event)

            events = log.get_events(order_id="ORD-001")
            assert len(events) == 1
            assert events[0].event_id == "E1"
            assert events[0].symbol == "RELIANCE"
            assert events[0].side == "BUY"

    def test_get_order_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = AuditLog(Path(tmp))
            base_time = datetime.utcnow().isoformat()

            # Order placed
            log.log_event(AuditEvent(
                event_id="E1", timestamp=base_time, event_type="ORDER_PLACED",
                order_id="ORD-001", client_order_id="C1", symbol="RELIANCE", exchange="NSE",
                side="BUY", quantity=100, price=2500.0, order_type="LIMIT", product="CNC",
                status="PENDING",
            ))
            # Filled
            log.log_event(AuditEvent(
                event_id="E2", timestamp=base_time, event_type="ORDER_FILLED",
                order_id="ORD-001", client_order_id="C1", symbol="RELIANCE", exchange="NSE",
                side="BUY", quantity=100, price=2500.0, order_type="LIMIT", product="CNC",
                status="FILLED", filled_qty=100, avg_fill_price=2500.0, fees=10.0,
            ))

            lifecycle = log.get_order_lifecycle("ORD-001")
            assert len(lifecycle) == 2
            # ASC order (oldest first) - placed first, then filled
            assert lifecycle[0].event_type == "ORDER_PLACED"
            assert lifecycle[1].event_type == "ORDER_FILLED"

    def test_approval_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = AuditLog(Path(tmp))
            log.log_event(AuditEvent(
                event_id="E1", timestamp=datetime.utcnow().isoformat(), event_type="ORDER_PLACED",
                order_id="ORD-001", symbol="RELIANCE", exchange="NSE", side="BUY",
                quantity=100, price=2500.0, order_type="LIMIT", product="CNC", status="PENDING_APPROVAL",
            ))
            log.log_event(AuditEvent(
                event_id="E2", timestamp=datetime.utcnow().isoformat(), event_type="APPROVAL_GRANTED",
                order_id="ORD-001", symbol="RELIANCE", exchange="NSE", side="BUY",
                quantity=100, price=2500.0, order_type="LIMIT", product="CNC", status="APPROVED",
            ))

            approvals = log.get_events(event_type="APPROVAL_GRANTED")
            assert len(approvals) == 1

    def test_daily_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = AuditLog(Path(tmp))
            now = datetime.utcnow()

            # Order 1: placed then filled
            log.log_event(AuditEvent(
                event_id="E1", timestamp=now.isoformat(), event_type="ORDER_PLACED",
                order_id="ORD-001", symbol="RELIANCE", exchange="NSE", side="BUY",
                quantity=100, price=2500.0, order_type="LIMIT", product="CNC", status="PENDING",
            ))
            log.log_event(AuditEvent(
                event_id="E1b", timestamp=now.isoformat(), event_type="ORDER_FILLED",
                order_id="ORD-001", symbol="RELIANCE", exchange="NSE", side="BUY",
                quantity=100, price=2500.0, order_type="LIMIT", product="CNC", status="FILLED",
                filled_qty=100, avg_fill_price=2500.0, fees=10.0,
            ))
            # Order 2: placed then filled
            log.log_event(AuditEvent(
                event_id="E2", timestamp=now.isoformat(), event_type="ORDER_PLACED",
                order_id="ORD-002", symbol="TCS", exchange="NSE", side="SELL",
                quantity=50, price=3000.0, order_type="MARKET", product="CNC", status="PENDING",
            ))
            log.log_event(AuditEvent(
                event_id="E2b", timestamp=now.isoformat(), event_type="ORDER_FILLED",
                order_id="ORD-002", symbol="TCS", exchange="NSE", side="SELL",
                quantity=50, price=3000.0, order_type="MARKET", product="CNC", status="FILLED",
                filled_qty=50, avg_fill_price=3000.0, fees=5.0,
            ))
            # Order 3: cancelled
            log.log_event(AuditEvent(
                event_id="E3", timestamp=now.isoformat(), event_type="ORDER_CANCELLED",
                order_id="ORD-003", symbol="INFY", exchange="NSE", side="BUY",
                quantity=200, price=1500.0, order_type="LIMIT", product="CNC", status="CANCELLED",
            ))

            summary = log.daily_summary(now)
            assert summary["total_events"] == 5  # 2 placed + 2 filled + 1 cancelled
            assert summary["orders_placed"] == 2
            assert summary["orders_filled"] == 2
            assert summary["orders_cancelled"] == 1
            assert summary["total_fees"] == 15.0
            assert summary["total_volume"] == 400000.0  # 100*2500 + 50*3000

    def test_helper_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            import backend.execution.audit_log as audit_mod
            original = audit_mod._audit_log
            try:
                audit_mod._audit_log = AuditLog(Path(tmp))
                event = log_order_event(
                    event_type="ORDER_PLACED",
                    order_id="ORD-001",
                    symbol="RELIANCE",
                    exchange="NSE",
                    side="BUY",
                    quantity=100,
                    order_type="LIMIT",
                    product="CNC",
                    status="PENDING",
                    price=2500.0,
                )
                assert event.order_id == "ORD-001"
                assert event.event_type == "ORDER_PLACED"
            finally:
                audit_mod._audit_log = original


if __name__ == "__main__":
    pytest.main([__file__, "-v"])