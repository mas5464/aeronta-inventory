"""GlueIcebergFeatureStore reads against a local pyiceberg lake (skips without iceberg extra)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

pytest.importorskip("pyiceberg")

from trax_io_feature_store.client import (  # noqa: E402
    FeatureStoreLookupError,
    MissingTenantContextError,
    TenantContext,
)
from trax_io_feature_store.iceberg_store import GlueIcebergFeatureStore  # noqa: E402

ACME = TenantContext(tenant_id="acme")
D1 = date(2026, 4, 1)
D2 = date(2026, 4, 2)


def _store(catalog) -> GlueIcebergFeatureStore:
    return GlueIcebergFeatureStore(
        catalog=catalog,
        namespace="trax_io",
        table_prefix="",
    )


def _stock_row(pn, loc, serviceable, extract_date, tenant="acme"):
    return {
        "pn": pn, "location": loc, "on_hand": serviceable + 2, "serviceable": serviceable,
        "unserviceable_in_repair": 1, "allocated_reserved": 1, "rental": 0, "loan": 0,
        "tenant_id": tenant, "extract_date": extract_date,
    }


def _lead_time_row(
    *,
    pn: str = "PN-LT",
    vendor: str = "DEFAULT",
    condition: str = "NEW",
    extract_date: date = D1,
):
    return {
        "pn": pn,
        "vendor": vendor,
        "condition": condition,
        "promised_lead_days": 21.0,
        "realized_mean_days": 21.0,
        "realized_p50_days": 21.0,
        "realized_p90_days": 21.0,
        "realized_p99_days": 21.0,
        "promised_vs_actual_delta_mean": None,
        "n_observations": 0,
        "tenant_id": "acme",
        "extract_date": extract_date,
    }


def _run_status_row(
    extract_date: date,
    statuses: dict[str, str],
    *,
    tenant: str = "acme",
    run_id: str | None = None,
):
    return {
        "run_id": run_id or f"RUN-{extract_date.isoformat()}",
        "run_status": "succeeded",
        "artifact_status_json": json.dumps(statuses, sort_keys=True),
        "tenant_id": tenant,
        "extract_date": extract_date,
    }


def _feature_batch_row(
    extract_date: date,
    feature_group: str,
    *,
    run_id: str | None = None,
    row_count: int = 1,
):
    return {
        "feature_group": feature_group,
        "run_id": run_id or f"RUN-{extract_date.isoformat()}",
        "status": "completed",
        "batch_ingested_at": datetime(2026, 1, 1),
        "row_count": row_count,
        "tenant_id": "acme",
        "extract_date": extract_date,
    }


def test_default_reader_identifier_matches_cdk_tenant_tables(catalog) -> None:
    store = GlueIcebergFeatureStore(catalog=catalog)

    assert store._identifier(  # noqa: SLF001 - deployment contract
        "requisition_snapshot",
        TenantContext(tenant_id="air-canada"),
    ) == "trax_io_lake_air_canada.raw_requisition_snapshot"


def test_stock_position_roundtrip(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1)])
    sp = _store(catalog).get_stock_position(tenant=ACME, pn="PN-A", location="LOC-1")
    assert (sp.pn, sp.location, sp.serviceable, sp.on_hand) == ("PN-A", "LOC-1", 8, 10)
    assert sp.tenant_id == "acme" and sp.extract_date == D1


def test_latest_extract_date_wins(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1)])
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 99, D2)])  # newer snapshot
    sp = _store(catalog).get_stock_position(tenant=ACME, pn="PN-A", location="LOC-1")
    assert sp.serviceable == 99 and sp.extract_date == D2  # latest wins


def test_pinned_reader_stays_on_one_run_while_newer_run_commits(
    catalog,
    seed,
) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1)])
    seed(
        "extract_run_status",
        [
            _run_status_row(
                D1,
                {"stock_amount": "succeeded"},
                run_id="RUN-A",
            )
        ],
    )
    seed(
        "feature_batch_status",
        [
            _feature_batch_row(
                D1,
                "stock_position",
                run_id="RUN-A",
            )
        ],
    )
    unpinned = _store(catalog)
    pinned = unpinned.pin_latest_run(tenant=ACME)

    seed("stock_position", [_stock_row("PN-A", "LOC-1", 99, D2)])
    seed(
        "extract_run_status",
        [
            _run_status_row(
                D2,
                {"stock_amount": "succeeded"},
                run_id="RUN-B",
            )
        ],
    )
    seed(
        "feature_batch_status",
        [
            _feature_batch_row(
                D2,
                "stock_position",
                run_id="RUN-B",
            )
        ],
    )

    assert pinned.get_stock_position(
        tenant=ACME,
        pn="PN-A",
        location="LOC-1",
    ).serviceable == 8
    assert unpinned.get_stock_position(
        tenant=ACME,
        pn="PN-A",
        location="LOC-1",
    ).serviceable == 99


def test_vendor_economics_decimal_and_nulls(catalog, seed) -> None:
    seed("vendor_economics", [{
        "pn": "PN-A", "vendor": "DEFAULT", "unit_cost": Decimal("4200.5000"),
        "market_value_unit_cost": None, "average_cost": None, "kit_cost": None,
        "repair_cost_24mo_avg": Decimal("70.0000"), "minimum_order_qty": 3, "currency": "USD",
        "tenant_id": "acme", "extract_date": D1,
    }])
    ve = _store(catalog).get_vendor_economics(tenant=ACME, pn="PN-A", vendor="DEFAULT")
    assert ve.unit_cost == Decimal("4200.5000")
    assert ve.market_value_unit_cost is None and ve.kit_cost is None
    assert ve.repair_cost_24mo_avg == Decimal("70.0000")
    assert ve.minimum_order_qty == 3 and ve.currency == "USD"


def test_migrated_legacy_lead_time_null_provenance_uses_canonical_defaults(
    catalog,
    seed,
) -> None:
    # Additive Iceberg evolution materializes historical values as null, not
    # absent JSON keys. The reader must still load them as explicitly legacy.
    seed("lead_time_distribution", [_lead_time_row()])

    distribution = _store(catalog).get_lead_time_distribution(
        tenant=ACME,
        pn="PN-LT",
        vendor="DEFAULT",
        condition="NEW",
    )

    assert distribution.evidence_status == "legacy_unknown"
    assert distribution.source == "legacy_unknown"
    assert distribution.grouping_level == "legacy_unknown"
    assert distribution.confidence == "unknown"
    assert distribution.data_cutoff is None
    assert distribution.model_version == "legacy-v0"
    assert distribution.proxy_definition is None
    assert distribution.classification_source == "legacy_unknown"


def test_partial_null_lead_time_provenance_fails_closed(catalog, seed) -> None:
    seed(
        "lead_time_distribution",
        [{**_lead_time_row(), "data_cutoff": D1}],
    )

    with pytest.raises(ValidationError):
        _store(catalog).get_lead_time_distribution(
            tenant=ACME,
            pn="PN-LT",
            vendor="DEFAULT",
            condition="NEW",
        )


@pytest.mark.parametrize(
    "statuses",
    [
        {
            "pn_vendor_price": "succeeded",
            "order_plan_closed_orders": "failed",
        },
        {
            "pn_vendor_price": "failed",
            "order_plan_closed_orders": "succeeded",
        },
    ],
)
def test_lead_time_run_is_readable_when_either_source_succeeded(
    catalog,
    seed,
    statuses,
) -> None:
    seed("lead_time_distribution", [_lead_time_row()])
    seed(
        "extract_run_status",
        [_run_status_row(D1, statuses)],
    )
    seed(
        "feature_batch_status",
        [_feature_batch_row(D1, "lead_time_distribution")],
    )

    distribution = _store(catalog).get_lead_time_distribution(
        tenant=ACME,
        pn="PN-LT",
        vendor="DEFAULT",
        condition="NEW",
    )

    assert distribution.pn == "PN-LT"


def test_lead_time_run_fails_closed_when_neither_source_succeeded(
    catalog,
    seed,
) -> None:
    seed("lead_time_distribution", [_lead_time_row()])
    seed(
        "extract_run_status",
        [
            _run_status_row(
                D1,
                {
                    "pn_vendor_price": "failed",
                    "order_plan_closed_orders": "failed",
                },
            )
        ],
    )

    with pytest.raises(FeatureStoreLookupError, match="source unavailable"):
        _store(catalog).get_lead_time_distribution(
            tenant=ACME,
            pn="PN-LT",
            vendor="DEFAULT",
            condition="NEW",
        )


def test_demand_history_aggregates_and_uses_latest_date(catalog, seed) -> None:
    # Two observations on the latest date + one stale-date row that must be excluded.
    seed("demand_history", [
        {"pn": "PN-A", "location": "LOC-1", "interchange_group_id": None, "bucket": "month",
         "period_start": date(2026, 2, 1), "removals": 5, "issues": 0,
         "removal_events": 2, "issue_events": 0,
         "observation_start": date(2023, 3, 1), "observation_end": date(2026, 3, 31),
         "event_count_source": "observed", "source": "nightly-extract",
         "tenant_id": "acme", "extract_date": D2},
        {"pn": "PN-A", "location": "LOC-1", "interchange_group_id": None, "bucket": "month",
         "period_start": date(2026, 3, 1), "removals": 3, "issues": 2,
         "removal_events": 1, "issue_events": 1,
         "observation_start": date(2023, 3, 1), "observation_end": date(2026, 3, 31),
         "event_count_source": "observed", "source": "nightly-extract",
         "tenant_id": "acme", "extract_date": D2},
        {"pn": "PN-A", "location": "LOC-1", "interchange_group_id": None, "bucket": "day",
         "period_start": date(2026, 1, 1), "removals": 99, "issues": 0, "source": "nightly-extract",
         "tenant_id": "acme", "extract_date": D1},  # stale extract_date -> excluded
    ])
    dh = _store(catalog).get_demand_history(tenant=ACME, pn="PN-A", location="LOC-1")
    assert dh.extract_date == D2
    assert [(o.period_start, o.removals, o.issues) for o in dh.observations] == [
        (date(2026, 2, 1), 5, 0),  # sorted by period_start
        (date(2026, 3, 1), 3, 2),
    ]
    assert dh.observation_start == date(2023, 3, 1)
    assert dh.observation_end == date(2026, 3, 31)
    assert sum((o.removal_events or 0) + (o.issue_events or 0) for o in dh.observations) == 4
    assert dh.event_count_source == "observed"
    assert dh.source == "nightly_extract"  # model default (Glue's hyphen value is dropped)


def test_zero_demand_marker_preserves_configured_window(catalog, seed) -> None:
    seed(
        "demand_history",
        [
            {
                "pn": "PN-ZERO",
                "location": "LOC-1",
                "interchange_group_id": None,
                "bucket": "month",
                "period_start": date(2023, 4, 16),
                "removals": 0,
                "issues": 0,
                "removal_events": 0,
                "issue_events": 0,
                "observation_start": date(2023, 4, 16),
                "observation_end": date(2026, 4, 16),
                "event_count_source": "observed",
                "source": "nightly-extract",
                "tenant_id": "acme",
                "extract_date": D2,
            }
        ],
    )

    history = _store(catalog).get_demand_history(
        tenant=ACME,
        pn="PN-ZERO",
        location="LOC-1",
    )

    assert history.observation_start == date(2023, 4, 16)
    assert history.observation_end == date(2026, 4, 16)
    assert len(history.observations) == 1
    assert history.observations[0].removals == history.observations[0].issues == 0


def test_pre_migration_demand_row_reads_with_nullable_defaults(catalog, seed) -> None:
    seed(
        "demand_history",
        [
            {
                "pn": "PN-LEGACY",
                "location": "LOC-1",
                "interchange_group_id": None,
                "bucket": "month",
                "period_start": date(2025, 1, 1),
                "removals": 2,
                "issues": 0,
                "source": "nightly-extract",
                "tenant_id": "acme",
                "extract_date": D1,
            }
        ],
    )

    history = _store(catalog).get_demand_history(
        tenant=ACME,
        pn="PN-LEGACY",
        location="LOC-1",
    )

    assert history.observation_start is None
    assert history.observation_end is None
    assert history.event_count_source == "unavailable"
    assert history.observations[0].removal_events is None
    assert history.observations[0].issue_events is None


def test_open_orders_nested_struct(catalog, seed) -> None:
    seed("open_orders_snapshot", [{
        "pn": "PN-A", "location": "LOC-1", "snapshot_at": datetime(2026, 4, 1, 0, 0),
        "orders": [
            {"order_id": "O1", "order_type": "PO", "vendor": None, "qty_open": 7,
             "expected_rcv_date": date(2026, 4, 10)},
            {"order_id": "O2", "order_type": "RO", "vendor": None, "qty_open": 4,
             "expected_rcv_date": None, "order_line_id": "8",
             "opened_at": datetime(2026, 3, 20, 11, 30),
             "status": "IN_PROGRESS", "serial_number": None,
             "shop": "SHOP-1", "location": "LOC-1"},
        ],
        "total_open_qty": 11, "tenant_id": "acme", "extract_date": D1,
    }])
    oo = _store(catalog).get_open_orders_snapshot(tenant=ACME, pn="PN-A", location="LOC-1")
    assert oo.total_open_qty == 11 and len(oo.orders) == 2
    o1 = next(o for o in oo.orders if o.order_id == "O1")
    assert (o1.order_type, o1.qty_open, o1.expected_rcv_date) == ("PO", 7, date(2026, 4, 10))
    assert o1.order_line_id is None
    assert o1.opened_at is None
    assert o1.status == "OPEN"
    assert o1.location == "LOC-1"
    o2 = next(o for o in oo.orders if o.order_id == "O2")
    assert o2.expected_rcv_date is None
    assert o2.order_line_id == "8"
    assert o2.opened_at == datetime(2026, 3, 20, 11, 30)
    assert o2.status == "IN_PROGRESS"
    assert o2.shop == "SHOP-1"
    assert o2.location == "LOC-1"


def test_legacy_open_order_struct_defaults_missing_repair_fields(
    catalog,
    monkeypatch,
) -> None:
    store = _store(catalog)
    monkeypatch.setattr(
        store,
        "_one",
        lambda *_args, **_kwargs: {
            "pn": "PN-LEGACY",
            "location": "LOC-1",
            "snapshot_at": datetime(2026, 4, 1),
            "orders": [
                {
                    "order_id": "RO-LEGACY",
                    "order_type": "RO",
                    "vendor": "SHOP-OLD",
                    "qty_open": 2,
                    "expected_rcv_date": None,
                }
            ],
            "total_open_qty": 2,
            "extract_date": D1,
        },
    )

    snapshot = store.get_open_orders_snapshot(
        tenant=ACME,
        pn="PN-LEGACY",
        location="LOC-1",
    )

    order = snapshot.orders[0]
    assert order.order_line_id is None
    assert order.opened_at is None
    assert order.status == "OPEN"
    assert order.serial_number is None
    assert order.shop is None
    assert order.location == "LOC-1"


def test_unmaterialized_requisition_table_is_typed_unavailable(catalog) -> None:
    with pytest.raises(FeatureStoreLookupError):
        _store(catalog).get_requisition_snapshot(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
        )


def test_requisition_nested_struct_and_known_empty_snapshot(catalog, seed) -> None:
    seed(
        "requisition_snapshot",
        [
            {
                "pn": "PN-A",
                "location": "LOC-1",
                "snapshot_at": datetime(2026, 4, 1),
                "lines": [
                    {
                        "requisition_id": "REQ-1",
                        "qty_needed": 3,
                        "need_by": date(2026, 5, 1),
                        "alt_source_location": "LOC-2",
                    },
                    {
                        "requisition_id": "REQ-UNDATED",
                        "qty_needed": 2,
                        "need_by": None,
                        "alt_source_location": None,
                    },
                ],
                "total_qty_needed": 5,
                "tenant_id": "acme",
                "extract_date": D1,
            },
            {
                "pn": "PN-ZERO",
                "location": "LOC-2",
                "snapshot_at": datetime(2026, 4, 1),
                "lines": [],
                "total_qty_needed": 0,
                "tenant_id": "acme",
                "extract_date": D1,
            },
        ],
    )

    store = _store(catalog)
    observed = store.get_requisition_snapshot(
        tenant=ACME,
        pn="PN-A",
        location="LOC-1",
    )
    empty = store.get_requisition_snapshot(
        tenant=ACME,
        pn="PN-ZERO",
        location="LOC-2",
    )

    assert observed.total_qty_needed == 5
    assert observed.lines[0].need_by == date(2026, 5, 1)
    assert observed.lines[1].need_by is None
    assert empty.lines == []
    assert empty.total_qty_needed == 0


@pytest.mark.parametrize(
    ("feature_group", "source_domain", "getter"),
    [
        (
            "open_orders_snapshot",
            "order_plan",
            lambda store: store.get_open_orders_snapshot(
                tenant=ACME,
                pn="PN-A",
                location="LOC-1",
            ),
        ),
        (
            "requisition_snapshot",
            "order_plan_data_requisition",
            lambda store: store.get_requisition_snapshot(
                tenant=ACME,
                pn="PN-A",
                location="LOC-1",
            ),
        ),
    ],
)
def test_latest_failed_optional_feed_does_not_serve_prior_snapshot(
    catalog,
    seed,
    feature_group,
    source_domain,
    getter,
) -> None:
    if feature_group == "open_orders_snapshot":
        seed(
            feature_group,
            [
                {
                    "pn": "PN-A",
                    "location": "LOC-1",
                    "snapshot_at": datetime(2026, 4, 1),
                    "orders": [],
                    "total_open_qty": 0,
                    "tenant_id": "acme",
                    "extract_date": D1,
                }
            ],
        )
    else:
        seed(
            feature_group,
            [
                {
                    "pn": "PN-A",
                    "location": "LOC-1",
                    "snapshot_at": datetime(2026, 4, 1),
                    "lines": [],
                    "total_qty_needed": 0,
                    "tenant_id": "acme",
                    "extract_date": D1,
                }
            ],
        )
    seed(
        "extract_run_status",
        [_run_status_row(D2, {source_domain: "failed"})],
    )

    with pytest.raises(FeatureStoreLookupError, match="source unavailable"):
        getter(_store(catalog))


def test_latest_succeeded_run_without_current_row_does_not_fall_back(
    catalog,
    seed,
) -> None:
    seed(
        "open_orders_snapshot",
        [
            {
                "pn": "PN-A",
                "location": "LOC-1",
                "snapshot_at": datetime(2026, 4, 1),
                "orders": [],
                "total_open_qty": 0,
                "tenant_id": "acme",
                "extract_date": D1,
            }
        ],
    )
    seed(
        "extract_run_status",
        [_run_status_row(D2, {"order_plan": "succeeded"})],
    )

    with pytest.raises(FeatureStoreLookupError, match="no completed open_orders_snapshot batch"):
        _store(catalog).get_open_orders_snapshot(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
        )


def test_same_date_newest_run_without_completed_batch_cannot_serve_old_rows(
    catalog,
    seed,
) -> None:
    seed(
        "open_orders_snapshot",
        [
            {
                "pn": "PN-A",
                "location": "LOC-1",
                "snapshot_at": datetime(2026, 4, 1),
                "orders": [],
                "total_open_qty": 0,
                "tenant_id": "acme",
                "extract_date": D1,
            }
        ],
    )
    # RUN-2 saw a succeeded source but its feature job never committed a
    # feature_batch_status row. RUN-1's same-date data must remain invisible.
    seed(
        "extract_run_status",
        [
            _run_status_row(
                D1,
                {"order_plan": "succeeded"},
                run_id="RUN-2",
            )
        ],
    )
    seed(
        "feature_batch_status",
        [
            _feature_batch_row(
                D1,
                "open_orders_snapshot",
                run_id="RUN-1",
            )
        ],
    )

    with pytest.raises(
        FeatureStoreLookupError,
        match="no completed open_orders_snapshot batch.*RUN-2",
    ):
        _store(catalog).get_open_orders_snapshot(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
        )


def test_interchangeable_graph_nested(catalog, seed) -> None:
    seed("interchangeable_graph", [{
        "pn": "PN-2", "group_id": "PN-1+PN-2+PN-3", "members": ["PN-1", "PN-2", "PN-3"],
        "edges": [
            {"from_pn": "PN-1", "to_pn": "PN-2", "one_way": False},
            {"from_pn": "PN-2", "to_pn": "PN-3", "one_way": True},
        ],
        "tenant_id": "acme", "extract_date": D1,
    }])
    ig = _store(catalog).get_interchangeable_graph(tenant=ACME, pn="PN-2")
    assert ig.group_id == "PN-1+PN-2+PN-3" and ig.members == ["PN-1", "PN-2", "PN-3"]
    assert [(e.from_pn, e.to_pn, e.one_way) for e in ig.edges] == [
        ("PN-1", "PN-2", False), ("PN-2", "PN-3", True),
    ]


def test_location_graph_flat_to_nested_node(catalog, seed) -> None:
    seed("location_graph", [{
        "location": "YOW", "related_main_warehouse": "YYZ", "role": "outstation",
        "children": [], "tenant_id": "acme", "extract_date": D1,
    }])
    lg = _store(catalog).get_location_graph(tenant=ACME, location="YOW")
    assert lg.location == "YOW" and lg.children == []
    assert lg.node.related_main_warehouse == "YYZ" and lg.node.role == "outstation"


def test_missing_row_raises_lookup(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1)])
    with pytest.raises(FeatureStoreLookupError):
        _store(catalog).get_stock_position(tenant=ACME, pn="NOPE", location="LOC-1")


def test_missing_table_raises_lookup(catalog) -> None:
    # Table genuinely absent (not yet provisioned) -> NoSuchTableError -> lookup error, not a crash.
    with pytest.raises(FeatureStoreLookupError):
        _store(catalog).get_causal_utilization(tenant=ACME, ac_type="A320", destination="YYZ")


def test_empty_table_raises_lookup(catalog, seed) -> None:
    # The live production shape for causal/wash: the CDK creates the table but no Glue job
    # populates it -> the table exists but is empty -> lookup error (empty-rows path).
    seed("wash_rate_history", [])  # create an empty table
    with pytest.raises(FeatureStoreLookupError):
        _store(catalog).get_wash_rate_history(tenant=ACME, pn="PN-A", location="LOC-1")


def test_wash_rate_history_aggregates_points(catalog, seed) -> None:
    # Exploded per period_month -> reader must rebuild the sorted `points` list (not drop them).
    seed("wash_rate_history", [
        {"pn": "PN-A", "location": "LOC-1", "period_month": date(2026, 3, 1), "wash_rate": 0.2,
         "tenant_id": "acme", "extract_date": D1},
        {"pn": "PN-A", "location": "LOC-1", "period_month": date(2026, 2, 1), "wash_rate": 0.1,
         "tenant_id": "acme", "extract_date": D1},
    ])
    wr = _store(catalog).get_wash_rate_history(tenant=ACME, pn="PN-A", location="LOC-1")
    assert [(p.period_month, p.wash_rate) for p in wr.points] == [
        (date(2026, 2, 1), 0.1), (date(2026, 3, 1), 0.2),  # sorted by period_month
    ]


def test_reappended_partition_is_last_write_wins(catalog, seed) -> None:
    # Iceberg appends don't dedupe: a re-run leaves two row-sets for the same (key, extract_date).
    # The latest `ingested_at` must win deterministically (matching the in-memory last-write-wins).
    old = datetime(2026, 4, 1, 1, 0)
    new = datetime(2026, 4, 1, 9, 0)
    seed("stock_position", [{**_stock_row("PN-A", "LOC-1", 8, D1), "ingested_at": old}])
    seed("stock_position", [{**_stock_row("PN-A", "LOC-1", 99, D1), "ingested_at": new}])
    sp = _store(catalog).get_stock_position(tenant=ACME, pn="PN-A", location="LOC-1")
    assert sp.serviceable == 99  # freshest ingestion wins, deterministically


def test_missing_tenant_raises(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1)])
    with pytest.raises(MissingTenantContextError):
        _store(catalog).get_stock_position(tenant=None, pn="PN-A", location="LOC-1")  # type: ignore[arg-type]


def test_cross_tenant_isolation(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1, tenant="acme")])
    other = TenantContext(tenant_id="other")
    with pytest.raises(FeatureStoreLookupError):
        _store(catalog).get_stock_position(tenant=other, pn="PN-A", location="LOC-1")


def test_iter_inference_keys_returns_distinct_tenant_scoped(catalog, seed) -> None:
    seed("stock_position", [
        _stock_row("PN-A", "LOC-1", 8, D1),
        _stock_row("PN-A", "LOC-1", 9, D2),       # same key, newer date -> still one key
        _stock_row("PN-B", "LOC-2", 5, D1),
        _stock_row("PN-Z", "LOC-9", 5, D1, tenant="other"),  # other tenant -> excluded
    ])
    keys = _store(catalog).iter_inference_keys(tenant=ACME)
    # PN-B existed only in the older tenant snapshot and must not persist.
    assert keys == [("PN-A", "LOC-1")]


def test_iter_inference_keys_is_empty_when_latest_stock_feed_failed(
    catalog,
    seed,
) -> None:
    seed("stock_position", [_stock_row("PN-OLD", "LOC-1", 8, D1)])
    seed(
        "extract_run_status",
        [_run_status_row(D2, {"stock_amount": "failed"})],
    )

    assert _store(catalog).iter_inference_keys(tenant=ACME) == []


def test_iter_inference_keys_empty_when_no_table(catalog) -> None:
    assert _store(catalog).iter_inference_keys(tenant=ACME) == []
