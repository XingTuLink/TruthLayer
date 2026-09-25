"""File discovery and ignore tests (#25)."""

from __future__ import annotations

from pathlib import Path

from truthlayer.config import SourceConfig
from truthlayer.ingestion.discovery import discover_files, is_ignored


def _source(path: str, *, authority: float = 0.9, type_: str = "policy"):
    return SourceConfig(path=Path(path), authority=authority, type=type_)


def test_ignores_glob_patterns() -> None:
    assert is_ignored("docs/internal-test/a.txt", ["**/internal-test/**"])
    assert is_ignored("internal-test/a.txt", ["**/internal-test/**"])
    assert is_ignored("docs/policies/a.txt", ["docs/policies/**"])
    assert not is_ignored("docs/public/a.txt", ["**/internal-test/**"])


def test_discover_directory_recursively_sorted(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "docs" / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "docs" / "ignore.bin").write_text("x", encoding="utf-8")

    files, warnings = discover_files(tmp_path, [_source("docs")], [])

    assert [f.relative_path for f in files] == ["docs/a.md", "docs/b.txt"]
    assert any("unsupported" in warning for warning in warnings)


def test_discover_single_file(tmp_path: Path) -> None:
    target = tmp_path / "one.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")

    files, warnings = discover_files(tmp_path, [_source("one.csv", type_="data")], [])

    assert len(files) == 1
    assert files[0].source_type == "data"
    assert warnings == []


def test_missing_source_warns(tmp_path: Path) -> None:
    files, warnings = discover_files(tmp_path, [_source("nope")], [])
    assert files == []
    assert any("does not exist" in warning for warning in warnings)


def test_ignore_pattern_excludes_files(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "keep.txt").write_text("k", encoding="utf-8")
    (tmp_path / "docs" / "skip.txt").write_text("s", encoding="utf-8")

    files, _ = discover_files(
        tmp_path, [_source("docs")], ["**/skip.txt"]
    )

    assert [f.relative_path for f in files] == ["docs/keep.txt"]


def test_overlapping_sources_dedupe_and_first_wins(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "a.txt"
    target.write_text("a", encoding="utf-8")

    files, _ = discover_files(
        tmp_path,
        [_source("docs/a.txt", authority=0.8), _source("docs", authority=0.5)],
        [],
    )

    assert len(files) == 1
    assert files[0].authority == 0.8
