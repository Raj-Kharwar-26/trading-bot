"""Unit tests for alerting module."""

from __future__ import annotations

import time
import pytest

from backend.monitoring.alerting import (
    Alert,
    AlertRule,
    AlertSeverity,
    AlertState,
    AlertManager,
    create_default_rules,
)


class TestAlert:
    def test_alert_creation(self):
        alert = Alert(
            name="test_alert",
            severity=AlertSeverity.WARNING,
            message="Test message",
            value=1.5,
            threshold=1.0,
            labels={"mode": "paper"},
        )
        assert alert.name == "test_alert"
        assert alert.severity == AlertSeverity.WARNING
        assert alert.state == AlertState.FIRING

    def test_fingerprint_generation(self):
        alert1 = Alert(name="test", severity=AlertSeverity.INFO, message="msg", labels={"mode": "paper", "market": "NSE"})
        alert2 = Alert(name="test", severity=AlertSeverity.INFO, message="msg", labels={"mode": "paper", "market": "NSE"})
        assert alert1.fingerprint == alert2.fingerprint

        alert3 = Alert(name="test", severity=AlertSeverity.INFO, message="msg", labels={"mode": "live"})
        assert alert3.fingerprint != alert1.fingerprint


class TestAlertRule:
    def test_rule_gt_fires(self):
        rule = AlertRule(
            name="test",
            severity=AlertSeverity.WARNING,
            query=lambda: 10.0,
            threshold=5.0,
            operator=">",
        )
        alert = rule.evaluate(time.time())
        assert alert is not None
        assert alert.value == 10.0

    def test_rule_lt_fires(self):
        rule = AlertRule(
            name="test",
            severity=AlertSeverity.WARNING,
            query=lambda: 3.0,
            threshold=5.0,
            operator="<",
        )
        alert = rule.evaluate(time.time())
        assert alert is not None

    def test_rule_no_fire(self):
        rule = AlertRule(
            name="test",
            severity=AlertSeverity.WARNING,
            query=lambda: 10.0,
            threshold=5.0,
            operator="<",
        )
        alert = rule.evaluate(time.time())
        assert alert is None

    def test_all_operators(self):
        for op, val, thresh, should_fire in [
            (">", 10, 5, True),
            (">=", 5, 5, True),
            (">=", 4, 5, False),
            ("<", 3, 5, True),
            ("<=", 5, 5, True),
            ("<=", 6, 5, False),
            ("==", 5, 5, True),
            ("==", 4, 5, False),
            ("!=", 4, 5, True),
            ("!=", 5, 5, False),
        ]:
            rule = AlertRule("test", AlertSeverity.INFO, lambda: val, thresh, op)
            alert = rule.evaluate(time.time())
            if should_fire:
                assert alert is not None, f"Operator {op} should fire"
            else:
                assert alert is None, f"Operator {op} should not fire"


class TestAlertManager:
    def test_add_rule(self):
        manager = AlertManager()
        rule = AlertRule("test", AlertSeverity.INFO, lambda: 10, 5, ">")
        manager.add_rule(rule)
        assert len(manager.rules) == 1

    def test_check_rules_fires(self):
        manager = AlertManager()
        rule = AlertRule("test", AlertSeverity.WARNING, lambda: 10, 5, ">", cooldown=0)
        manager.add_rule(rule)

        import asyncio
        alerts = asyncio.run(manager.check_rules())
        assert len(alerts) == 1
        assert alerts[0].name == "test"

    def test_no_duplicate_firing(self):
        manager = AlertManager()
        rule = AlertRule("test", AlertSeverity.WARNING, lambda: 10, 5, ">", cooldown=0)
        manager.add_rule(rule)

        import asyncio
        alerts1 = asyncio.run(manager.check_rules())
        alerts2 = asyncio.run(manager.check_rules())
        assert len(alerts1) == 1
        assert len(alerts2) == 0  # No duplicate firing

    def test_resolution(self):
        manager = AlertManager()
        rule = AlertRule("test", AlertSeverity.WARNING, lambda: 10, 5, ">", cooldown=0)
        manager.add_rule(rule)

        import asyncio
        alerts = asyncio.run(manager.check_rules())
        assert len(alerts) == 1

        # Change query to not fire
        rule.query = lambda: 3.0
        alerts = asyncio.run(manager.check_rules())
        assert len(alerts) == 0
        assert len(manager.active_alerts) == 0
        assert len(manager.resolved_alerts) == 1

    def test_cooldown(self):
        manager = AlertManager()
        rule = AlertRule("test", AlertSeverity.WARNING, lambda: 10, 5, ">", cooldown=1)
        manager.add_rule(rule)

        import asyncio
        asyncio.run(manager.check_rules())
        alerts = asyncio.run(manager.check_rules())
        assert len(alerts) == 0  # Should be in cooldown


class TestDefaultRules:
    def test_default_rules_created(self):
        rules = create_default_rules()
        assert len(rules) > 0
        for rule in rules:
            assert isinstance(rule, AlertRule)
            assert rule.severity in AlertSeverity
            assert rule.threshold is not None
            assert rule.operator in (">", "<", ">=", "<=", "==", "!=")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])