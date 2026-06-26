"""Smoke test: import the CLI entrypoint and verify top-level commands run."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from click.testing import CliRunner

import trax_io_extract.cli as cli_mod
from trax_io_extract.cli import main
from trax_io_extract.domains import DOMAINS


def test_cli_help_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "Trax IO" in result.output


def test_cli_version_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0, result.output


def test_cli_list_domains_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list-domains"])
    assert result.exit_code == 0, result.output
    # All 21 domain names should appear in the output.
    for d in DOMAINS:
        assert d.name in result.output


def test_cli_extract_dry_run_emits_21_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "extract",
            "--tenant-id",
            "lighthouse-01",
            "--extract-date",
            "2026-04-16",
            "--transaction",
            "NR",
            "--output-dir",
            str(tmp_path),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "domains=21/21" in result.output
    assert "status=succeeded" in result.output

    # Locate the one run directory emitted.
    run_dirs = list(tmp_path.glob("extract_date=2026-04-16/run_id=*"))
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    manifest_path = run_dir / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == "1.0.0"
    assert len(manifest["artifacts"]) == 21
    assert manifest["run_status"] == "succeeded"

    # Every domain should have a placeholder artifact on disk.
    for d in DOMAINS:
        assert (run_dir / f"{d.name}.json").is_file()


def test_cli_extract_subset(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "extract",
            "--tenant-id",
            "t",
            "--extract-date",
            "2026-04-16",
            "--domain",
            "location_master",
            "--domain",
            "vendor",
            "--output-dir",
            str(tmp_path),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "domains=2/2" in result.output


def test_cli_extract_events_requires_transaction(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "extract",
            "--tenant-id",
            "t",
            "--extract-date",
            "2026-04-16",
            "--domain",
            "events",
            "--output-dir",
            str(tmp_path),
            "--dry-run",
        ],
    )
    assert result.exit_code != 0
    assert "transaction" in result.output.lower()


# ---------------------------------------------------------------------------
# --no-dry-run with a monkeypatched connection factory


class _FakeCursor:
    def __init__(self, rows: list[tuple], columns: list[str]) -> None:
        self._rows = rows
        self.description = [(c, None, None, None, None, None, None) for c in columns]

    def execute(self, sql: str, binds: dict) -> None:
        pass

    def fetchall(self) -> list[tuple]:
        return self._rows

    def close(self) -> None:
        pass


class _FakeConn:
    def cursor(self) -> _FakeCursor:
        return _FakeCursor([("ATL", "Atlanta")], ["location_id", "name"])

    def close(self) -> None:
        pass


def _fake_factory():
    @contextmanager
    def factory() -> Iterator[_FakeConn]:
        yield _FakeConn()

    return factory


def test_cli_extract_no_dry_run_uses_conn_factory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli_mod, "_resolve_conn_factory", _fake_factory)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "extract",
            "--tenant-id",
            "t",
            "--extract-date",
            "2026-04-16",
            "--domain",
            "location_master",
            "--domain",
            "vendor",
            "--output-dir",
            str(tmp_path),
            "--no-dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "domains=2/2" in result.output
    assert "status=succeeded" in result.output

    run_dir = next(iter(tmp_path.glob("extract_date=2026-04-16/run_id=*")))
    parsed = json.loads((run_dir / "location_master.json").read_text("utf-8"))
    assert parsed == [{"location_id": "ATL", "name": "Atlanta"}]


def test_cli_extract_no_dry_run_missing_env_exits_2(tmp_path: Path, monkeypatch) -> None:
    # Clear the required env vars and restore real env-based resolution.
    for var in (
        "TRAX_ORACLE_HOST",
        "TRAX_ORACLE_SERVICE",
        "TRAX_ORACLE_USER",
        "TRAX_ORACLE_PASSWORD",
        "TRAX_ORACLE_PORT",
        "TRAX_ORACLE_WALLET",
    ):
        monkeypatch.delenv(var, raising=False)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "extract",
            "--tenant-id",
            "t",
            "--extract-date",
            "2026-04-16",
            "--domain",
            "location_master",
            "--output-dir",
            str(tmp_path),
            # default is --no-dry-run
        ],
    )
    assert result.exit_code == 2, result.output
    assert "TRAX_ORACLE" in (result.output + (result.stderr if False else ""))
