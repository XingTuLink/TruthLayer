"""Detector contract (#55).

Detectors are pure: read a KnowledgeState + DetectionContext, return
DriftCandidates. They never open sessions, mutate rows, touch the CLI or
render HTML.
"""

from __future__ import annotations

from typing import Protocol

from truthlayer.detection.candidates import DriftCandidate
from truthlayer.detection.context import DetectionContext
from truthlayer.detection.state import KnowledgeState


class DriftDetector(Protocol):
    name: str

    def detect(
        self,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> list[DriftCandidate]: ...
