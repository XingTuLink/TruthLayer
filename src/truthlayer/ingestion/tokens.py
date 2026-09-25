"""Deterministic, dependency-free token estimator.

Phase 0 deliberately avoids a model-specific tokenizer (#14: start simple).
The estimator is stable and reproducible — Knowledge Hash / chunk indexes
must not depend on third-party tokenizer versions:

- each Han (CJK unified ideograph) character counts as one token;
- each maximal run of latin letters/digits (with internal dots) counts as one;
- whitespace and punctuation do not add tokens (they bound runs).

This underestimates punctuation but gives consistent, comparable counts.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(
    r"[一-鿿]"                      # single Han character
    r"|[A-Za-z0-9]+(?:\.[0-9]+)*"  # word / number run, e.g. gpt-4o splits to gpt/4o
)


def estimate_tokens(text: str) -> int:
    return len(_TOKEN_RE.findall(text))
