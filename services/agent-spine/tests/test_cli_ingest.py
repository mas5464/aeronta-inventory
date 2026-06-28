import json
from pathlib import Path

from trax_io_event_publisher import make_event
from typer.testing import CliRunner

from trax_io_spine.cli import app

runner = CliRunner()
_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def _removal_for(pn, loc):
    base = make_event("removal_recorded", tenant_id="acme")
    return base.model_copy(
        update={"payload": base.payload.model_copy(update={"pn": pn, "location": loc})}
    )


def test_ingest_prints_a_report(tmp_path):
    events = tmp_path / "events.jsonl"
    ev = _removal_for("FILTER-EXP-042", "YYZ")
    events.write_text(
        ev.model_dump_json() + "\n"
        + make_event("flight_completed", tenant_id="acme").model_dump_json() + "\n"
    )
    result = runner.invoke(
        app,
        ["ingest", "--extract-dir", str(_SAMPLE), "--tenant", "acme",
         "--events", str(events), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["received"] == 2
    assert report["processed"] == 1
    assert report["no_op"] == 1
    assert "outcomes" not in report
    assert report["recompute_totals"]["recommendations"] >= 2


def test_ingest_skips_blank_lines(tmp_path):
    events = tmp_path / "events.jsonl"
    ev = _removal_for("FILTER-EXP-042", "YYZ")
    events.write_text("\n" + ev.model_dump_json() + "\n\n")
    result = runner.invoke(
        app,
        ["ingest", "--extract-dir", str(_SAMPLE), "--tenant", "acme",
         "--events", str(events), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["received"] == 1
