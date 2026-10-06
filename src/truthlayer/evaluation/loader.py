"""Load golden QA cases from a directory of YAML suite files."""

from __future__ import annotations

from pathlib import Path

import yaml

from truthlayer.evaluation.schema import QACase, QACaseSuite


class EvaluationError(Exception):
    """Raised when golden cases cannot be loaded."""


def load_cases(directory: str | Path) -> list[QACase]:
    directory = Path(directory)
    if not directory.is_dir():
        raise EvaluationError(f"QA cases directory not found: {directory}")

    files = sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix in (".yaml", ".yml")
    )
    if not files:
        raise EvaluationError(f"no .yaml/.yml cases found under {directory}")

    cases: list[QACase] = []
    seen: set[str] = set()
    for path in files:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise EvaluationError(f"invalid YAML in {path}: {exc}") from exc
        suite = QACaseSuite.model_validate(raw)
        for case in suite.cases:
            if case.id in seen:
                raise EvaluationError(f"duplicate case id {case.id!r} in {path}")
            seen.add(case.id)
            cases.append(case)

    return cases
