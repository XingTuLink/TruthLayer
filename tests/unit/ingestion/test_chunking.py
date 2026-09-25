"""Chunker tests (#14)."""

from __future__ import annotations

import pytest

from truthlayer.ingestion.chunking import chunk_blocks, split_long_line
from truthlayer.ingestion.tokens import estimate_tokens
from truthlayer.providers.parser_protocol import ParsedBlock


def test_empty_blocks() -> None:
    assert chunk_blocks([]) == []


def test_blank_only_blocks_dropped() -> None:
    # ParsedBlock forbids empty strings; whitespace-only blocks still drop.
    assert chunk_blocks([ParsedBlock(text="   \n  ")]) == []


def test_small_blocks_pack_into_one_chunk() -> None:
    blocks = [
        ParsedBlock(text="price is 99", page=1),
        ParsedBlock(text="price is 129", page=1),
    ]
    chunks = chunk_blocks(blocks)

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].token_count == estimate_tokens(chunks[0].text)
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 1
    assert chunks[0].block_indexes == (0, 1)
    assert "99" in chunks[0].text and "129" in chunks[0].text


def test_chunk_flushes_when_budget_exceeded() -> None:
    blocks = [
        ParsedBlock(text=" ".join(["word"] * 300)),
        ParsedBlock(text=" ".join(["word"] * 300)),
    ]
    chunks = chunk_blocks(blocks, max_tokens=512)

    assert len(chunks) == 2
    assert all(chunk.token_count <= 512 for chunk in chunks)
    assert chunks[0].block_indexes == (0,)
    assert chunks[1].block_indexes == (1,)


def test_chunk_indexes_are_contiguous() -> None:
    blocks = [ParsedBlock(text=" ".join(["x"] * 400)) for _ in range(4)]
    chunks = chunk_blocks(blocks, max_tokens=512)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))


def test_page_range_across_packed_blocks() -> None:
    blocks = [
        ParsedBlock(text="a", page=3),
        ParsedBlock(text="b", page=5),
    ]
    chunks = chunk_blocks(blocks)
    assert chunks[0].page_start == 3
    assert chunks[0].page_end == 5


def test_overlong_line_is_split_into_windows() -> None:
    line = " ".join(["word"] * 1000)
    windows = split_long_line(line, max_tokens=512, overlap_tokens=64)

    assert len(windows) >= 2
    assert all(estimate_tokens(window) <= 512 for window in windows)
    # Overlap: the tail of window 1 reappears at the head of window 2.
    tail = windows[0].split()[-10:]
    assert all(token in windows[1] for token in tail[-5:])


def test_overlong_cjk_line_is_split() -> None:
    line = "价格" * 1000  # 2000 Han tokens
    blocks = [ParsedBlock(text=line)]
    chunks = chunk_blocks(blocks, max_tokens=512, overlap_tokens=64)

    assert len(chunks) >= 3
    assert all(chunk.token_count <= 512 for chunk in chunks)


def test_invalid_parameters_rejected() -> None:
    with pytest.raises(ValueError):
        chunk_blocks([], max_tokens=0)
    with pytest.raises(ValueError):
        chunk_blocks([], max_tokens=512, overlap_tokens=512)
