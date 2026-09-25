"""Workspace/config preparation service (Sprint 1 slice).

Later sprints add DocumentIngestionService, ExtractionService,
DriftDetectionService, etc. (#54).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from truthlayer.config import CONFIG_FILENAME, TruthLayerConfig, load_config
from truthlayer.domain.errors import ConfigError, UserInputError


@dataclass(frozen=True)
class CheckPreparation:
    config_path: Path
    config: TruthLayerConfig
    missing_sources: tuple[Path, ...]


def resolve_config_path(path: Path) -> Path:
    """Accept either a config file or a workspace directory containing one."""
    if path.is_dir():
        candidate = path / CONFIG_FILENAME
        if not candidate.is_file():
            raise ConfigError(
                f"no {CONFIG_FILENAME} found in directory: {path}"
            )
        return candidate
    if not path.is_file():
        raise UserInputError(f"path does not exist: {path}")
    return path


def prepare_check(path: str | Path) -> CheckPreparation:
    """Validate workspace config and report missing source directories."""
    config_path = resolve_config_path(Path(path))
    config = load_config(config_path)

    base_dir = config_path.parent
    missing = tuple(
        base_dir / source.path
        for source in config.sources
        if not (base_dir / source.path).exists()
    )
    return CheckPreparation(
        config_path=config_path,
        config=config,
        missing_sources=missing,
    )
