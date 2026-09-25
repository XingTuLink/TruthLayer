"""DocumentParser contract (#24).

Parser output must carry enough location information to build Evidence:
page, paragraph, xlsx row/sheet, and offsets when available.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class ParsedBlock(BaseModel):
    """One parse unit (paragraph / row / text window) with provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=0)
    row: int | None = Field(default=None, ge=0)
    sheet: str | None = None
    offset_start: int | None = Field(default=None, ge=0)
    offset_end: int | None = Field(default=None, ge=0)


class DocumentParser(Protocol):
    """Implemented by PdfParser / DocxParser / XlsxParser / text parsers."""

    #: File extensions this parser accepts, e.g. frozenset({".pdf"}).
    extensions: frozenset[str]

    def parse(self, path: Path) -> Sequence[ParsedBlock]:
        ...
