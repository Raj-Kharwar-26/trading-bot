"""Alerting rules and Telegram alert dispatcher.

Evaluates Prometheus metrics against thresholds and sends Telegram alerts.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from backend.core.config import settings
from backend.monitoring.metrics import (
    CONTENT_TYPE_LATEST,
    get_metrics,
    registry,
)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertState(str, Enum):
    FIRING = "firing"
    RESOLVED = "resolved"


@dataclass
class Alert:
    """A single alert instance."""
    name: str
    severity: AlertSeverity
    message: str
    value: float | None = None
    threshold: float | None = None
    labels: dict[str, str] = field(default_factory=dict)
    state: AlertState = AlertState.FIRING
    started_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
    fingerprint: str = ""

    def __post_init__(self):
        if not self.fingerprint:
            parts = [self.name, str(self.labels.get("mode", "")), str(self.labels.get("market", ""))]
            self.fingerprint = "|".join(parts)


@dataclass
class AlertRule:
    """An alerting rule with threshold and evaluation logic."""
    name: str
    severity: AlertSeverity
    query: Callable[[], float | None]  # Returns metric value, None if unavailable
    threshold: float
    operator: str = ">"  # ">", "<", ">=", "<=", "==", "!="
    for_duration: float = 0  # seconds metric must exceed threshold before firing
    labels: dict[str, str] = field(default_factory=dict)
    description: str = ""
    cooldown: float = 300  # seconds before re-firing after resolution

    def evaluate(self, now: float) -> Alert | None:
        """Evaluate the rule and return an alert if firing."""
        value = self.query()
        if value is None:
            return None

        fired = False
        if self.operator == ">" and value > self.threshold:
            fired = True
        elif self.operator == "<" and value < self.threshold:
            fired = True
        elif self.operator == ">=" and value >= self.threshold:
            fired = True
        elif self.operator == "<=" and value <= self.threshold:
            fired = True
        elif self.operator == "==" and value == self.threshold:
            fired = True
        elif self.operator == "!=" and value != self.threshold:
            fired = True

        if not fired:
            return None

        return Alert(
            name=self.name,
            severity=self.severity,
            message=self.description or f"{self.name}: {value} {self.operator} {self.threshold}",
            value=value,
            threshold=self.threshold,
            labels=self.labels.copy(),
        )

    def fingerprint(self) -> str:
        """Unique fingerprint for this rule (for alert deduplication)."""
        parts = [self.name, str(self.labels.get("mode", "")), str(self.labels.get("market", ""))]
        return "|".join(parts)


class AlertManager:
    """Manages alert rules, state, and notifications."""

    def __init__(self, telegram_dispatcher: "TelegramAlertDispatcher | None" = None):
        self.rules: list[AlertRule] = []
        self.active_alerts: dict[str, Alert] = {}  # fingerprint -> Alert
        self.resolved_alerts: dict[str, Alert] = {}
        self.last_fired: dict[str, float] = {}  # fingerprint -> timestamp
        self.telegram = telegram_dispatcher
        self._running = False
        self._task: asyncio.Task | None = None

    def add_rule(self, rule: AlertRule) -> None:
        self.rules.append(rule)

    def add_rules(self, rules: list[AlertRule]) -> None:
        for r in rules:
            self.add_rule(r)

    async def check_rules(self) -> list[Alert]:
        """Evaluate all rules and return newly fired alerts."""
        now = time.time()
        new_alerts = []

        for rule in self.rules:
            alert = rule.evaluate(now)
            fp = rule.fingerprint()

            if alert:
                # Check if already firing
                existing = self.active_alerts.get(fp)
                if not existing:
                    # Check cooldown
                    last = self.last_fired.get(fp, 0)
                    if now - last >= rule.cooldown:
                        self.active_alerts[fp] = alert
                        new_alerts.append(alert)
                        self.last_fired[fp] = now
            else:
                # Check if we need to resolve a previously firing alert
                if fp in self.active_alerts:
                    resolved_alert = self.active_alerts.pop(fp)
                    resolved_alert.state = AlertState.RESOLVED
                    resolved_alert.resolved_at = datetime.utcnow()
                    self.resolved_alerts[fp] = resolved_alert

        return new_alerts

    async def send_alerts(self, alerts: list[Alert]) -> None:
        """Send alerts via configured dispatchers."""
        if self.telegram:
            await self.telegram.send_alerts(alerts)

    async def run_loop(self, interval: float = 30) -> None:
        """Run the alert evaluation loop."""
        self._running = True
        while self._running:
            try:
                alerts = await self.check_rules()
                if alerts:
                    await self.send_alerts(alerts)
            except Exception:
                pass  # Log but don't crash
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False


class TelegramAlertDispatcher:
    """Dispatches alerts to Telegram chat(s)."""

    def __init__(self, bot_token: str, chat_ids: list[int]):
        self.bot_token = bot_token
        self.chat_ids = chat_ids
        self._session: Any = None

    async def _get_session(self) -> Any:
        import aiohttp
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send_alerts(self, alerts: list[Alert]) -> None:
        if not self.chat_ids:
            return

        import aiohttp
        session = await self._get_session()
        base_url = f"https://api.telegram.org/bot{self.bot_token}"

        for alert in alerts:
            emoji = {
                AlertSeverity.INFO: "ℹ️",
                AlertSeverity.WARNING: "⚠️",
                AlertSeverity.CRITICAL: "🚨",
            }.get(alert.severity, "📢")

            state_emoji = "🔴" if alert.state == AlertState.FIRING else "🟢"
            text = (
                f"{state_emoji} {emoji} <b>{alert.name}</b>\n"
                f"Severity: {alert.severity.value.upper()}\n"
                f"State: {alert.state.value.upper()}\n"
                f"Message: {alert.message}"
            )
            if alert.value is not None and alert.threshold is not None:
                text += f"\nValue: {alert.value:.4g} (threshold: {alert.threshold:.4g})"
            if alert.labels:
                text += f"\nLabels: {', '.join(f'{k}={v}' for k, v in alert.labels.items())}"

            for chat_id in self.chat_ids:
                try:
                    await session.post(
                        f"{base_url}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": text,
                            "parse_mode": "HTML",
                        },
                    )
                except Exception:
                    pass  # Log but continue

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# --- Default Alert Rules ---

def create_default_rules() -> list[AlertRule]:
    """Create default alert rules for the trading bot."""
    from backend.monitoring.metrics import (
        risk_daily_pnl_pct,
        risk_exposure_pct,
        risk_halted,
        risk_violations_total,
        portfolio_equity,
        portfolio_unrealized_pnl,
        open_positions,
        portfolio_cash,
        registry,
    )

    def get_metric(gauge):
        try:
            # Get the current value from the gauge using the registry
            samples = list(registry.collect())
            for sample in samples:
                for s in sample.samples:
                    if s.name == gauge._name:
                        return s.value
        except Exception:
            pass
        return None

    return [
        # Risk alerts
        AlertRule(
            name="daily_loss_limit",
            severity=AlertSeverity.CRITICAL,
            query=lambda: get_metric(risk_daily_pnl_pct) or 0,
            threshold=-0.03,  # -3%
            operator="<",
            for_duration=60,
            description="Daily PnL exceeded max daily loss limit (3%)",
            labels={"component": "risk"},
            cooldown=300,
        ),
        AlertRule(
            name="exposure_limit",
            severity=AlertSeverity.WARNING,
            query=lambda: get_metric(risk_exposure_pct) or 0,
            threshold=0.30,  # 30%
            operator=">",
            for_duration=60,
            description="Total exposure exceeded limit (30%)",
            labels={"component": "risk"},
            cooldown=300,
        ),
        AlertRule(
            name="trading_halted",
            severity=AlertSeverity.CRITICAL,
            query=lambda: get_metric(risk_halted) or 0,
            threshold=0.5,  # 1 = halted
            operator=">=",
            for_duration=0,
            description="Trading has been halted (risk gate or manual /kill)",
            labels={"component": "risk"},
            cooldown=60,
        ),
        AlertRule(
            name="risk_violation",
            severity=AlertSeverity.WARNING,
            query=lambda: get_metric(risk_violations_total) or 0,
            threshold=0,
            operator=">",
            for_duration=0,
            description="Risk violation detected",
            labels={"component": "risk"},
            cooldown=60,
        ),

        # Portfolio alerts
        AlertRule(
            name="low_equity",
            severity=AlertSeverity.WARNING,
            query=lambda: get_metric(portfolio_equity) or 0,
            threshold=500000,  # 50% of initial 1M
            operator="<",
            for_duration=300,
            description="Portfolio equity dropped below 50% of initial",
            labels={"component": "portfolio"},
            cooldown=600,
        ),
        AlertRule(
            name="large_unrealized_loss",
            severity=AlertSeverity.WARNING,
            query=lambda: get_metric(portfolio_unrealized_pnl) or 0,
            threshold=-50000,
            operator="<",
            for_duration=120,
            description="Large unrealized loss detected",
            labels={"component": "portfolio"},
            cooldown=300,
        ),
        AlertRule(
            name="too_many_positions",
            severity=AlertSeverity.WARNING,
            query=lambda: get_metric(open_positions) or 0,
            threshold=8,  # Near max of 10
            operator=">=",
            for_duration=60,
            description="Number of open positions approaching limit",
            labels={"component": "portfolio"},
            cooldown=300,
        ),
        AlertRule(
            name="low_cash",
            severity=AlertSeverity.WARNING,
            query=lambda: get_metric(portfolio_cash) or 0,
            threshold=100000,  # Below 10% of initial
            operator="<",
            for_duration=300,
            description="Available cash running low",
            labels={"component": "portfolio"},
            cooldown=600,
        ),
    ]


def create_alert_manager(
    telegram_bot_token: str | None = None,
    chat_ids: list[int] | None = None,
) -> AlertManager:
    """Factory for AlertManager with default rules and optional Telegram dispatcher."""
    dispatcher = None
    if telegram_bot_token and chat_ids:
        from backend.monitoring.alerting import TelegramAlertDispatcher
        dispatcher = TelegramAlertDispatcher(telegram_bot_token, chat_ids)

    manager = AlertManager(telegram_dispatcher=dispatcher)
    manager.add_rules(create_default_rules())
    return manager


async def run_alert_manager(manager: AlertManager, interval: float = 30) -> None:
    """Run the alert manager loop."""
    await manager.run_loop(interval)


if __name__ == "__main__":
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO)

    # Test run
    manager = create_alert_manager()
    asyncio.run(manager.run_loop(interval=10))