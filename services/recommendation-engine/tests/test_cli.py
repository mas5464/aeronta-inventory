from __future__ import annotations

import json

from click.testing import CliRunner

from trax_io_reco.cli import main


def test_cli_run_emits_json_batch(tmp_path) -> None:
    data = {
        "tenant_id": "acme",
        "parts": [
            {
                "pn": "P-100", "location": "YYZ", "monthly_units": [20] * 12,
                "serviceable": 2, "lead_mean_days": 60, "current_policy": [5, 5, 2, 40],
                "tier": 3, "unit_cost": "400",
            }
        ],
    }
    data_file = tmp_path / "seed.json"
    data_file.write_text(json.dumps(data))

    result = CliRunner().invoke(
        main, ["run", "--data-file", str(data_file), "--now", "2026-04-17T09:00:00"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tenant_id"] == "acme"
    assert "recommendations" in payload
    assert any(r["type"] == "purchase" for r in payload["recommendations"])


def test_cli_type_filter(tmp_path) -> None:
    data = {
        "tenant_id": "acme",
        "parts": [
            {"pn": "P-1", "location": "L", "monthly_units": [30] * 12, "serviceable": 20,
             "current_policy": [1, 1, 0, 2], "tier": 4, "unit_cost": "200"}
        ],
    }
    data_file = tmp_path / "seed.json"
    data_file.write_text(json.dumps(data))
    result = CliRunner().invoke(
        main, ["run", "--data-file", str(data_file), "--type", "adjust_min_max",
               "--now", "2026-04-17T09:00:00"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert all(r["type"] == "adjust_min_max" for r in payload["recommendations"])
