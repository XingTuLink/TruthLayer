"""Drift detection engine (Sprint 4, #18–#21).

Pipeline: KnowledgeState -> deterministic Detectors -> DriftCandidates
-> fingerprint dedupe -> Drift rows.

Embeddings only recall candidates; every decision is deterministic (#23).
Detectors never touch the CLI or produce HTML (#55, #56).
"""
