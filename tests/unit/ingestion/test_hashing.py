"""Hashing tests (#30, #35)."""

from __future__ import annotations

import hashlib

from truthlayer.ingestion.hashing import EMPTY_CONTENT_HASH, hash_blocks, hash_bytes
from truthlayer.providers.parser_protocol import ParsedBlock


def test_hash_bytes_known_empty_vector() -> None:
    assert hash_bytes(b"") == hashlib.sha256(b"").hexdigest()
    assert EMPTY_CONTENT_HASH == hashlib.sha256(b"").hexdigest()


def test_hash_bytes_deterministic() -> None:
    assert hash_bytes(b"abc") == hash_bytes(b"abc")
    assert hash_bytes(b"abc") != hash_bytes(b"abd")


def test_content_hash_order_insensitive_for_fields() -> None:
    # Canonical JSON sorts dict keys, so construction-kwarg order does not
    # affect the hash of identical blocks (#30).
    block = ParsedBlock(text="price is 99", page=1, paragraph=0)
    again = ParsedBlock(text="price is 99", paragraph=0, page=1)
    assert hash_blocks([block]) == hash_blocks([again])


def test_content_hash_changes_with_text() -> None:
    first = ParsedBlock(text="price is 99")
    second = ParsedBlock(text="price is 129")
    assert hash_blocks([first]) != hash_blocks([second])


def test_content_hash_block_order_significant() -> None:
    first = ParsedBlock(text="a")
    second = ParsedBlock(text="b")
    assert hash_blocks([first, second]) != hash_blocks([second, first])
