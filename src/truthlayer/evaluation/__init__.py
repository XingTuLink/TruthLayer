"""Deterministic evaluation harness and Phase 0 Gate metrics (#39, #40, #42).

The golden QA set exercises the *deterministic detection core* directly:
each YAML case is turned into a :class:`~truthlayer.detection.state.KnowledgeState`
and fed to the real detectors, with no database and no LLM. This is exactly
the layer that adjudicates drift (#22 — the LLM only proposes; #23 —
embeddings only recall), so it can be evaluated deterministically and run on
every CI pass. End-to-end extraction quality is covered separately by the
``ollama``-marked tests and real-world HCR measurement.
"""

from truthlayer.evaluation.schema import (
    CaseCategory,
    ExpectedDrift,
    QACase,
    QACaseSuite,
    QADocument,
    QAEntity,
    QAFact,
    QARules,
)

__all__ = [
    "CaseCategory",
    "ExpectedDrift",
    "QACase",
    "QACaseSuite",
    "QADocument",
    "QAEntity",
    "QAFact",
    "QARules",
]
