"""Deterministic text normalization for parsed content.

Kept deliberately conservative: normalization must never change the meaning
of a quote, only remove presentation noise (CRLF, BOM already handled by
readers, trailing whitespace, unicode compatibility forms).
"""

from __future__ import annotations

import unicodedata


def normalize_text(text: str) -> str:
    """NFC + CRLF→LF + per-line strip + outer strip.

    Internal blank lines and paragraph structure are preserved; stripping
    indentation on every line is safe because leading spaces inside a
    paragraph are presentation noise for QA purposes.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()
