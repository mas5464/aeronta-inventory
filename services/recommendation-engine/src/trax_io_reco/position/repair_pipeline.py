"""Identity-aware, conservative reconciliation of open repair work.

This module intentionally stops before probabilistic return modeling. It
identifies which open repair order lines *could* be modeled later, prevents
purchase/repair and aggregate-WIP overlap, and records why every other unit
receives zero time-phased repair credit.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time
from typing import Any

from trax_io_reco.contracts.repair import (
    IncludedRepairPosition,
    RepairPipeline,
    RepairPipelineWarningCode,
    RepairWorkExclusion,
    RepairWorkExclusionCode,
    RepairWorkItem,
    parse_repair_timestamp,
)

_MISSING_IDENTITIES = frozenset({"", "?", "none", "null", "unknown"})
_OPEN_STATUSES = frozenset(
    {
        "open",
        "in_progress",
        "in progress",
        "awaiting_parts",
        "awaiting parts",
        "awaiting_vendor",
        "awaiting vendor",
        "on_hold",
        "on hold",
    }
)
_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "closed",
        "cancelled",
        "canceled",
        "scrapped",
        "condemned",
    }
)


def _clean(value: object) -> str | None:
    normalized = str(value or "").strip()
    if normalized.lower() in _MISSING_IDENTITIES:
        return None
    return normalized


def _status(value: object) -> str:
    return str(value or "OPEN").strip().lower()


def _opened_at(value: object) -> datetime | None:
    return parse_repair_timestamp(value)


def _reason_detail(reason: RepairWorkExclusionCode) -> str:
    return {
        "missing_order_identity": "Stable repair-order identity is missing.",
        "missing_line_identity": "Stable repair-order line identity is missing.",
        "missing_opened_at": "Repair lifecycle start is missing; current age is unknown.",
        "future_opened_at": "Repair lifecycle start is later than the planning as-of.",
        "missing_location": "Repair work has no location identity.",
        "location_mismatch": "Repair work belongs to a different part-location key.",
        "terminal_status": "Terminal repair work cannot be future return supply.",
        "ineligible_status": "Repair status is not an approved open-work state.",
        "duplicate_order_line": "Duplicate repair-order line identity is ambiguous.",
        "duplicate_serial": "Serial identity appears on more than one open repair line.",
        "serial_quantity_mismatch": (
            "A serialized repair position must represent exactly one physical unit."
        ),
        "aggregate_wip_cap": "Identified work exceeds reconciled aggregate repair WIP.",
        "unidentified_aggregate_residual": (
            "Aggregate in-repair WIP remains after identified order lines are removed."
        ),
    }[reason]


def _exclusion(
    order: Any,
    *,
    quantity: int,
    reason: RepairWorkExclusionCode,
) -> RepairWorkExclusion:
    return RepairWorkExclusion(
        repair_order_id=_clean(getattr(order, "order_id", None)),
        repair_line_id=_clean(getattr(order, "order_line_id", None)),
        serial_number=_clean(getattr(order, "serial_number", None)),
        quantity=max(0, quantity),
        reason=reason,
        detail=_reason_detail(reason),
    )


def _primary_reason(reasons: list[RepairWorkExclusionCode]) -> RepairWorkExclusionCode:
    """Pick a stable primary reason while warnings retain broader categories."""

    priority: tuple[RepairWorkExclusionCode, ...] = (
        "missing_order_identity",
        "missing_line_identity",
        "missing_opened_at",
        "future_opened_at",
        "missing_location",
        "location_mismatch",
        "terminal_status",
        "ineligible_status",
        "duplicate_order_line",
        "duplicate_serial",
        "serial_quantity_mismatch",
        "aggregate_wip_cap",
        "unidentified_aggregate_residual",
    )
    return next(reason for reason in priority if reason in reasons)


def _warning_codes(
    exclusions: list[RepairWorkExclusion],
    *,
    unavailable: bool,
    residual: int,
    overflow: int,
    observed_reasons: set[RepairWorkExclusionCode] | None = None,
) -> tuple[RepairPipelineWarningCode, ...]:
    warnings: set[RepairPipelineWarningCode] = set()
    reasons = {item.reason for item in exclusions}
    if observed_reasons:
        reasons.update(observed_reasons)
    if unavailable:
        warnings.add("repair_pipeline_unavailable")
    if reasons - {"unidentified_aggregate_residual"}:
        warnings.add("repair_work_excluded")
    if reasons & {"missing_order_identity", "missing_line_identity"}:
        warnings.add("repair_identity_excluded")
    if reasons & {"missing_opened_at", "future_opened_at"}:
        warnings.add("repair_age_missing")
    if reasons & {
        "duplicate_order_line",
        "duplicate_serial",
        "serial_quantity_mismatch",
    }:
        warnings.add("repair_source_duplicates")
    if overflow or "aggregate_wip_cap" in reasons:
        warnings.add("repair_wip_mismatch")
    if residual:
        warnings.add("repair_residual_unidentified")
    return tuple(sorted(warnings))


def build_repair_pipeline(
    *,
    tenant_id: str,
    part_number: str,
    location_code: str,
    open_orders: Any | None,
    aggregate_wip_quantity: int,
    as_of: date,
) -> RepairPipeline:
    """Reconcile open RO lines against aggregate physical in-repair WIP.

    Non-RO orders are deliberately ignored here and remain procurement
    receipts. RO rows never enter generic open-receipt credit. Every incomplete,
    duplicate, terminal, future-dated, or over-cap RO line is excluded
    conservatively and receives zero time-phased repair credit.
    """

    aggregate_wip = max(0, int(aggregate_wip_quantity))
    if open_orders is None:
        residual_exclusions = (
            (
                RepairWorkExclusion(
                    quantity=aggregate_wip,
                    reason="unidentified_aggregate_residual",
                    detail=_reason_detail("unidentified_aggregate_residual"),
                ),
            )
            if aggregate_wip
            else ()
        )
        return RepairPipeline(
            tenant_id=tenant_id,
            part_number=part_number,
            location_code=location_code,
            as_of=as_of,
            status="unavailable",
            aggregate_wip_quantity=aggregate_wip,
            identified_open_quantity=0,
            eligible_quantity=0,
            excluded_identifiable_quantity=0,
            aggregate_residual_quantity=aggregate_wip,
            source_overflow_quantity=0,
            exclusions=residual_exclusions,
            warning_codes=_warning_codes(
                list(residual_exclusions),
                unavailable=True,
                residual=aggregate_wip,
                overflow=0,
            ),
        )

    repair_orders = [
        order
        for order in getattr(open_orders, "orders", ())
        if str(getattr(order, "order_type", "")).strip().upper() == "RO"
        and int(getattr(order, "qty_open", 0) or 0) > 0
    ]
    line_counts = Counter(
        (
            _clean(getattr(order, "order_id", None)),
            _clean(getattr(order, "order_line_id", None)),
        )
        for order in repair_orders
        if _clean(getattr(order, "order_id", None))
        and _clean(getattr(order, "order_line_id", None))
    )
    serial_counts = Counter(
        _clean(getattr(order, "serial_number", None))
        for order in repair_orders
        if _clean(getattr(order, "serial_number", None))
    )

    # Collapse a repeated order-line identity to one conservative physical line.
    by_line: dict[tuple[str, str], list[Any]] = defaultdict(list)
    unidentified: list[Any] = []
    for order in repair_orders:
        order_id = _clean(getattr(order, "order_id", None))
        line_id = _clean(getattr(order, "order_line_id", None))
        if order_id and line_id:
            by_line[(order_id, line_id)].append(order)
        else:
            unidentified.append(order)

    canonical_orders: list[Any] = []
    for identity in sorted(by_line):
        group = by_line[identity]
        canonical_orders.append(
            sorted(
                group,
                key=lambda order: (
                    -int(getattr(order, "qty_open", 0) or 0),
                    _clean(getattr(order, "serial_number", None)) or "",
                ),
            )[0]
        )

    exclusions: list[RepairWorkExclusion] = []
    observed_reasons: set[RepairWorkExclusionCode] = set()
    unidentified_quantity = 0
    for order in unidentified:
        quantity = int(getattr(order, "qty_open", 0) or 0)
        unidentified_quantity += quantity
        reason: RepairWorkExclusionCode = (
            "missing_order_identity"
            if _clean(getattr(order, "order_id", None)) is None
            else "missing_line_identity"
        )
        observed_reasons.add(reason)
        exclusions.append(
            _exclusion(
                order,
                quantity=quantity,
                reason=reason,
            )
        )

    as_of_end = datetime.combine(as_of, time.max, tzinfo=UTC)
    valid: list[tuple[Any, RepairWorkItem, int]] = []
    identified_quantity = 0
    preexcluded_quantity = 0
    for order in canonical_orders:
        qty = int(getattr(order, "qty_open", 0) or 0)
        identified_quantity += qty
        order_id = _clean(getattr(order, "order_id", None))
        line_id = _clean(getattr(order, "order_line_id", None))
        opened = _opened_at(getattr(order, "opened_at", None))
        location = _clean(getattr(order, "location", None))
        status = _status(getattr(order, "status", None))
        serial = _clean(getattr(order, "serial_number", None))
        reasons: list[RepairWorkExclusionCode] = []
        if opened is None:
            reasons.append("missing_opened_at")
        elif opened > as_of_end:
            reasons.append("future_opened_at")
        if not location:
            reasons.append("missing_location")
        elif location != location_code:
            reasons.append("location_mismatch")
        if status in _TERMINAL_STATUSES:
            reasons.append("terminal_status")
        elif status not in _OPEN_STATUSES:
            reasons.append("ineligible_status")
        if line_counts[(order_id, line_id)] > 1:
            reasons.append("duplicate_order_line")
        if serial and serial_counts[serial] > 1:
            reasons.append("duplicate_serial")
        if serial and qty != 1:
            reasons.append("serial_quantity_mismatch")

        if reasons:
            observed_reasons.update(reasons)
            preexcluded_quantity += qty
            exclusions.append(
                _exclusion(
                    order,
                    quantity=qty,
                    reason=_primary_reason(reasons),
                )
            )
            continue

        # The preceding checks prove these fields are populated.
        assert order_id is not None
        assert line_id is not None
        assert opened is not None
        assert location is not None
        item = RepairWorkItem(
            tenant_id=tenant_id,
            repair_order_id=order_id,
            repair_line_id=line_id,
            part_number=part_number,
            quantity=qty,
            location_code=location,
            opened_at=opened,
            status=status,
            shop_code=_clean(getattr(order, "shop", None)),
            vendor_code=_clean(getattr(order, "vendor", None)),
            serial_number=serial,
        )
        age_days = max(0, (as_of - opened.date()).days)
        valid.append((order, item, age_days))

    # Ambiguous/ineligible identified lines consume the aggregate cap first.
    # That deliberately minimizes future-credit eligibility under disagreement.
    remaining_capacity = max(
        0,
        aggregate_wip - preexcluded_quantity - unidentified_quantity,
    )
    included: list[IncludedRepairPosition] = []
    for order, item, age_days in sorted(
        valid,
        key=lambda candidate: (
            candidate[1].repair_order_id,
            candidate[1].repair_line_id,
        ),
    ):
        accepted = min(item.quantity, remaining_capacity)
        if accepted:
            included.append(
                IncludedRepairPosition(
                    work_item=item,
                    eligible_quantity=accepted,
                    age_days=age_days,
                )
            )
            remaining_capacity -= accepted
        overflow = item.quantity - accepted
        if overflow:
            observed_reasons.add("aggregate_wip_cap")
            exclusions.append(
                _exclusion(
                    order,
                    quantity=overflow,
                    reason="aggregate_wip_cap",
                )
            )

    eligible_quantity = sum(position.eligible_quantity for position in included)
    observed_source_quantity = identified_quantity + unidentified_quantity
    residual = max(0, aggregate_wip - observed_source_quantity)
    source_overflow = max(0, observed_source_quantity - aggregate_wip)
    if residual:
        observed_reasons.add("unidentified_aggregate_residual")
        exclusions.append(
            RepairWorkExclusion(
                quantity=residual,
                reason="unidentified_aggregate_residual",
                detail=_reason_detail("unidentified_aggregate_residual"),
            )
        )
    warnings = _warning_codes(
        exclusions,
        unavailable=False,
        residual=residual,
        overflow=source_overflow,
        observed_reasons=observed_reasons,
    )
    return RepairPipeline(
        tenant_id=tenant_id,
        part_number=part_number,
        location_code=location_code,
        as_of=as_of,
        status="partial" if warnings else "available",
        aggregate_wip_quantity=aggregate_wip,
        identified_open_quantity=identified_quantity,
        unidentified_source_quantity=unidentified_quantity,
        eligible_quantity=eligible_quantity,
        excluded_identifiable_quantity=identified_quantity - eligible_quantity,
        aggregate_residual_quantity=residual,
        source_overflow_quantity=source_overflow,
        included=tuple(included),
        exclusions=tuple(exclusions),
        warning_codes=warnings,
    )
