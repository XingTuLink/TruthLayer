"""Deterministic hashes for idempotency (#35).

- ``file_hash``    : SHA-256 of the raw file bytes.
- ``content_hash`` : SHA-256 of canonicalized parsed content. Same parsed
                     content must always hash identically, regardless of
                     dict/block ordering artifacts (#30).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence

from truthlayer.providers.parser_protocol import ParsedBlock

#: SHA-256 of empty bytes — used for documents that failed before parsing.
EMPTY_CONTENT_HASH = hashlib.sha256(b"").hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: str | os.PathLike[str]) -> str:
    """SHA-256 of raw file bytes (streamed)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_blocks(blocks: Sequence[ParsedBlock]) -> str:
    """Stable hash of parsed-and-normalized block content.

    Canonical JSON (sorted keys, no ASCII escaping) of the block sequence;
    block ORDER is significant (reading order), field order is not.
    """
    payload = [block.model_dump() for block in blocks]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
