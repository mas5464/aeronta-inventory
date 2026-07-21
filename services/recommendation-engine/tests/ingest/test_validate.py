"""Validation rules over parsed canonical rows."""
from trax_io_reco.ingest.validate import IngestError, validate


def _clean() -> dict[str, list[dict]]:
    return {
        "parts": [{"part_number": "P1", "criticality": "AOG", "unit_cost": "100"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5"}],
        "demand_history": [
            {"part_number": "P1", "location_code": "MIA", "period": "2026-01-01",
             "quantity": "3"}
        ],
    }


def test_clean_passes():
    assert validate(_clean()) == []


def test_missing_required_file():
    errs = validate({"parts": [{"part_number": "P1"}]})  # no stock
    assert any(e.file == "stock" and "required" in e.message.lower() for e in errs)


def test_missing_required_column():
    p = _clean()
    p["stock"] = [{"part_number": "P1", "location_code": "MIA"}]  # no on_hand
    errs = validate(p)
    assert any(e.file == "stock" and e.column == "on_hand" for e in errs)


def test_non_numeric_quantity():
    p = _clean()
    p["stock"][0]["on_hand"] = "lots"
    errs = validate(p)
    assert any(e.file == "stock" and e.row == 0 and e.column == "on_hand" for e in errs)


def test_bad_date():
    p = _clean()
    p["demand_history"][0]["period"] = "not-a-date"
    errs = validate(p)
    assert any(e.file == "demand_history" and e.column == "period" for e in errs)


def test_referential_unknown_part():
    p = _clean()
    p["stock"].append({"part_number": "GHOST", "location_code": "MIA", "on_hand": "1"})
    errs = validate(p)
    assert any("GHOST" in e.message for e in errs)


def test_unknown_criticality_flagged():
    p = _clean()
    p["parts"][0]["criticality"] = "WHATISTHIS"
    errs = validate(p)
    assert any(e.file == "parts" and e.column == "criticality" for e in errs)


def test_quota_exceeded():
    p = _clean()
    p["stock"] = [
        {"part_number": f"P{i}", "location_code": "MIA", "on_hand": "1"} for i in range(5)
    ]
    p["parts"] = [{"part_number": f"P{i}"} for i in range(5)]
    errs = validate(p, key_quota=3)
    assert any(e.file == "stock" and "exceeds" in e.message for e in errs)
