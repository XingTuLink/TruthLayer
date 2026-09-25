"""LLMProvider contract (#22).

LLMs only do candidate generation; structured output must pass Pydantic
validation and deterministic rules before becoming trusted knowledge (#3).
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


class LLMProvider(Protocol):
    """Implemented by OpenAIProvider / OllamaProvider in later sprints."""

    def generate_structured(
        self,
        *,
        input_text: str,
        output_schema: type[StructuredOutputT],
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> StructuredOutputT:
        """Return a validated structured object or raise ProviderError.

        Implementations own retry, timeout and provider-specific errors.
        """
        ...
