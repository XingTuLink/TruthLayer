"""OpenAI-compatible provider tests with a faked SDK client.

Covers the three-tier structured-output fallback, fail-fast auth, fence
stripping, and embedding dimension enforcement — no network involved.
"""

from __future__ import annotations

import json

import pytest

from truthlayer.domain.errors import ProviderError
from truthlayer.providers import openai_compatible as mod
from truthlayer.providers.openai_compatible import (
    OpenAICompatibleEmbedder,
    OpenAICompatibleLLM,
    is_local_endpoint,
)


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _ChatResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class BadRequestError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class _Completions:
    def __init__(self, behaviors: list[Exception | str]) -> None:
        self._behaviors = behaviors
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return _ChatResponse(behavior)


class _Chat:
    def __init__(self, behaviors) -> None:
        self.completions = _Completions(behaviors)


class _EmbeddingItem:
    def __init__(self, vector: list[float]) -> None:
        self.embedding = vector


class _EmbeddingResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_EmbeddingItem(v) for v in vectors]


class _Embeddings:
    def __init__(self, vectors, fail: Exception | None = None) -> None:
        self._vectors = vectors
        self._fail = fail
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail:
            raise self._fail
        return _EmbeddingResponse(self._vectors)


class _FakeClient:
    def __init__(self, *, chat_behaviors=None, emb_vectors=None, emb_fail=None):
        self.chat = _Chat(chat_behaviors or [])
        self.embeddings = _Embeddings(emb_vectors or [], emb_fail)


@pytest.fixture
def patched_client(monkeypatch):
    holder: dict[str, _FakeClient] = {}

    def factory(*, chat_behaviors=None, emb_vectors=None, emb_fail=None):
        client = _FakeClient(
            chat_behaviors=chat_behaviors,
            emb_vectors=emb_vectors,
            emb_fail=emb_fail,
        )
        holder["client"] = client
        return client

    monkeypatch.setattr(
        mod, "_load_openai", lambda: (lambda **kw: holder["client"])
    )
    return factory


def test_is_local_endpoint() -> None:
    assert is_local_endpoint("http://localhost:11434/v1")
    assert is_local_endpoint("http://127.0.0.1:11434")
    assert not is_local_endpoint("https://api.openai.com/v1")
    assert not is_local_endpoint(None)


def test_strict_structured_output_success(patched_client) -> None:
    from truthlayer.extraction.schemas import ExtractionEnvelope

    payload = json.dumps({"entities": [], "facts": []}, ensure_ascii=False)
    patched_client(chat_behaviors=[payload])

    llm = OpenAICompatibleLLM(model="qwen2.5:7b", base_url="http://localhost:11434/v1")
    result = llm.generate_structured(
        input_text="hello",
        output_schema=ExtractionEnvelope,
        system_prompt="sys",
    )
    assert isinstance(result, ExtractionEnvelope)
    call = llm._client.chat.completions.calls[0]
    assert call["response_format"]["type"] == "json_schema"
    assert call["temperature"] == 0


def test_fallback_to_json_mode_then_prompt_only(patched_client) -> None:
    from truthlayer.extraction.schemas import ExtractionEnvelope

    payload = json.dumps({"entities": [], "facts": []})
    patched_client(
        chat_behaviors=[
            BadRequestError("unsupported response_format"),
            BadRequestError("json object not supported"),
            payload,
        ]
    )
    llm = OpenAICompatibleLLM(model="x")
    result = llm.generate_structured(
        input_text="hello", output_schema=ExtractionEnvelope
    )
    assert isinstance(result, ExtractionEnvelope)
    formats = [c.get("response_format") for c in llm._client.chat.completions.calls]
    assert formats[0]["type"] == "json_schema"
    assert formats[1] == {"type": "json_object"}
    assert formats[2] is None


def test_prompt_only_tier_strips_code_fence(patched_client) -> None:
    from truthlayer.extraction.schemas import ExtractionEnvelope

    fenced = "```json\n" + json.dumps({"entities": [], "facts": []}) + "\n```"
    patched_client(
        chat_behaviors=[
            BadRequestError("no"),
            BadRequestError("no"),
            fenced,
        ]
    )
    llm = OpenAICompatibleLLM(model="x")
    assert isinstance(
        llm.generate_structured(
            input_text="hi", output_schema=ExtractionEnvelope
        ),
        ExtractionEnvelope,
    )


def test_auth_error_fails_fast_without_fallback(patched_client) -> None:
    from truthlayer.extraction.schemas import ExtractionEnvelope

    patched_client(chat_behaviors=[AuthenticationError("401 bad key")])
    llm = OpenAICompatibleLLM(model="x", api_key="bad")
    with pytest.raises(ProviderError, match="authentication"):
        llm.generate_structured(
            input_text="hi", output_schema=ExtractionEnvelope
        )
    assert len(llm._client.chat.completions.calls) == 1


def test_connection_error_fails_fast(patched_client) -> None:
    from truthlayer.extraction.schemas import ExtractionEnvelope

    patched_client(chat_behaviors=[APIConnectionError("refused")])
    llm = OpenAICompatibleLLM(model="x", base_url="http://localhost:11434/v1")
    with pytest.raises(ProviderError, match="cannot reach"):
        llm.generate_structured(
            input_text="hi", output_schema=ExtractionEnvelope
        )


def test_all_tiers_invalid_payload_raises_provider_error(patched_client) -> None:
    from truthlayer.extraction.schemas import ExtractionEnvelope

    patched_client(chat_behaviors=["{not json", "{not json", "{not json"])
    llm = OpenAICompatibleLLM(model="x")
    with pytest.raises(ProviderError, match="every fallback tier"):
        llm.generate_structured(
            input_text="hi", output_schema=ExtractionEnvelope
        )


def test_embedder_returns_vectors_and_records_dim(patched_client) -> None:
    patched_client(emb_vectors=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    embedder = OpenAICompatibleEmbedder(model="bge-m3", base_url="http://localhost:11434")
    vectors = embedder.embed(["a", "b"])
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert embedder.dimensions == 3
    # Ollama must never receive the OpenAI-specific dimensions parameter.
    assert "dimensions" not in embedder._client.embeddings.calls[0]


def test_embedder_cloud_passes_dimensions(patched_client) -> None:
    patched_client(emb_vectors=[[0.0] * 8])
    embedder = OpenAICompatibleEmbedder(
        model="text-embedding-3-small",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        dimensions=8,
    )
    embedder.embed(["a"])
    assert embedder._client.embeddings.calls[0]["dimensions"] == 8


def test_embedder_rejects_dimension_mismatch(patched_client) -> None:
    patched_client(emb_vectors=[[0.0] * 4])
    embedder = OpenAICompatibleEmbedder(
        model="m", base_url="http://localhost:11434", dimensions=8
    )
    with pytest.raises(ProviderError, match="dimension mismatch"):
        embedder.embed(["a"])


def test_embedder_wraps_request_error(patched_client) -> None:
    patched_client(emb_fail=RuntimeError("boom"))
    embedder = OpenAICompatibleEmbedder(model="m", base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="embedding request failed"):
        embedder.embed(["a"])


def test_embedder_empty_input(patched_client) -> None:
    patched_client()
    embedder = OpenAICompatibleEmbedder(model="m", base_url="http://localhost:11434")
    assert embedder.embed([]) == []
