"""Canonical repair-history intake and native-contract equivalence."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import openpyxl
import pytest
from pydantic import ValidationError
from trax_io_feature_store import TenantContext

from trax_io_reco.contracts.repair import RepairCycleObservation
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.ingest.canonical import CANONICAL_FILES
from trax_io_reco.ingest.mapper import to_extract_dir
from trax_io_reco.ingest.parse import parse_uploads
from trax_io_reco.ingest.repair import repair_history_coverage
from trax_io_reco.ingest.validate import validate


def _base() -> dict[str, list[dict]]:
    return {
        "parts": [
            {
                "part_number": "P1",
                "part_class": "rotable",
                "repairable": "Y",
                "unit_cost": "100",
            }
        ],
        "stock": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "on_hand": "1",
                "in_repair": "1",
            }
        ],
        "locations": [{"location_code": "MIA"}],
    }


def _repair_row(**over: str) -> dict[str, str]:
    row = {
        "repair_order_id": "RO-100",
        "repair_line_id": "1",
        "part_number": "P1",
        "quantity": "1",
        "started_at": "2026-01-01T10:00:00Z",
        "completed_at": "2026-01-21T10:00:00Z",
        "status": "completed",
        "shop_code": "SHOP-1",
        "location_code": "MIA",
        "outcome": "serviceable",
        "serial_number": "SER-1",
    }
    row.update(over)
    return row


def _xlsx(headers: list[str], values: list[str]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    sheet.append(values)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_public_repair_observation_contract_normalizes_utc_and_rejects_contradiction() -> None:
    observation = RepairCycleObservation(
        tenant_id="acme",
        **_repair_row(),
    )

    assert observation.contract_version == "repair-cycle-observation.v1"
    assert observation.started_at == datetime(2026, 1, 1, 10, tzinfo=UTC)
    assert observation.is_observed_return

    with pytest.raises(ValidationError, match="cancelled repair"):
        RepairCycleObservation(
            tenant_id="acme",
            **_repair_row(status="cancelled"),
        )


def test_repair_history_is_an_optional_public_canonical_file() -> None:
    spec = CANONICAL_FILES["repair_history"]

    assert not spec.required
    assert spec.required_columns == (
        "repair_order_id",
        "repair_line_id",
        "part_number",
        "quantity",
        "started_at",
        "completed_at",
        "status",
    )
    assert "serial_number" in spec.optional_columns


def test_csv_and_excel_normalize_to_the_same_repair_rows() -> None:
    columns = [
        *CANONICAL_FILES["repair_history"].required_columns,
        *CANONICAL_FILES["repair_history"].optional_columns,
    ]
    row = _repair_row()
    csv_data = (
        ",".join(columns)
        + "\n"
        + ",".join(row.get(column, "") for column in columns)
        + "\n"
    ).encode()
    xlsx_data = _xlsx(columns, [row.get(column, "") for column in columns])

    csv_rows = parse_uploads({"repair_history": csv_data})["repair_history"]
    xlsx_rows = parse_uploads({"repair_history": xlsx_data})["repair_history"]

    assert csv_rows == xlsx_rows == [{column: row.get(column, "") for column in columns}]


@pytest.mark.parametrize(
    ("mutation", "column", "message"),
    [
        ({"repair_order_id": ""}, "repair_order_id", "required"),
        ({"quantity": "0"}, "quantity", "positive"),
        ({"quantity": "1.5"}, "quantity", "integer"),
        ({"started_at": "not-a-time"}, "started_at", "ISO timestamp"),
        ({"completed_at": "2025-12-31"}, "completed_at", "must not precede"),
        ({"status": "flying"}, "status", "unknown terminal"),
        ({"status": "cancelled"}, "outcome", "cannot carry"),
        ({"status": "scrapped", "outcome": "serviceable"}, "outcome", "contradicts"),
        ({"quantity": "2"}, "quantity", "serial-number"),
        ({"part_number": "GHOST"}, "part_number", "not found"),
        ({"location_code": "GHOST"}, "location_code", "not found"),
    ],
)
def test_repair_history_validation_is_actionable_and_fail_closed(
    mutation: dict[str, str],
    column: str,
    message: str,
) -> None:
    parsed = _base()
    parsed["repair_history"] = [{**_repair_row(), **mutation}]

    errors = validate(parsed)

    assert any(
        error.file == "repair_history"
        and error.row == 0
        and error.column == column
        and message in error.message
        for error in errors
    )


def test_duplicate_terminal_event_is_rejected_by_stable_order_line_identity() -> None:
    parsed = _base()
    parsed["repair_history"] = [
        _repair_row(),
        _repair_row(serial_number="SER-2"),
    ]

    errors = validate(parsed)

    assert any(
        error.file == "repair_history"
        and error.row == 1
        and "duplicate terminal event" in error.message
        for error in errors
    )


def test_coverage_separates_observed_pooled_proxy_and_unavailable() -> None:
    parsed = _base()
    parsed["parts"] = [
        {"part_number": "P1", "part_class": "rotable"},
        {"part_number": "P2", "part_class": "repairable"},
        {"part_number": "P3", "repairable": "Y"},
        {"part_number": "P4", "part_class": "rotable"},
    ]
    parsed["repair_history"] = [
        _repair_row(),
        _repair_row(
            repair_order_id="RO-200",
            part_number="P2",
            status="scrapped",
            outcome="scrapped",
            serial_number="SER-2",
        ),
        _repair_row(
            repair_order_id="RO-400",
            part_number="P4",
            shop_code="",
            serial_number="SER-4",
        ),
    ]
    parsed["vendors"] = [
        {
            "part_number": "P2",
            "vendor_code": "SHOP-2",
            "unit_price": "10",
            "lead_time_days": "45",
            "condition": "REP",
        }
    ]

    coverage = repair_history_coverage(parsed, tenant_id="acme")

    assert coverage.as_dict() == {
        "accepted": 2,
        "excluded": 1,
        "quarantined": 0,
        "parts_covered": 2,
        "shops_covered": 1,
        "observed": 2,
        "pooled": 1,
        "proxy": 1,
        "unavailable": 1,
        "proxy_definition": "order_creation_to_last_receipt",
    }


def test_coverage_counts_malformed_rows_as_quarantined_without_accepting_them() -> None:
    parsed = _base()
    parsed["repair_history"] = [
        _repair_row(),
        _repair_row(
            repair_order_id="RO-200",
            status="scrapped",
            outcome="scrapped",
            serial_number="SER-2",
        ),
        _repair_row(
            repair_order_id="RO-300",
            quantity="0",
            serial_number="SER-3",
        ),
    ]
    errors = validate(parsed)

    coverage = repair_history_coverage(
        parsed,
        tenant_id="acme",
        validation_errors=errors,
    )

    assert any(
        error.file == "repair_history"
        and error.row == 2
        and error.column == "quantity"
        for error in errors
    )
    assert coverage.accepted == 1
    assert coverage.excluded == 1
    assert coverage.quarantined == 1
    assert coverage.accepted + coverage.excluded + coverage.quarantined == 3


def test_canonical_repair_history_maps_only_eligible_returns_and_matches_native(
    tmp_path,
) -> None:
    parsed = _base()
    parsed["repair_history"] = [
        _repair_row(),
        _repair_row(
            repair_order_id="RO-200",
            status="scrapped",
            outcome="scrapped",
            serial_number="SER-2",
        ),
    ]
    assert validate(parsed) == []
    canonical_dir = tmp_path / "canonical"
    to_extract_dir(parsed, canonical_dir, tenant_id="acme")

    mapped_rows = json.loads(
        (canonical_dir / "order_plan_closed_orders.json").read_text()
    )
    assert len(mapped_rows) == 1
    assert mapped_rows[0]["ordertypeid"] == "RO"
    assert mapped_rows[0]["hostvendorlocid"] == "SHOP-1"
    assert mapped_rows[0]["repair_contract_version"] == (
        "repair-cycle-observation.v1"
    )
    manifest = json.loads((canonical_dir / "manifest.json").read_text())
    artifact = next(
        item
        for item in manifest["artifacts"]
        if item["domain"] == "order_plan_closed_orders"
    )
    assert artifact["repair_history"]["accepted"] == 1
    assert artifact["repair_history"]["excluded"] == 1
    assert manifest["extract_date"] == "2026-01-21"
    assert manifest["extract_date_source"] == "latest_repair_completion"

    native_dir = tmp_path / "native"
    to_extract_dir(_base(), native_dir, tenant_id="acme")
    native_rows = [
        {
            "hostorderid": "RO-100",
            "orderid": "1",
            "hostpartid": "P1",
            "hostvendorlocid": "SHOP-1",
            "hostlocid": "MIA",
            "planquantity": "1",
            "receivedquantity": "1",
            "planorderdate": "2026-01-01T10:00:00+00:00",
            "actualrcvdate": "2026-01-21T10:00:00+00:00",
            "ordertypeid": "RO",
            "orderstatus": "CLOSED",
            "repairoutcome": "serviceable",
            "serialnumber": "SER-1",
        }
    ]
    (native_dir / "order_plan_closed_orders.json").write_text(
        json.dumps(native_rows)
    )
    native_manifest = json.loads((native_dir / "manifest.json").read_text())
    native_manifest["extract_date"] = "2026-01-21"
    native_manifest["artifacts"].append(
        {
            "domain": "order_plan_closed_orders",
            "status": "succeeded",
            "row_count": 1,
        }
    )
    (native_dir / "manifest.json").write_text(json.dumps(native_manifest))

    tenant = TenantContext(tenant_id="acme")
    canonical_store, _, _, _ = build_stores_from_extract(canonical_dir)
    native_store, _, _, _ = build_stores_from_extract(native_dir)
    canonical = canonical_store.get_lead_time_distribution(
        tenant=tenant,
        pn="P1",
        vendor="SHOP-1",
        condition="REP",
    )
    native = native_store.get_lead_time_distribution(
        tenant=tenant,
        pn="P1",
        vendor="SHOP-1",
        condition="REP",
    )

    assert canonical.model_dump() == native.model_dump()
