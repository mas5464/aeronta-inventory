"""Fixtures for the GlueIcebergFeatureStore tests.

Builds a hermetic local Iceberg lake with pyiceberg's SQLite catalog + a temp file warehouse
(pure Python, no Spark/JVM/AWS). Tests skip cleanly when the `iceberg` extra is not installed.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pa = pytest.importorskip("pyarrow")
pytest.importorskip("pyiceberg")

from pyiceberg.catalog.sql import SqlCatalog  # noqa: E402
from pyiceberg.exceptions import TableAlreadyExistsError  # noqa: E402

_NS = "trax_io"
_INGESTED = datetime(2026, 1, 1, 0, 0, 0)

# arrow type shorthands
_S, _I, _D = pa.string(), pa.int32(), pa.float64()
_DEC, _DATE, _TS, _B = pa.decimal128(18, 4), pa.date32(), pa.timestamp("us"), pa.bool_()
_META = [("manifest_sha256", _S), ("ingested_at", _TS)]
_PART = [("tenant_id", _S), ("extract_date", _DATE)]

_ORDER_STRUCT = pa.struct(
    [
        ("order_id", _S),
        ("order_type", _S),
        ("vendor", _S),
        ("qty_open", _I),
        ("expected_rcv_date", _DATE),
    ]
)
_EDGE_STRUCT = pa.struct([("from_pn", _S), ("to_pn", _S), ("one_way", _B)])

# Mirrors infra/feature-store/stacks/iceberg_schemas.py (+ the tenant_id/extract_date partitions).
ARROW_FIELDS: dict[str, list[tuple[str, object]]] = {
    "stock_position": [
        ("pn", _S), ("location", _S), ("on_hand", _I), ("serviceable", _I),
        ("unserviceable_in_repair", _I), ("allocated_reserved", _I), ("rental", _I), ("loan", _I),
    ] + _META + _PART,
    "current_policy": [
        ("pn", _S), ("location", _S), ("rop", _I), ("eoq", _I), ("safety_stock", _I),
        ("max_stock", _I), ("replenishment_lead_days", _D),
    ] + _META + _PART,
    "vendor_economics": [
        ("pn", _S), ("vendor", _S), ("unit_cost", _DEC), ("market_value_unit_cost", _DEC),
        ("average_cost", _DEC), ("kit_cost", _DEC), ("repair_cost_24mo_avg", _DEC),
        ("minimum_order_qty", _I), ("currency", _S),
    ] + _META + _PART,
    "part_attributes": [
        ("pn", _S), ("description", _S), ("ata_chapter", _S), ("part_class", _S),
        ("shelf_life_days", _I), ("hazardous_material", _B), ("tool_control_item", _B),
        ("fleet_effectivity_tail_count", _I),
    ] + _META + _PART,
    "criticality": [
        ("pn", _S), ("raw_essentiality_code", _S), ("canonical_tier", _I), ("mapping_source", _S),
    ] + _META + _PART,
    "lead_time_distribution": [
        ("pn", _S), ("vendor", _S), ("condition", _S), ("promised_lead_days", _D),
        ("realized_mean_days", _D), ("realized_p50_days", _D), ("realized_p90_days", _D),
        ("realized_p99_days", _D), ("promised_vs_actual_delta_mean", _D), ("n_observations", _I),
    ] + _META + _PART,
    "demand_history": [
        ("pn", _S), ("location", _S), ("interchange_group_id", _S), ("bucket", _S),
        ("period_start", _DATE), ("removals", _I), ("issues", _I), ("source", _S),
    ] + _META + _PART,
    "open_orders_snapshot": [
        ("pn", _S), ("location", _S), ("snapshot_at", _TS), ("orders", pa.list_(_ORDER_STRUCT)),
        ("total_open_qty", _I),
    ] + _META + _PART,
    "interchangeable_graph": [
        ("pn", _S), ("group_id", _S), ("members", pa.list_(_S)),
        ("edges", pa.list_(_EDGE_STRUCT)),
    ] + _META + _PART,
    "location_graph": [
        ("location", _S), ("related_main_warehouse", _S), ("role", _S),
        ("children", pa.list_(_S)),
    ] + _META + _PART,
    # Tables the CDK creates but no v1 Glue job populates (causal/wash). Present so tests can
    # cover the empty-table miss path and the exploded wash_rate aggregation.
    "causal_utilization": [
        ("ac_type", _S), ("destination", _S), ("observation_date", _DATE),
        ("flight_hours", _D), ("flight_cycles", _I),
    ] + _META + _PART,
    "wash_rate_history": [
        ("pn", _S), ("location", _S), ("period_month", _DATE), ("wash_rate", _D),
    ] + _META + _PART,
}


def _schema(group: str) -> pa.Schema:
    return pa.schema([pa.field(n, t) for n, t in ARROW_FIELDS[group]])


@pytest.fixture
def catalog(tmp_path):
    wh = tmp_path / "warehouse"
    wh.mkdir()
    cat = SqlCatalog(
        "test", **{"uri": f"sqlite:///{wh}/catalog.db", "warehouse": f"file://{wh}"}
    )
    cat.create_namespace(_NS)
    return cat


@pytest.fixture
def seed(catalog):
    """Append `rows` to `group`'s table, filling metadata + creating the table on first use."""

    def _seed(group: str, rows: list[dict]) -> None:
        schema = _schema(group)
        filled = [{"manifest_sha256": "seed", "ingested_at": _INGESTED, **r} for r in rows]
        tbl = pa.Table.from_pylist(filled, schema=schema)
        identifier = f"{_NS}.{group}"
        try:
            table = catalog.create_table(identifier, schema=tbl.schema)
        except TableAlreadyExistsError:
            table = catalog.load_table(identifier)
        table.append(tbl)

    return _seed
