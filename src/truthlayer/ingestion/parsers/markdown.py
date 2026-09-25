"""Markdown parser (native, Phase 0).

Markdown is valid text; paragraph grouping is sufficient. Structural
handling (headers/lists as metadata) can be added later without changing
the ParsedBlock contract.
"""

from __future__ import annotations

from pathlib import Path

from truthlayer.ingestion.parsers._encoding import decode_bytes
from truthlayer.ingestion.parsers.text import parse_paragraphs
from truthlayer.providers.parser_protocol import ParsedBlock


class MarkdownParser:
    extensions = frozenset({".md", ".markdown"})

    def parse(self, path: Path) -> list[ParsedBlock]:
        text = decode_bytes(Path(path).read_bytes())
        return parse_paragraphs(text)
