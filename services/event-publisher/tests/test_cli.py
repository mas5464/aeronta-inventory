import json

from trax_io_event_publisher.cli import main


def test_emit_to_stdout_prints_valid_event(capsys):
    rc = main(["emit", "--kind", "stock_moved", "--tenant", "acme-air"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "stock_moved"
    assert payload["tenant_id"] == "acme-air"


def test_emit_to_fake_reports_emitted(capsys):
    rc = main(["emit", "--kind", "removal_recorded", "--tenant", "acme-air", "--to", "fake"])
    assert rc == 0
    assert "emitted" in capsys.readouterr().out.lower()
