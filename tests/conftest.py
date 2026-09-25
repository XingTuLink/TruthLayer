"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

VALID_CONFIG_YAML = """
workspace:
  name: acme-corp

sources:
  - path: ./docs/policies
    authority: 0.95
    type: policy

rules:
  stale_after_days: 365
  pricing_stale_days: 90

severity:
  pricing_change: high
  policy_change: critical

ci:
  fail_on: critical

version_mapping:
  - group: pricing_policy
    files:
      - path: pricing_2025.xlsx
        label: "2025"
      - path: pricing_2026.xlsx
        label: "2026"
        supersedes: pricing_2025.xlsx

ignore:
  - "**/internal-test/**"

extraction:
  provider: openai_compatible
  model: gpt-4o-mini

embedding:
  provider: openai_compatible
  model: text-embedding-3-small
  dimensions: 1536
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    cfg = tmp_path / ".truthlayer.yaml"
    cfg.write_text(VALID_CONFIG_YAML, encoding="utf-8")
    (tmp_path / "docs" / "policies").mkdir(parents=True)
    return cfg
