"""Unit tests for Prometheus metrics."""

from __future__ import annotations

import pytest

from backend.monitoring.metrics import (
    CONTENT_TYPE_LATEST,
    get_metrics,
    observe_http_request,
    observe_analysis,
    observe_backtest,
    observe_trade,
    observe_portfolio,
    observe_risk,
    observe_violation,
    observe_approval,
    observe_llm,
    observe_rag,
    observe_market_data,
    registry,
)


class TestMetrics:
    def test_get_metrics_returns_prometheus_format(self):
        output = get_metrics()
        assert isinstance(output, bytes)
        decoded = output.decode()
        assert "http_requests_total" in decoded
        assert "analysis_requests_total" in decoded
        assert "trade_orders_total" in decoded

    def test_content_type(self):
        # prometheus-client version may vary
        assert CONTENT_TYPE_LATEST.startswith("text/plain; version=")
        assert "charset=utf-8" in CONTENT_TYPE_LATEST

    def test_observe_http_request(self):
        observe_http_request("GET", "/analyze", 200, 0.123)
        output = get_metrics().decode()
        assert 'http_requests_total{endpoint="/analyze",method="GET",status="200"} 1.0' in output

    def test_observe_analysis(self):
        observe_analysis("lean", "NSE", "LONG", 1.5, 0.85)
        output = get_metrics().decode()
        assert 'analysis_requests_total{engine="lean",market="NSE",signal="LONG"} 1.0' in output

    def test_observe_backtest(self):
        observe_backtest("NSE", "ok", 23, 1.2, 0.65, 0.15)
        output = get_metrics().decode()
        assert 'backtest_runs_total{market="NSE",status="ok"} 1.0' in output

    def test_observe_trade(self):
        observe_trade("paper", "NSE", "LONG", "filled", pnl=500.0, r_multiple=1.5)
        output = get_metrics().decode()
        assert 'trade_orders_total{market="NSE",mode="paper",side="LONG",status="filled"} 1.0' in output

    def test_observe_portfolio(self):
        observe_portfolio("paper", 100000.0, 50000.0, 1000.0, 2000.0, 3)
        output = get_metrics().decode()
        assert 'portfolio_equity{mode="paper"} 100000.0' in output

    def test_observe_risk(self):
        observe_risk(0.02, 0.25, False)
        output = get_metrics().decode()
        assert "risk_daily_pnl_pct 0.02" in output
        assert "risk_exposure_pct 0.25" in output
        assert "risk_halted 0.0" in output

        observe_risk(-0.01, 0.30, True)
        output = get_metrics().decode()
        assert "risk_halted 1.0" in output

    def test_observe_violation(self):
        observe_violation("max_position", "WARN")
        output = get_metrics().decode()
        assert 'risk_violations_total{rule="max_position",severity="WARN"} 1.0' in output

    def test_observe_approval(self):
        observe_approval("approved", 45.0)
        output = get_metrics().decode()
        assert 'approval_requests_total{status="approved"} 1.0' in output

    def test_metrics_registry_not_empty(self):
        # Verify registry has our custom metrics
        output = get_metrics().decode()
        metric_names = [
            "http_requests_total",
            "analysis_requests_total",
            "backtest_runs_total",
            "trade_orders_total",
            "portfolio_equity",
            "risk_daily_pnl_pct",
            "risk_violations_total",
            "approval_requests_total",
            "llm_requests_total",
            "rag_retrievals_total",
            "market_data_requests_total",
        ]
        for name in metric_names:
            assert name in output, f"Missing metric: {name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])