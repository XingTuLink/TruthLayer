"""Normalization tests."""

from __future__ import annotations

from truthlayer.ingestion.normalize import normalize_text


def test_crlf_to_lf() -> None:
    assert normalize_text("a\r\nb") == "a\nb"


def test_strips_outer_and_trailing_whitespace() -> None:
    assert normalize_text("  a  \n b \n\n") == "a\nb"


def test_preserves_internal_blank_lines() -> None:
    assert normalize_text("a\n\nb") == "a\n\nb"


def test_nfc_normalization() -> None:
    # NFD "é" (e + combining acute) must normalize to NFC.
    nfd = "café"
    nfc = "café"
    assert normalize_text(nfd) == nfc


def test_idempotent() -> None:
    text = "Line one.  \r\n Line two.\n"
    assert normalize_text(normalize_text(text)) == normalize_text(text)
