"""Provider abstractions.

The domain must never import a vendor SDK (#22, #23, #53). Concrete
implementations (OpenAI / Ollama / file parsers) arrive in later sprints;
Sprint 1 establishes the contracts only.
"""
