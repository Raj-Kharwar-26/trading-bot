"""Tests for Telegram command arg sanitisation."""

import pytest

from backend.telegram.bot import _clean_args


def test_clean_args_plain_spaces() -> None:
    assert _clean_args(["uni-usdt", "long", "23", "0.1000", "crypto"]) == [
        "uni-usdt",
        "long",
        "23",
        "0.1000",
        "crypto",
    ]


def test_clean_args_zero_width_spaces() -> None:
    glued = ["uni-usdt\u200blong\u200b23\u200b0.1000\u200bcrypto"]
    assert _clean_args(glued) == ["uni-usdt", "long", "23", "0.1000", "crypto"]


def test_clean_args_mixed_unicode_separators() -> None:
    glued = ["uni-usdt\u00a0long\u200c23\u20600.1000\ufeffcrypto"]
    assert _clean_args(glued) == ["uni-usdt", "long", "23", "0.1000", "crypto"]


def test_clean_args_none_and_empty() -> None:
    assert _clean_args(None) == []
    assert _clean_args([]) == []