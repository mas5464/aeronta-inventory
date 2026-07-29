from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from trax_io_reco.position.net_position import open_receipts_in_horizon
from trax_io_reco.position.repair_pipeline import build_repair_pipeline

AS_OF = date(2026, 7, 28)


def _order(
    *,
    order_id: str = "RO-1",
    line_id: str | None = "1",
    order_type: str = "RO",
    quantity: int = 1,
    opened_at: str | None = "2026-07-01T00:00:00Z",
    status: str = "OPEN",
    serial_number: str | None = None,
    expected_rcv_date: date | None = None,
    location: str | None = "MIA",
) -> SimpleNamespace:
    return SimpleNamespace(
        order_id=order_id,
        order_line_id=line_id,
        order_type=order_type,
        vendor="SHOP-1",
        shop="SHOP-1",
        qty_open=quantity,
        expected_rcv_date=expected_rcv_date,
        opened_at=opened_at,
        status=status,
        serial_number=serial_number,
        location=location,
    )


def _snapshot(*orders: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(orders=list(orders))


def test_identified_open_repair_is_eligible_and_aggregate_residual_is_excluded() -> None:
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=_snapshot(_order(quantity=4)),
        aggregate_wip_quantity=6,
        as_of=AS_OF,
    )

    assert pipeline.status == "partial"
    assert pipeline.identified_open_quantity == 4
    assert pipeline.eligible_quantity == 4
    assert pipeline.excluded_identifiable_quantity == 0
    assert pipeline.aggregate_residual_quantity == 2
    assert pipeline.source_overflow_quantity == 0
    assert pipeline.time_phased_credit_quantity == 0
    assert pipeline.warning_codes == ("repair_residual_unidentified",)
    assert len(pipeline.included) == 1
    included = pipeline.included[0]
    assert included.work_item.repair_order_id == "RO-1"
    assert included.work_item.repair_line_id == "1"
    assert included.work_item.location_code == "MIA"
    assert included.work_item.shop_identity == "SHOP-1"
    assert included.eligible_quantity == 4
    assert included.age_days == 27
    assert pipeline.exclusions[0].reason == "unidentified_aggregate_residual"
    assert pipeline.exclusions[0].quantity == 2


def test_ambiguous_work_consumes_wip_cap_before_valid_work() -> None:
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=_snapshot(
            _order(order_id="RO-AMBIG", line_id="1", quantity=3, opened_at=None),
            _order(order_id="RO-VALID", line_id="1", quantity=4),
        ),
        aggregate_wip_quantity=5,
        as_of=AS_OF,
    )

    assert pipeline.identified_open_quantity == 7
    assert pipeline.eligible_quantity == 2
    assert pipeline.excluded_identifiable_quantity == 5
    assert pipeline.aggregate_residual_quantity == 0
    assert pipeline.source_overflow_quantity == 2
    assert pipeline.included[0].work_item.repair_order_id == "RO-VALID"
    assert pipeline.included[0].eligible_quantity == 2
    assert {item.reason for item in pipeline.exclusions} == {
        "missing_opened_at",
        "aggregate_wip_cap",
    }
    assert pipeline.warning_codes == (
        "repair_age_missing",
        "repair_wip_mismatch",
        "repair_work_excluded",
    )


def test_duplicate_line_and_serial_identity_receive_no_repair_credit() -> None:
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=_snapshot(
            _order(order_id="RO-DUP", line_id="1", quantity=1, serial_number="SER-1"),
            _order(order_id="RO-DUP", line_id="1", quantity=1, serial_number="SER-1"),
            _order(order_id="RO-OTHER", line_id="1", quantity=1, serial_number="SER-1"),
        ),
        aggregate_wip_quantity=2,
        as_of=AS_OF,
    )

    # The duplicate order-line is collapsed to one identifiable physical line.
    assert pipeline.identified_open_quantity == 2
    assert pipeline.eligible_quantity == 0
    assert pipeline.excluded_identifiable_quantity == 2
    assert pipeline.source_overflow_quantity == 0
    assert pipeline.included == ()
    assert {item.reason for item in pipeline.exclusions} == {
        "duplicate_order_line",
        "duplicate_serial",
    }
    assert pipeline.warning_codes == (
        "repair_source_duplicates",
        "repair_work_excluded",
    )


def test_warning_categories_preserve_secondary_defects_on_one_line() -> None:
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=_snapshot(
            _order(
                order_id="RO-DUP",
                line_id="1",
                opened_at=None,
            ),
            _order(
                order_id="RO-DUP",
                line_id="1",
                opened_at=None,
            ),
        ),
        aggregate_wip_quantity=1,
        as_of=AS_OF,
    )

    # One stable primary exclusion keeps quantities reconcilable, while warning
    # categories still disclose every detected data-quality defect.
    assert pipeline.exclusions[0].reason == "missing_opened_at"
    assert pipeline.warning_codes == (
        "repair_age_missing",
        "repair_source_duplicates",
        "repair_work_excluded",
    )


