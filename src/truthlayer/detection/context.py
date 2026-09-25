"""DetectionContext — rule inputs for one scan (#19, #20).

``as_of`` is injectable so detectors are deterministic under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from truthlayer.config import TruthLayerConfig
from truthlayer.domain.enums import Severity


@dataclass(frozen=True)
class DetectionContext:
    as_of: date
    stale_after_days: int
    pricing_stale_days: int
    #: override keys look like "pricing_change" / "policy_change".
    severity_overrides: dict[str, Severity]
    #: Predicates explicitly allowed to carry multiple values at once (#19).
    multi_valued_predicates: frozenset[str]

    def severity_for_change(
        self, source_type: str, default: Severity
    ) -> Severity:
        return self.severity_overrides.get(f"{source_type}_change", default)

    def is_multi_valued(self, predicate: str) -> bool:
        return predicate.strip().casefold() in self.multi_valued_predicates

    @classmethod
    def from_config(
        cls, config: TruthLayerConfig, as_of: date | None = None
    ) -> "DetectionContext":
        rules = config.rules
        return cls(
            as_of=as_of or date.today(),
            stale_after_days=rules.stale_after_days,
            pricing_stale_days=rules.pricing_stale_days
            or rules.stale_after_days,
            severity_overrides=dict(config.severity),
            multi_valued_predicates=frozenset(
                p.strip().casefold() for p in rules.multi_valued_predicates
            ),
        )
