"""Version-chain resolution — pure logic, no database (#8).

Priority is strictly:

1. explicit previous_version_id
2. explicit ``supersedes`` metadata
3. .truthlayer.yaml manual mapping (version_mapping)
4. version_label is display-only

Deriving a chain by sorting version labels (``v10`` vs ``v2``) is
forbidden; this module therefore never creates an edge from labels alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from truthlayer.config import VersionMappingConfig


@dataclass(frozen=True)
class VersionLink:
    relative_path: str
    group: str
    version_label: str | None
    supersedes_path: str | None  # resolved POSIX path, or None


def _build_indexes(present: set[str]) -> tuple[dict[str, str], set[str]]:
    """Return (basename → path) index and ambiguous basenames."""
    by_basename: dict[str, str] = {}
    ambiguous: set[str] = set()
    for relative in present:
        basename = Path(relative).name
        if basename in by_basename and by_basename[basename] != relative:
            ambiguous.add(basename)
        else:
            by_basename[basename] = relative
    return by_basename, ambiguous


def _locate(
    spec: str,
    present: set[str],
    by_basename: dict[str, str],
    ambiguous: set[str],
) -> str | None:
    """Locate a mapping file spec among ingested relative paths."""
    spec_posix = Path(spec).as_posix()
    if spec_posix in present:
        return spec_posix
    basename = Path(spec_posix).name
    if basename in ambiguous:
        return None
    return by_basename.get(basename)


def resolve_version_links(
    mappings: list[VersionMappingConfig],
    present: set[str],
) -> tuple[list[VersionLink], list[str]]:
    """Plan document_group / label / previous_version links.

    ``present`` is the set of POSIX relative paths successfully seen in
    this workspace. Missing references produce warnings, never guessed
    edges.
    """
    by_basename, ambiguous = _build_indexes(present)
    links: list[VersionLink] = []
    warnings: list[str] = []

    for mapping in mappings:
        for file_spec in mapping.files:
            located = _locate(
                file_spec.path, present, by_basename, ambiguous
            )
            if located is None:
                warnings.append(
                    f"version_mapping '{mapping.group}' references "
                    f"unavailable file: {file_spec.path}"
                )
                continue

            supersedes_path: str | None = None
            if file_spec.supersedes:
                supersedes_path = _locate(
                    file_spec.supersedes, present, by_basename, ambiguous
                )
                if supersedes_path is None:
                    warnings.append(
                        f"version_mapping '{mapping.group}': "
                        f"{file_spec.path} supersedes "
                        f"{file_spec.supersedes}, which is unavailable"
                    )

            links.append(
                VersionLink(
                    relative_path=located,
                    group=mapping.group,
                    version_label=file_spec.label,
                    supersedes_path=supersedes_path,
                )
            )

    return links, warnings