def test_missing_identity_terminal_and_future_work_are_conservatively_excluded() -> None:
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=_snapshot(
            _order(order_id="?", line_id="1", quantity=1),
            _order(order_id="RO-NOLINE", line_id=None, quantity=1),
            _order(order_id="RO-CLOSED", line_id="1", quantity=1, status="CLOSED"),
            _order(
                order_id="RO-FUTURE",
                line_id="1",
                quantity=1,
                opened_at="2026-07-29T00:00:00Z",
            ),
            _order(
                order_id="RO-SERIAL-QTY",
                line_id="1",
                quantity=2,
                serial_number="SER-2",
            ),
        ),
        aggregate_wip_quantity=6,
        as_of=AS_OF,
    )

    assert pipeline.eligible_quantity == 0
    assert pipeline.identified_open_quantity == 4
    assert pipeline.unidentified_source_quantity == 2
    assert pipeline.aggregate_residual_quantity == 0
    assert {item.reason for item in pipeline.exclusions} == {
        "missing_order_identity",
        "missing_line_identity",
        "terminal_status",
        "future_opened_at",
        "serial_quantity_mismatch",
    }
    assert pipeline.warning_codes == (
        "repair_age_missing",
        "repair_identity_excluded",
        "repair_source_duplicates",
        "repair_work_excluded",
    )


def test_missing_identity_quantity_and_aggregate_residual_conserve_physical_wip() -> None:
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=_snapshot(
            _order(order_id="?", line_id="1", quantity=2),
            _order(order_id="RO-VALID", line_id="1", quantity=1),
        ),
        aggregate_wip_quantity=5,
        as_of=AS_OF,
    )

    assert pipeline.identified_open_quantity == 1
    assert pipeline.unidentified_source_quantity == 2
    assert pipeline.eligible_quantity == 1
    assert pipeline.aggregate_residual_quantity == 2
    assert pipeline.source_overflow_quantity == 0
    assert sum(item.quantity for item in pipeline.exclusions) == 4
    assert (
        pipeline.eligible_quantity
        + sum(item.quantity for item in pipeline.exclusions)
        == pipeline.aggregate_wip_quantity
    )
    assert {item.reason for item in pipeline.exclusions} == {
        "missing_order_identity",
        "unidentified_aggregate_residual",
    }


def test_terminal_only_pipeline_is_partial_instead_of_raising() -> None:
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=_snapshot(
            _order(order_id="RO-CLOSED", line_id="1", status="CLOSED"),
        ),
        aggregate_wip_quantity=1,
        as_of=AS_OF,
    )

    assert pipeline.status == "partial"
    assert pipeline.eligible_quantity == 0
    assert pipeline.exclusions[0].reason == "terminal_status"
    assert pipeline.warning_codes == ("repair_work_excluded",)


def test_missing_repair_location_stays_missing_and_receives_no_credit() -> None:
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=_snapshot(
            _order(order_id="RO-NO-LOC", line_id="1", location=None),
        ),
        aggregate_wip_quantity=1,
        as_of=AS_OF,
    )

    assert pipeline.identified_open_quantity == 1
    assert pipeline.eligible_quantity == 0
    assert pipeline.aggregate_residual_quantity == 0
    assert pipeline.exclusions[0].reason == "missing_location"
    assert pipeline.exclusions[0].quantity == 1


def test_missing_open_order_snapshot_is_unavailable_and_never_credits_aggregate_wip() -> None:
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=None,
        aggregate_wip_quantity=3,
        as_of=AS_OF,
    )

    assert pipeline.status == "unavailable"
    assert pipeline.eligible_quantity == 0
    assert pipeline.aggregate_residual_quantity == 3
    assert pipeline.time_phased_credit_quantity == 0
    assert pipeline.warning_codes == (
        "repair_pipeline_unavailable",
        "repair_residual_unidentified",
    )


def test_procurement_receipts_and_repair_work_are_disjoint() -> None:
    orders = _snapshot(
        _order(
            order_id="PO-1",
            line_id="1",
            order_type="PO",
            quantity=3,
            expected_rcv_date=date(2026, 8, 1),
        ),
        _order(
            order_id="RO-1",
            line_id="1",
            quantity=2,
            expected_rcv_date=date(2026, 8, 1),
        ),
    )

    receipts = open_receipts_in_horizon(
        orders,
        as_of=AS_OF,
        horizon_days=30,
    )
    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=orders,
        aggregate_wip_quantity=2,
        as_of=AS_OF,
    )

    assert receipts.open_receipts_due == 3
    assert pipeline.identified_open_quantity == 2
    assert pipeline.eligible_quantity == 2
    assert receipts.open_receipts_due + pipeline.eligible_quantity == 5


def test_all_small_identified_unidentified_and_aggregate_quantities_conserve() -> None:
    for aggregate_wip in range(6):
        for unidentified_quantity in range(5):
            for identified_quantity in range(5):
                orders = []
                if unidentified_quantity:
                    orders.append(
                        _order(
                            order_id="?",
                            line_id="1",
                            quantity=unidentified_quantity,
                        )
                    )
                if identified_quantity:
                    orders.append(
                        _order(
                            order_id="RO-VALID",
                            line_id="1",
                            quantity=identified_quantity,
                        )
                    )

                pipeline = build_repair_pipeline(
                    tenant_id="acme",
                    part_number="PN-1",
                    location_code="MIA",
                    open_orders=_snapshot(*orders),
                    aggregate_wip_quantity=aggregate_wip,
                    as_of=AS_OF,
                )

                observed_source = unidentified_quantity + identified_quantity
                assert pipeline.identified_open_quantity == identified_quantity
                assert (
                    pipeline.unidentified_source_quantity
                    == unidentified_quantity
                )
                assert pipeline.aggregate_residual_quantity == max(
                    0,
                    aggregate_wip - observed_source,
                )
                assert pipeline.source_overflow_quantity == max(
                    0,
                    observed_source - aggregate_wip,
                )
                assert (
                    pipeline.eligible_quantity
                    + sum(exclusion.quantity for exclusion in pipeline.exclusions)
                    == max(aggregate_wip, observed_source)
                )
