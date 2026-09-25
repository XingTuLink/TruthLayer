"""Build provider instances from validated config + environment variables.

Credentials live only in environment variables named by the config slot
(defaults: TRUTHLAYER_LLM_API_KEY / TRUTHLAYER_EMBEDDING_API_KEY). They are
never read from the YAML file, logged, or persisted (#68 credential hygiene
starts here even though the API arrives in Phase 1).
"""

from __future__ import annotations

import os

from truthlayer.config import EmbeddingConfig, ExtractionConfig
from truthlayer.domain.errors import ConfigError
from truthlayer.providers.openai_compatible import (
    OpenAICompatibleEmbedder,
    OpenAICompatibleLLM,
    is_local_endpoint,
)

DEFAULT_LLM_KEY_ENV = "TRUTHLAYER_LLM_API_KEY"
DEFAULT_EMBEDDING_KEY_ENV = "TRUTHLAYER_EMBEDDING_API_KEY"


def _resolve_api_key(
    *, base_url: str | None, api_key_env: str | None, default_env: str
) -> str | None:
    env_name = api_key_env or default_env
    key = os.environ.get(env_name)
    if key:
        return key
    if is_local_endpoint(base_url):
        return None  # Ollama ignores credentials entirely.
    raise ConfigError(
        f"missing API key: set environment variable {env_name} "
        f"(endpoint {base_url or 'api.openai.com'} requires authentication)"
    )


def build_llm(extraction: ExtractionConfig) -> OpenAICompatibleLLM:
    api_key = _resolve_api_key(
        base_url=extraction.base_url,
        api_key_env=extraction.api_key_env,
        default_env=DEFAULT_LLM_KEY_ENV,
    )
    return OpenAICompatibleLLM(
        model=extraction.model,
        base_url=extraction.base_url,
        api_key=api_key,
    )


def build_embedder(
    embedding: EmbeddingConfig | None,
) -> OpenAICompatibleEmbedder | None:
    if embedding is None:
        return None
    api_key = _resolve_api_key(
        base_url=embedding.base_url,
        api_key_env=embedding.api_key_env,
        default_env=DEFAULT_EMBEDDING_KEY_ENV,
    )
    return OpenAICompatibleEmbedder(
        model=embedding.model,
        base_url=embedding.base_url,
        api_key=api_key,
        dimensions=embedding.dimensions,
    )
