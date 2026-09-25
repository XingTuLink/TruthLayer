""".truthlayer.yaml schema and loader (#25).

The parser must validate the schema and produce clear error messages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from truthlayer.domain.enums import CIFailOn, Severity
from truthlayer.domain.errors import ConfigError

AuthorityScore = Annotated[float, Field(ge=0.0, le=1.0)]
CONFIG_FILENAME = ".truthlayer.yaml"


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    authority: AuthorityScore = 0.9
    type: str = Field(min_length=1)


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stale_after_days: int = Field(default=365, ge=1)
    pricing_stale_days: int | None = Field(default=None, ge=1)
    # Predicates that are legitimately multi-valued (e.g. tiered benefits);
    # reserved escape hatch for future cardinality support (#19).
    multi_valued_predicates: list[str] = Field(default_factory=list)


class CIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fail_on: CIFailOn = CIFailOn.CRITICAL


class VersionFileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    label: str | None = None
    supersedes: str | None = None


class VersionMappingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str = Field(min_length=1)
    files: list[VersionFileConfig] = Field(min_length=1)


class ProviderEndpointConfig(BaseModel):
    """One OpenAI-compatible endpoint slot (#22, #23).

    Cloud gateways, the official OpenAI API and local Ollama all speak the
    same protocol; they differ only by base_url / model / credential.
    ``api_key_env`` names the environment variable holding the key — the
    secret itself is never written into the config file. Local endpoints
    (Ollama) need no key at all.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key_env: str | None = Field(default=None, min_length=1)


class ExtractionConfig(ProviderEndpointConfig):
    """LLM slot used for candidate fact/entity extraction."""


class EmbeddingConfig(ProviderEndpointConfig):
    """Embedding slot; optional until drift detection needs recall (#23)."""

    dimensions: int | None = Field(default=None, ge=1)


class TruthLayerConfig(BaseModel):
    """Validated shape of .truthlayer.yaml."""

    model_config = ConfigDict(extra="forbid")

    workspace: WorkspaceConfig
    sources: list[SourceConfig] = Field(default_factory=list)
    rules: RulesConfig = Field(default_factory=RulesConfig)
    severity: dict[str, Severity] = Field(default_factory=dict)
    ci: CIConfig = Field(default_factory=CIConfig)
    version_mapping: list[VersionMappingConfig] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)
    extraction: ExtractionConfig | None = None
    embedding: EmbeddingConfig | None = None


def load_config(path: str | Path) -> TruthLayerConfig:
    """Load and validate a .truthlayer.yaml file.

    Raises:
        ConfigError: file missing, invalid YAML, or schema violation.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if raw is None:
        raise ConfigError(f"config file is empty: {path}")
    if not isinstance(raw, dict):
        raise ConfigError(
            f"config root must be a mapping, got {type(raw).__name__}: {path}"
        )

    try:
        return TruthLayerConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid config {path}:\n{exc}") from exc
