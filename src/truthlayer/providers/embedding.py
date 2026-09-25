"""EmbeddingProvider contract (#23, #4).

Embeddings only retrieve candidate pairs — similarity must never be treated
as the final duplicate/conflict verdict.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Batch-embed texts; one vector per input, in input order."""
        ...
