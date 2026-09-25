"""Provider factory credential resolution tests."""

from __future__ import annotations

import pytest

from truthlayer.config import EmbeddingConfig, ExtractionConfig
from truthlayer.domain.errors import ConfigError
from truthlayer.providers.factory import (
    DEFAULT_EMBEDDING_KEY_ENV,
    DEFAULT_LLM_KEY_ENV,
    build_embedder,
    build_llm,
)


def _llm(**overrides) -> ExtractionConfig:
    base = dict(provider="openai_compatible", model="gpt-4o-mini")
    base.update(overrides)
    return ExtractionConfig(**base)


def test_local_endpoint_needs_no_key(monkeypatch) -> None:
    monkeypatch.delenv(DEFAULT_LLM_KEY_ENV, raising=False)
    llm = build_llm(_llm(base_url="http://localhost:11434/v1"))
    assert llm.model == "gpt-4o-mini"


def test_remote_endpoint_requires_key(monkeypatch) -> None:
    monkeypatch.delenv(DEFAULT_LLM_KEY_ENV, raising=False)
    with pytest.raises(ConfigError, match=DEFAULT_LLM_KEY_ENV):
        build_llm(_llm(base_url="https://api.deepseek.com/v1"))


def test_remote_endpoint_reads_named_env(monkeypatch) -> None:
    monkeypatch.setenv("MY_LLM_KEY", "sk-secret")
    llm = build_llm(
        _llm(
            base_url="https://api.deepseek.com/v1",
            api_key_env="MY_LLM_KEY",
        )
    )
    assert llm.model == "gpt-4o-mini"


def test_no_embedding_config_returns_none() -> None:
    assert build_embedder(None) is None


def test_embedding_remote_key_env(monkeypatch) -> None:
    monkeypatch.delenv(DEFAULT_EMBEDDING_KEY_ENV, raising=False)
    with pytest.raises(ConfigError, match=DEFAULT_EMBEDDING_KEY_ENV):
        build_embedder(
            EmbeddingConfig(
                provider="openai_compatible",
                model="bge-m3",
                base_url="https://gateway.example/v1",
            )
        )
