"""One OpenAI-compatible client for cloud gateways, OpenAI itself and Ollama.

A single implementation covers every deployment shape because all three
speak ``/v1/chat/completions`` and ``/v1/embeddings``; they differ only in
base_url, model name and credential. Structured output degrades in three
tiers (strict json_schema -> JSON mode -> prompt-only) and every parsed
payload is still re-validated by our own Pydantic models (#22): the LLM
only generates candidates, trust is decided deterministically.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from truthlayer.domain.errors import ProviderError

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def is_local_endpoint(base_url: str | None) -> bool:
    """True for loopback endpoints (Ollama-style), where no key is needed."""
    if not base_url:
        return False
    head = base_url.split("://", 1)[-1].split("/", 1)[0]
    host = head.split(":", 1)[0].lower()
    return host in _LOCAL_HOSTS


def _load_openai() -> Any:
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise ProviderError(
            "the openai package is required; install with "
            'pip install "truthlayer[openai]"'
        ) from exc
    return OpenAI


def _strip_json_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
    return text


class OpenAICompatibleLLM:
    """Chat client returning validated Pydantic objects."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 180.0,
    ) -> None:
        OpenAI = _load_openai()
        self.model = model
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "not-required",
            timeout=timeout,
            max_retries=1,
        )

    @staticmethod
    def _schema_name(output_schema: type[BaseModel]) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "_", output_schema.__name__)

    def _call_once(
        self,
        *,
        messages: list[dict[str, str]],
        output_schema: type[StructuredOutputT],
        response_format: dict[str, Any] | None,
    ) -> StructuredOutputT:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        return output_schema.model_validate_json(_strip_json_fences(content))

    def generate_structured(
        self,
        *,
        input_text: str,
        output_schema: type[StructuredOutputT],
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> StructuredOutputT:
        if model is not None:
            self.model = model

        schema = output_schema.model_json_schema()
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": input_text})

        schema_hint = (
            "\n\n只输出符合以下 JSON Schema 的 JSON，不要输出任何解释：\n"
            + json.dumps(schema, ensure_ascii=False)
        )
        name = self._schema_name(output_schema)
        attempts: list[
            tuple[str, dict[str, Any] | None, list[dict[str, str]]]
        ] = [
            (
                "json_schema",
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": name,
                        "schema": schema,
                        "strict": True,
                    },
                },
                messages,
            ),
            (
                "json_mode",
                {"type": "json_object"},
                messages + [{"role": "user", "content": schema_hint}],
            ),
            (
                "prompt_only",
                None,
                messages + [{"role": "user", "content": schema_hint}],
            ),
        ]

        failures: list[str] = []
        for tier_name, response_format, tier_messages in attempts:
            try:
                return self._call_once(
                    messages=tier_messages,
                    output_schema=output_schema,
                    response_format=response_format,
                )
            except ValidationError as exc:
                failures.append(
                    f"{tier_name}: schema validation failed: {exc}"
                )
            except Exception as exc:  # noqa: BLE001 - narrow by class name
                kind = type(exc).__name__
                text = str(exc)
                # Auth problems never improve by changing tiers — fail fast.
                if kind in {"AuthenticationError", "PermissionDeniedError"}:
                    raise ProviderError(
                        f"LLM authentication failed ({kind}): {text}"
                    ) from exc
                # Unreachable endpoint also cannot be fixed by a fallback tier.
                if kind in {"APIConnectionError", "APITimeoutError"}:
                    raise ProviderError(
                        f"cannot reach LLM endpoint ({kind}): {text}"
                    ) from exc
                # Unsupported response_format / parameter → try the next tier.
                failures.append(f"{tier_name}: {kind}: {text}")

        raise ProviderError(
            "LLM structured output failed on every fallback tier:\n"
            + "\n".join(f"- {item}" for item in failures)
        )


class OpenAICompatibleEmbedder:
    """Batch embedder; discovers and enforces the vector dimensionality."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        dimensions: int | None = None,
        batch_size: int = 64,
        timeout: float = 120.0,
    ) -> None:
        OpenAI = _load_openai()
        self.model = model
        self.batch_size = batch_size
        self.dimensions: int | None = dimensions
        # Ollama rejects the OpenAI-specific dimensions parameter.
        self._supports_dimensions_param = not is_local_endpoint(base_url)
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "not-required",
            timeout=timeout,
            max_retries=1,
        )

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        kwargs: dict[str, Any] = {"model": self.model, "input": batch}
        if self.dimensions and self._supports_dimensions_param:
            kwargs["dimensions"] = self.dimensions
        try:
            response = self._client.embeddings.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"embedding request failed ({type(exc).__name__}): {exc}"
            ) from exc
        return [item.embedding for item in response.data]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(
                self._embed_batch(list(texts[start : start + self.batch_size]))
            )

        if len(vectors) != len(texts):
            raise ProviderError(
                f"embedding count mismatch: sent {len(texts)} got {len(vectors)}"
            )
        observed = {len(v) for v in vectors}
        if len(observed) != 1:
            raise ProviderError(
                "embedding vectors have inconsistent dimensions: "
                f"{sorted(observed)}"
            )
        actual = observed.pop()
        if self.dimensions is None:
            self.dimensions = actual
        elif actual != self.dimensions:
            raise ProviderError(
                f"embedding dimension mismatch: configured {self.dimensions}, "
                f"model returned {actual}"
            )
        return vectors
