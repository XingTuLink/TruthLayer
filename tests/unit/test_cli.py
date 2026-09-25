"""CLI tests — thin adapter behavior, no database required (#31, #50, #67).

The ingestion service is mocked; config-error paths run for real because
they fail before any database access.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from truthlayer.cli import app as cli_module
from truthlayer.cli.app import app
from truthlayer.ingestion.service import IngestionResult

runner = CliRunner()


def _patch_ingestion(monkeypatch, result: IngestionResult) -> None:
    monkeypatch.setattr(cli_module, "_run_ingestion", lambda path: result)


def test_check_valid_workspace(config_file: Path, monkeypatch) -> None:
    result = IngestionResult(
        workspace_name="acme-corp",
        discovered=2,
        parsed=2,
        chunks=5,
    )
    _patch_ingestion(monkeypatch, result)

    cli_result = runner.invoke(app, ["check", str(config_file.parent)])

    assert cli_result.exit_code == 0, cli_result.output
    assert "acme-corp" in cli_result.output
    assert "2 parsed" in cli_result.output
    assert "Chunks    : 5" in cli_result.output


def test_check_failed_documents_exit_2(
    config_file: Path, monkeypatch
) -> None:
    result = IngestionResult(
        workspace_name="acme-corp", parsed=1, failed=1, chunks=3
    )
    result.errors.append(("broken.pdf", "parser failure"))
    _patch_ingestion(monkeypatch, result)

    cli_result = runner.invoke(app, ["check", str(config_file.parent)])

    assert cli_result.exit_code == 2
    assert "broken.pdf" in cli_result.output


def test_check_service_failure_exit_2(config_file: Path, monkeypatch) -> None:
    _patch_ingestion(monkeypatch, None)

    cli_result = runner.invoke(app, ["check", str(config_file.parent)])

    assert cli_result.exit_code == 2


def test_check_missing_config_returns_exit_2(tmp_path: Path) -> None:
    # Real code path: config validation fails before any database access.
    result = runner.invoke(app, ["check", str(tmp_path)])

    assert result.exit_code == 2
    assert "error:" in result.output


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "truthlayer" in result.output


def test_scan_without_extraction_config_exit_2(tmp_path: Path) -> None:
    # Fails deterministically at config validation, before any DB access.
    (tmp_path / "docs").mkdir()
    (tmp_path / ".truthlayer.yaml").write_text(
        "workspace:\n  name: acme-corp\n"
        "sources:\n  - path: docs\n    type: policy\n    authority: 0.9\n",
        encoding="utf-8",
    )

    cli_result = runner.invoke(app, ["scan", str(tmp_path)])

    assert cli_result.exit_code == 2
    assert "extraction" in cli_result.output


def test_scan_missing_config_returns_exit_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scan", str(tmp_path)])

    assert result.exit_code == 2
    assert "error:" in result.output
