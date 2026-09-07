"""Tests for the run_cloud keep-alive URL selection."""

from backend.run_cloud import resolve_keepalive_url


def test_keepalive_url_prefers_explicit_setting() -> None:
    assert resolve_keepalive_url(
        "https://custom.example.com",
        env_values={"RENDER_EXTERNAL_URL": "https://render.example.com"},
    ) == "https://custom.example.com"


def test_keepalive_url_falls_back_to_render_external() -> None:
    assert resolve_keepalive_url(
        "",
        env_values={"RENDER_EXTERNAL_URL": "https://render.example.com"},
    ) == "https://render.example.com"


def test_keepalive_url_falls_back_to_render_url() -> None:
    assert resolve_keepalive_url(
        "",
        env_values={"RENDER_URL": "https://render-fallback.example.com"},
    ) == "https://render-fallback.example.com"


def test_keepalive_url_external_wins_over_render_url() -> None:
    assert resolve_keepalive_url(
        "",
        env_values={
            "RENDER_EXTERNAL_URL": "https://primary.example.com",
            "RENDER_URL": "https://fallback.example.com",
        },
    ) == "https://primary.example.com"


def test_keepalive_url_none_when_unconfigured() -> None:
    assert resolve_keepalive_url("", env_values={}) is None