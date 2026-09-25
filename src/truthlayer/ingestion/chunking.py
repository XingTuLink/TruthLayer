"""Paragraph / sliding-window chunker (#14).

Start with ~512-token windows; do not build a complex chunker in Phase 0.

Strategy:

1. Blocks are split into non-empty lines; lines are greedily packed.
2. A line longer than ``max_tokens`` is hard-sliced into overlapping windows
   on estimator token spans (original substrings are preserved).
3. ``chunk_index`` is stable for a given deterministic block sequence, which
   is required by idempotency (#35).

The token estimator only counts Han characters and word runs, so the line
separators themselves add zero tokens; the running budget is therefore exact
with respect to :func:`estimate_tokens`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from truthlayer.ingestion.tokens import _TOKEN_RE, estimate_tokens
from truthlayer.providers.parser_protocol import ParsedBlock

DEFAULT_MAX_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 64


@dataclass(frozen=True)
class ChunkSpec:
    chunk_index: int
    text: str
    token_count: int
    page_start: int | None
    page_end: int | None
    block_indexes: tuple[int, ...] = field(default_factory=tuple)


@dataclass
class _Unit:
    text: str
    page: int | None
    block_index: int


def split_long_line(
    line: str, max_tokens: int, overlap_tokens: int
) -> list[str]:
    """Slice an over-long line into overlapping original-substring windows."""
    spans = [(match.start(), match.end()) for match in _TOKEN_RE.finditer(line)]
    if not spans:
        return []

    step = max(1, max_tokens - overlap_tokens)
    windows: list[str] = []
    start_idx = 0
    while start_idx < len(spans):
        end_idx = min(start_idx + max_tokens, len(spans))
        piece = line[spans[start_idx][0] : spans[end_idx - 1][1]].strip()
        if piece:
            windows.append(piece)
        if end_idx == len(spans):
            break
        start_idx += step
    return windows


def _build_units(
    blocks: Sequence[ParsedBlock], max_tokens: int, overlap_tokens: int
) -> list[_Unit]:
    units: list[_Unit] = []
    for block_index, block in enumerate(blocks):
        for line in block.text.split("\n"):
            if not line.strip():
                continue
            line = line.strip()
            if estimate_tokens(line) <= max_tokens:
                pieces = [line]
            else:
                pieces = split_long_line(line, max_tokens, overlap_tokens)
            units.extend(
                _Unit(text=piece, page=block.page, block_index=block_index)
                for piece in pieces
            )
    return units


def _assemble(units: Sequence[_Unit]) -> str:
    parts: list[str] = []
    previous_block: int | None = None
    for unit in units:
        if previous_block is not None:
            parts.append("\n" if unit.block_index == previous_block else "\n\n")
        parts.append(unit.text)
        previous_block = unit.block_index
    return "".join(parts)


def chunk_blocks(
    blocks: Sequence[ParsedBlock],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[ChunkSpec]:
    """Pack parsed blocks into an ordered list of ChunkSpec."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be in [0, max_tokens)")

    units = _build_units(blocks, max_tokens, overlap_tokens)
    chunks: list[ChunkSpec] = []
    current: list[_Unit] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        text = _assemble(current)
        pages = [unit.page for unit in current if unit.page is not None]
        block_indexes = tuple(sorted({unit.block_index for unit in current}))
        chunks.append(
            ChunkSpec(
                chunk_index=len(chunks),
                text=text,
                token_count=estimate_tokens(text),
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                block_indexes=block_indexes,
            )
        )
        current = []
        current_tokens = 0

    for unit in units:
        piece_tokens = estimate_tokens(unit.text)
        if current and current_tokens + piece_tokens > max_tokens:
            flush()
        current.append(unit)
        current_tokens += piece_tokens

    flush()
    return chunks
