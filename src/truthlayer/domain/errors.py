"""Typed error hierarchy (#34).

Errors must keep their context — bare ``except Exception: pass`` is forbidden.
Exit-code / HTTP-status mapping happens at the edge (CLI / API), not here.
"""

from __future__ import annotations


class TruthLayerError(Exception):
    """Base class for all TruthLayer errors."""


class UserInputError(TruthLayerError):
    """Invalid CLI arguments or user-supplied paths."""


class ConfigError(TruthLayerError):
    """Missing or invalid .truthlayer.yaml configuration."""


class ParserError(TruthLayerError):
    """Document parsing failed."""


class ProviderError(TruthLayerError):
    """LLM / embedding provider failure (auth, timeout, retry exhausted)."""


class DatabaseError(TruthLayerError):
    """Database / migration / session failure."""


class DomainValidationError(TruthLayerError):
    """A domain rule was violated (e.g. Fact XOR, invalid temporal window)."""


class DetectorError(TruthLayerError):
    """A drift detector failed during a scan."""


class ReportError(TruthLayerError):
    """HTML / JSON report rendering failed."""
