"""Source file discovery with ignore-glob evaluation (#25).

A source can be a single file or a directory (walked recursively). Files
are returned in deterministic POSIX-relative-path order so that chunk
indexes and content hashes stay reproducible (#5, #35).
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from truthlayer.config import SourceConfig
from truthlayer.ingestion.parsers import SUPPORTED_EXTENSIONS


@dataclass(frozen=True)
class SourceFile:
    relative_path: str  # POSIX-style, relative to the workspace base dir
    absolute_path: Path
    authority: float
    source_type: str


def is_ignored(relative_posix: str, patterns: list[str]) -> bool:
    """Match ignore patterns against a POSIX relative path.

    ``fnmatch`` treats ``*`` as crossing separators, so ``**`` needs no
    special engine; we additionally strip a leading ``**/`` and check
    directory-style patterns against every ancestor.
    """
    parts = relative_posix.split("/")
    ancestors = ["/".join(parts[:i]) for i in range(1, len(parts))]

    for pattern in patterns:
        candidates = {pattern}
        if pattern.startswith("**/"):
            candidates.add(pattern[3:])
        directory_pattern = pattern
        if directory_pattern.startswith("**/"):
            directory_pattern = directory_pattern[3:]
        if directory_pattern.endswith("/**"):
            directory_pattern = directory_pattern[:-3]

        for candidate in candidates:
            if fnmatch(relative_posix, candidate):
                return True
        if ancestors and directory_pattern:
            if any(fnmatch(ancestor, directory_pattern) for ancestor in ancestors):
                return True
    return False


def discover_files(
    base_dir: Path,
    sources: list[SourceConfig],
    ignore_patterns: list[str],
) -> tuple[list[SourceFile], list[str]]:
    """Expand configured sources into supported, non-ignored files.

    Returns the files and warnings (missing sources / unsupported files).
    First matching source wins when paths overlap; results are deduplicated.
    """
    warnings: list[str] = []
    seen: set[str] = set()
    files: list[SourceFile] = []

    def add_file(absolute: Path, source: SourceConfig) -> None:
        relative = absolute.resolve().relative_to(base_dir.resolve()).as_posix()
        if relative in seen:
            return
        if is_ignored(relative, ignore_patterns):
            return
        if absolute.suffix.lower() not in SUPPORTED_EXTENSIONS:
            warnings.append(
                f"skipping unsupported file type: {relative}"
            )
            return
        seen.add(relative)
        files.append(
            SourceFile(
                relative_path=relative,
                absolute_path=absolute,
                authority=source.authority,
                source_type=source.type,
            )
        )

    for source in sources:
        root = (base_dir / source.path).resolve()
        if not root.exists():
            warnings.append(f"source path does not exist: {source.path}")
            continue
        if root.is_file():
            add_file(root, source)
            continue
        for absolute in sorted(
            path for path in root.rglob("*") if path.is_file()
        ):
            add_file(absolute, source)

    files.sort(key=lambda item: item.relative_path)
    return files, warnings
