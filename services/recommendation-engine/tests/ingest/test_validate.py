"""Validation rules over parsed canonical rows."""
import pytest

from trax_io_reco.ingest.validate import IngestError, validate


def _clean() -> dict[str, list[dict]]:
    return {
        "parts": [{"part_number": "P1", "criticality": "AOG", "unit_cost": "100"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5"}],
        "demand_history": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "period": "2026-01-01",
                "quantity": "3",
                "observation_start": "2025-11-01",
                "observation_end": "2026-03-31",
            }
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


def test_inf_and_nan_rejected_as_non_numeric():
    # float("inf")/float("nan") parse as floats but the loader coerces them to 0 downstream;
    # they must be flagged, not silently turned into a real zero.
    for bad in ("inf", "-inf", "nan"):
        p = _clean()
        p["stock"][0]["on_hand"] = bad
        errs = validate(p)
        assert any(
            e.file == "stock" and e.column == "on_hand" and "not numeric" in e.message
            for e in errs
        ), f"{bad!r} should be rejected"


def test_bad_date():
    p = _clean()
    p["demand_history"][0]["period"] = "not-a-date"
    errs = validate(p)
    assert any(e.file == "demand_history" and e.column == "period" for e in errs)


def test_open_repair_started_at_must_be_an_iso_timestamp_when_supplied() -> None:
    parsed = _clean()
    parsed["open_orders"] = [
        {
            "part_number": "P1",
            "location_code": "MIA",
            "quantity": "1",
            "expected_date": "2026-08-15",
            "order_type": "RO",
            "order_id": "RO-42",
            "order_line_id": "7",
            "opened_at": "not-a-timestamp",
            "status": "OPEN",
        }
    ]

    errors = validate(parsed)

    assert any(
        error.file == "open_orders"
        and error.row == 0
        and error.column == "opened_at"
        and "ISO timestamp" in error.message
        for error in errors
    )


def test_demand_observation_bounds_must_be_paired_and_consistent() -> None:
    parsed = _clean()
    parsed["demand_history"] = [
        {
            "part_number": "P1",
            "location_code": "MIA",
            "period": "2026-01-01",
            "quantity": "3",
            "observation_start": "2023-04-16",
            "observation_end": "2026-04-16",
        },
        {
            "part_number": "P1",
            "location_code": "MIA",
            "period": "2026-02-01",
            "quantity": "4",
            "observation_start": "2024-04-16",
            "observation_end": "2026-04-16",
        },
    ]

    errors = validate(parsed)

    assert any("same observation window" in error.message for error in errors)


def test_demand_observation_bounds_reject_unpaired_or_reversed_values() -> None:
    parsed = _clean()
    parsed["demand_history"][0].update(
        {
            "observation_start": "2026-04-16",
            "observation_end": "",
        }
    )
    assert any("both" in error.message for error in validate(parsed))

    parsed["demand_history"][0]["observation_end"] = "2025-04-16"
    assert any("closed interval" in error.message for error in validate(parsed))


def test_demand_history_requires_a_closed_observation_window() -> None:
    parsed = _clean()
    parsed["demand_history"][0].pop("observation_start")
    parsed["demand_history"][0].pop("observation_end")

    errors = validate(parsed)

    assert any(
        error.file == "demand_history"
        and error.row is None
        and "closed observation window" in error.message
        for error in errors
    )


def test_demand_window_file_can_define_the_interval_once_for_all_rows() -> None:
    parsed = _clean()
    parsed["demand_history"][0].pop("observation_start")
    parsed["demand_history"][0].pop("observation_end")
    parsed["demand_window"] = [
        {
            "observation_start": "2025-11-01",
            "observation_end": "2026-03-31",
        }
    ]

    assert validate(parsed) == []


def test_empty_demand_history_requires_one_valid_window_row() -> None:
    parsed = _clean()
    parsed["demand_history"] = []
    assert any("closed observation window" in error.message for error in validate(parsed))

    parsed["demand_window"] = [
        {
            "observation_start": "2025-11-01",
            "observation_end": "2026-03-31",
        }
    ]
    assert validate(parsed) == []

    parsed["demand_window"].append(
        {
            "observation_start": "2025-12-01",
            "observation_end": "2026-03-31",
        }
    )
    assert any("exactly one row" in error.message for error in validate(parsed))


def test_demand_window_must_match_row_bounds_and_requires_demand_file() -> None:
    parsed = _clean()
    parsed["demand_window"] = [
        {
            "observation_start": "2025-12-01",
            "observation_end": "2026-03-31",
        }
    ]
    assert any("must match" in error.message for error in validate(parsed))

    parsed.pop("demand_history")
    assert any(
        error.file == "demand_window" and "demand_history" in error.message
        for error in validate(parsed)
    )


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


def test_blank_required_stock_column():
    p = _clean()
    p["stock"][0]["on_hand"] = ""
    errs = validate(p)
    assert any(e.file == "stock" and e.row == 0 and e.column == "on_hand"
               and "required" in e.message.lower() for e in errs)


def test_blank_required_demand_columns():
    p = _clean()
    p["demand_history"][0]["period"] = ""
    p["demand_history"][0]["quantity"] = "  "
    errs = validate(p)
    cols = {(e.file, e.column) for e in errs if "required" in e.message.lower()}
    assert ("demand_history", "period") in cols
    assert ("demand_history", "quantity") in cols


def test_blank_optional_column_tolerated():
    p = _clean()
    p["stock"][0]["allocated"] = ""   # optional
    errs = validate(p)
    assert not any(e.column == "allocated" for e in errs)


def test_required_numeric_empty_reports_required_not_numeric():
    p = _clean()
    p["stock"][0]["on_hand"] = ""
    errs = [e for e in validate(p) if e.column == "on_hand"]
    assert len(errs) == 1 and "required" in errs[0].message.lower()


@pytest.mark.parametrize(
    ("file_name", "column", "value", "message_fragment"),
    [
        ("stock", "on_hand", "-1", "non-negative"),
        ("stock", "allocated", "1.5", "integer"),
        ("stock", "in_repair", "-1", "non-negative"),
        ("stock", "current_rop", "2.5", "integer"),
        ("stock", "current_eoq", "-1", "non-negative"),
        ("stock", "current_safety_stock", "0.25", "integer"),
        ("stock", "current_max", "-1", "non-negative"),
        ("demand_history", "quantity", "1.5", "integer"),
        ("demand_history", "quantity", "-1", "non-negative"),
        ("open_orders", "quantity", "2.5", "integer"),
        ("open_orders", "quantity", "-1", "non-negative"),
        ("parts", "unit_cost", "0", "positive"),
        ("parts", "shelf_life_days", "3.5", "integer"),
        ("vendors", "unit_price", "-1", "positive"),
        ("vendors", "lead_time_days", "0", "positive"),
        ("vendors", "min_order_qty", "1.5", "integer"),
        ("vendors", "min_order_qty", "0", "positive"),
    ],
)
def test_canonical_quantities_and_commercial_values_reject_lossy_inputs(
    file_name: str,
    column: str,
    value: str,
    message_fragment: str,
) -> None:
    parsed = _clean()
    parsed["stock"][0].update(
        {
            "allocated": "0",
            "in_repair": "0",
            "current_rop": "1",
            "current_eoq": "1",
            "current_safety_stock": "1",
            "current_max": "2",
        }
    )
    parsed["parts"][0]["shelf_life_days"] = "10"
    parsed["open_orders"] = [
        {
            "part_number": "P1",
            "location_code": "MIA",
            "quantity": "2",
            "expected_date": "2026-02-01",
        }
    ]
    parsed["vendors"] = [
        {
            "part_number": "P1",
            "vendor_code": "V1",
            "unit_price": "10",
            "lead_time_days": "14",
            "min_order_qty": "1",
        }
    ]
    parsed[file_name][0][column] = value

    errors = [
        error
        for error in validate(parsed)
        if error.file == file_name
        and error.row == 0
        and error.column == column
    ]

    assert errors
    assert message_fragment in errors[0].message


def test_stock_part_requires_unit_cost_when_no_vendor_price_can_serve_it() -> None:
    parsed = _clean()
    parsed["parts"][0].pop("unit_cost")

    errors = validate(parsed)

    assert any(
        error.file == "parts"
        and error.row == 0
        and error.column == "unit_cost"
        and "no vendor price" in error.message
        for error in errors
    )


def test_vendor_price_can_supply_cost_when_part_unit_cost_is_absent() -> None:
    parsed = _clean()
    parsed["parts"][0].pop("unit_cost")
    parsed["vendors"] = [
        {
            "part_number": "P1",
            "vendor_code": "V1",
            "unit_price": "10",
            "lead_time_days": "14",
        }
    ]

    assert validate(parsed) == []


def test_requisitions_accept_dated_and_undated_lines_with_known_locations() -> None:
    parsed = _clean()
    parsed["locations"] = [
        {"location_code": "MIA"},
        {"location_code": "ATL"},
    ]
    parsed["requisitions"] = [
        {
            "requisition_id": "REQ-1",
            "part_number": "P1",
            "location_code": "MIA",
            "quantity": "4",
            "need_by": "2026-05-01",
            "alt_source_location": "ATL",
        },
        {
            "requisition_id": "REQ-2",
            "part_number": "P1",
            "location_code": "MIA",
            "quantity": "2",
            "need_by": "",
        },
    ]

    assert validate(parsed) == []


@pytest.mark.parametrize(
    ("mutation", "column", "message_fragment"),
    [
        ({"quantity": "0"}, "quantity", "positive"),
        ({"quantity": "1.5"}, "quantity", "integer"),
        ({"need_by": "not-a-date"}, "need_by", "valid date"),
        ({"alt_source_location": "GHOST"}, "alt_source_location", "not found"),
    ],
)
def test_requisition_values_fail_closed(
    mutation: dict[str, str],
    column: str,
    message_fragment: str,
) -> None:
    parsed = _clean()
    parsed["locations"] = [{"location_code": "MIA"}]
    parsed["requisitions"] = [
        {
            "requisition_id": "REQ-1",
            "part_number": "P1",
            "location_code": "MIA",
            "quantity": "4",
            "need_by": "2026-05-01",
            **mutation,
        }
    ]

    errors = validate(parsed)

    assert any(
        error.file == "requisitions"
        and error.column == column
        and message_fragment in error.message
        for error in errors
    )


def test_requisition_ids_are_unique() -> None:
    parsed = _clean()
    parsed["requisitions"] = [
        {
            "requisition_id": "REQ-1",
            "part_number": "P1",
            "location_code": "MIA",
            "quantity": "4",
        },
        {
            "requisition_id": " REQ-1 ",
            "part_number": "P1",
            "location_code": "MIA",
            "quantity": "2",
        },
    ]

    assert any("duplicate requisition_id" in error.message for error in validate(parsed))
