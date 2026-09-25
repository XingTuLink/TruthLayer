"""Config loader tests (#25)."""

from __future__ import annotations

from pathlib import Path

import pytest

from truthlayer.config import load_config
from truthlayer.domain.enums import CIFailOn, Severity
from truthlayer.domain.errors import ConfigError


def test_load_valid_config(config_file: Path) -> None:
    cfg = load_config(config_file)

    assert cfg.workspace.name == "acme-corp"
    assert len(cfg.sources) == 1
    assert cfg.sources[0].authority == pytest.approx(0.95)
    assert cfg.rules.stale_after_days == 365
    assert cfg.rules.pricing_stale_days == 90
    assert cfg.severity == {
        "pricing_change": Severity.HIGH,
        "policy_change": Severity.CRITICAL,
    }
    assert cfg.ci.fail_on is CIFailOn.CRITICAL
    assert cfg.version_mapping[0].group == "pricing_policy"
    assert cfg.version_mapping[0].files[1].supersedes == "pricing_2025.xlsx"
    assert cfg.extraction is not None
    assert cfg.extraction.model == "gpt-4o-mini"
    assert cfg.extraction.provider == "openai_compatible"
    assert cfg.embedding is not None
    assert cfg.embedding.model == "text-embedding-3-small"
    assert cfg.embedding.dimensions == 1536


def test_provider_endpoint_slots(tmp_path: Path) -> None:
    cfg_file = tmp_path / ".truthlayer.yaml"
    cfg_file.write_text(
        "workspace:\n  name: x\n"
        "extraction:\n"
        "  provider: openai_compatible\n"
        "  model: qwen2.5:7b\n"
        "  base_url: http://localhost:11434/v1\n"
        "embedding:\n"
        "  provider: openai_compatible\n"
        "  model: bge-m3\n"
        "  base_url: http://localhost:11434/v1\n"
        "  dimensions: 1024\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_file)
    assert cfg.extraction is not None
    assert cfg.extraction.base_url == "http://localhost:11434/v1"
    assert cfg.extraction.api_key_env is None
    assert cfg.embedding is not None
    assert cfg.embedding.dimensions == 1024


def test_defaults_when_minimal(tmp_path: Path) -> None:
    cfg_file = tmp_path / ".truthlayer.yaml"
    cfg_file.write_text("workspace:\n  name: minimal\n", encoding="utf-8")

    cfg = load_config(cfg_file)

    assert cfg.sources == []
    assert cfg.rules.stale_after_days == 365
    assert cfg.ci.fail_on is CIFailOn.CRITICAL


def test_unknown_field_rejected(tmp_path: Path) -> None:
    cfg_file = tmp_path / ".truthlayer.yaml"
    cfg_file.write_text(
        "workspace:\n  name: x\nunexpected: true\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError):
        load_config(cfg_file)


def test_bad_fail_on_value(tmp_path: Path) -> None:
    cfg_file = tmp_path / ".truthlayer.yaml"
    cfg_file.write_text(
        "workspace:\n  name: x\nci:\n  fail_on: block\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError):
        load_config(cfg_file)


def test_authority_out_of_range(tmp_path: Path) -> None:
    cfg_file = tmp_path / ".truthlayer.yaml"
    cfg_file.write_text(
        "workspace:\n  name: x\n"
        "sources:\n  - path: ./docs\n    authority: 1.5\n    type: policy\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(cfg_file)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_empty_file_raises(tmp_path: Path) -> None:
    cfg_file = tmp_path / ".truthlayer.yaml"
    cfg_file.write_text("", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(cfg_file)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    cfg_file = tmp_path / ".truthlayer.yaml"
    cfg_file.write_text("workspace:\n  name: x\n   bad: : :\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(cfg_file)
