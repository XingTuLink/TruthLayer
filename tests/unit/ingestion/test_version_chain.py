"""Version-chain rule tests (#8)."""

from __future__ import annotations

from truthlayer.config import VersionMappingConfig
from truthlayer.ingestion.version_chain import resolve_version_links


def _mapping(group: str, *files: tuple[str, str | None, str | None]):
    return VersionMappingConfig.model_validate(
        {
            "group": group,
            "files": [
                {"path": path, "label": label, "supersedes": supersedes}
                for path, label, supersedes in files
            ],
        }
    )


def test_explicit_supersedes_builds_edge() -> None:
    mappings = [
        _mapping(
            "pricing",
            ("pricing_2025.xlsx", "2025", None),
            ("pricing_2026.xlsx", "2026", "pricing_2025.xlsx"),
        )
    ]
    present = {"pricing_2025.xlsx", "pricing_2026.xlsx"}

    links, warnings = resolve_version_links(mappings, present)

    assert warnings == []
    by_path = {link.relative_path: link for link in links}
    assert by_path["pricing_2026.xlsx"].supersedes_path == "pricing_2025.xlsx"
    assert by_path["pricing_2025.xlsx"].group == "pricing"
    assert by_path["pricing_2025.xlsx"].version_label == "2025"


def test_labels_never_imply_edges_v10_vs_v2() -> None:
    # The forbidden case (#8): v10 must not chain onto v2 via label sorting.
    mappings = [
        _mapping(
            "policy",
            ("policy_v2.docx", "v2", None),
            ("policy_v10.docx", "v10", None),
        )
    ]
    present = {"policy_v2.docx", "policy_v10.docx"}

    links, warnings = resolve_version_links(mappings, present)

    assert warnings == []
    assert all(link.supersedes_path is None for link in links)


def test_missing_file_warns_and_no_link() -> None:
    mappings = [_mapping("g", ("only_2026.xlsx", "2026", "only_2025.xlsx"))]

    links, warnings = resolve_version_links(mappings, {"only_2026.xlsx"})

    assert len(links) == 1
    assert links[0].supersedes_path is None
    assert any("unavailable" in warning for warning in warnings)


def test_unknown_spec_skipped_with_warning() -> None:
    mappings = [_mapping("g", ("ghost.xlsx", "1", None))]

    links, warnings = resolve_version_links(mappings, set())

    assert links == []
    assert any("unavailable file" in warning for warning in warnings)


def test_basename_lookup_with_directory_prefix() -> None:
    mappings = [
        _mapping(
            "pricing",
            ("./docs/pricing_2025.csv", "2025", None),
            ("./docs/pricing_2026.csv", "2026", "pricing_2025.csv"),
        )
    ]
    present = {"docs/pricing_2025.csv", "docs/pricing_2026.csv"}

    links, _ = resolve_version_links(mappings, present)
    by_path = {link.relative_path: link for link in links}

    assert (
        by_path["docs/pricing_2026.csv"].supersedes_path
        == "docs/pricing_2025.csv"
    )


def test_ambiguous_basename_does_not_guess() -> None:
    mappings = [
        _mapping("g", ("new/a.xlsx", "new", "a.xlsx")),
    ]
    present = {"new/a.xlsx", "old/a.xlsx"}

    links, warnings = resolve_version_links(mappings, present)

    # Ambiguous supersedes target must not be guessed.
    assert links[0].supersedes_path is None
    assert any("unavailable" in warning for warning in warnings)
