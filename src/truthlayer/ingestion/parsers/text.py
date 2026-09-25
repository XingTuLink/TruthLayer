"""Plain-text parser."""

from __future__ import annotations

from pathlib import Path

from truthlayer.ingestion.parsers._encoding import decode_bytes
from truthlayer.providers.parser_protocol import ParsedBlock


def parse_paragraphs(text: str) -> list[ParsedBlock]:
    """Group consecutive non-empty lines into paragraph blocks.

    Character offsets are tracked on the decoded string for Evidence.
    """
    blocks: list[ParsedBlock] = []
    lines = text.split("\n")
    cursor = 0
    buffer: list[str] = []
    start = 0
    paragraph_index = 0

    def flush(end: int) -> None:
        nonlocal buffer, start, paragraph_index
        if buffer:
            # Exclude the line terminator(s) that separate paragraphs.
            while end > start and text[end - 1] == "\n":
                end -= 1
            blocks.append(
                ParsedBlock(
                    text="\n".join(buffer).strip(),
                    paragraph=paragraph_index,
                    offset_start=start,
                    offset_end=end,
                )
            )
            paragraph_index += 1
            buffer = []

    for line in lines:
        line_end = cursor + len(line)
        if line.strip():
            if not buffer:
                start = cursor
            buffer.append(line.strip())
        else:
            flush(cursor)
        cursor = line_end + 1  # +1 for the stripped "\n"

    flush(cursor - 1)
    return [block for block in blocks if block.text]


class TextParser:
    extensions = frozenset({".txt"})

    def parse(self, path: Path) -> list[ParsedBlock]:
        text = decode_bytes(Path(path).read_bytes())
        return parse_paragraphs(text)
