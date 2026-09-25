"""Token estimator tests."""

from __future__ import annotations

from truthlayer.ingestion.tokens import estimate_tokens


def test_empty_text() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("   \n\t  ") == 0


def test_latin_words() -> None:
    assert estimate_tokens("price is 99 yuan") == 4  # price / is / 99 / yuan


def test_cjk_each_character() -> None:
    assert estimate_tokens("标准价格") == 4


def test_mixed_text() -> None:
    assert estimate_tokens("价格 price 是 99") == 5  # 价格(2) price 是(1) 99


def test_deterministic() -> None:
    text = "Standard price in 2026: ¥129（含税）。"
    assert estimate_tokens(text) == estimate_tokens(text)
