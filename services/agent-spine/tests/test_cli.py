import json

from typer.testing import CliRunner

from trax_io_spine.cli import app

_SAMPLE = "../recommendation-engine/examples/extract_sample"


def test_cli_dry_run_emits_summary() -> None:
    result = CliRunner().invoke(
        app, ["run", "--extract-dir", _SAMPLE, "--tenant", "acme", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert "recommendations" in summary
    assert summary["recommendations"] >= 0
