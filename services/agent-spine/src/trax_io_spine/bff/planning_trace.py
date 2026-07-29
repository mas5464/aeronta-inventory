"""Planner-facing adapter for the recommendation engine's pure demand-basis trace.

This module only assembles public feature/recommendation evidence into the BFF wire
model. Historical exposure and scheduled-horizon arithmetic stay owned by
``trax_io_reco.demand.basis`` so the engine, scenarios, and browser contract cannot
quietly acquire separate formulas.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from trax_io_reco.demand.basis import demand_basis_trace, scheduled_units_in_horizon
from trax_io_reco.position.net_position import open_receipts_in_horizon

from trax_io_spine.bff.models import (
    PlanningConstraintView,
    PlanningMemberTraceView,
    PlanningTraceView,
)


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _iso(value) -> str | None:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed is not None else None


def _constraint_trace(
    *, recommendation, vendor_economics, part_attributes
) -> tuple[tuple[PlanningConstraintView, ...], str | None]:
    applied = (
        getattr(recommendation, "applied_constraints", None)
        if recommendation is not None
        else None
    )
    policy = getattr(recommendation, "policy", None) if recommendation is not None else None
    if not applied and policy is not None:
        # Transitional compatibility with any pre-release policy-level carrier.
        applied = getattr(policy, "applied_constraints", None)
    if applied:
        return (
            tuple(
                PlanningConstraintView(
                    name=str(item.name),
                    value=None if item.value is None else str(item.value),
                    binding=bool(item.binding),
                    source=str(item.source),
                    scope=str(getattr(item, "scope", "policy")),
                )
                for item in applied
            ),
            None,
        )

    # A legacy recommendation still exposes source inputs elsewhere in
    # PartContext, but it does not say which constraints the engine applied or
    # whether they bound. Emitting those inputs with `binding=False` would turn
    # "unknown" into a false operational claim. Keep the trace empty and explicit.
    del vendor_economics, part_attributes
    subject = "this key" if recommendation is None else "this recommendation"
    return (), (
        f"Applied-constraint evidence is unavailable for {subject}; no applied "
        "or binding state was inferred from source inputs."
    )


def _open_receipts(
    *, open_orders, as_of: date | None, horizon_days: int
) -> tuple[int, int, tuple[str, ...]]:
    if open_orders is None:
        return 0, 0, (
            "Open-order evidence is unavailable; open receipt quantities are "
            "unavailable placeholders, not observed zeros.",
        )
    if as_of is None:
        return 0, 0, (
            "No selected recommendation horizon is available; open receipts could "
            "not be assigned to a horizon.",
        )

    receipt_trace = open_receipts_in_horizon(
        open_orders,
        as_of=as_of,
        horizon_days=horizon_days,
    )
    due = receipt_trace.open_receipts_due
    overdue = receipt_trace.overdue_open_receipts_due
    undated = sum(
        1
        for order in getattr(open_orders, "orders", ())
        if getattr(order, "expected_rcv_date", None) is None
    )

    warnings: list[str] = []
    if overdue:
        warnings.append(
            f"{overdue} open receipt units are overdue as of {as_of.isoformat()} "
            "but remain included because their source orders are still open; "
            "receipt is not guaranteed."
        )
    if undated:
        warnings.append(
            f"{undated} open order lines without an expected receipt date were "
            "excluded from the selected horizon."
        )
    return due, overdue, tuple(warnings)


def _availability_status(carrier, field: str) -> str:
    value = getattr(carrier, field, "unavailable")
    return str(getattr(value, "value", value))


def _availability_warning(
    *,
    label: str,
    status: str,
    field: str,
    quantity: float,
    undated_lines: int,
    undated_units: int,
) -> str | None:
    if status == "available":
        return None
    if status == "partial":
        undated = (
            f" {undated_lines} undated lines ({undated_units} units) were excluded "
            "from the dated horizon."
            if undated_lines or undated_units
            else ""
        )
        qualifier = (
            f"{field}=0 is not evidence of no {label}."
            if quantity == 0
            else f"{field} may be understated."
        )
        return f"{label.capitalize()} evidence is partial; {qualifier}{undated}"
    qualifier = (
        f"{field}=0 is an unavailable placeholder, not an observed zero."
        if quantity == 0
        else f"{field} is not backed by complete source availability."
    )
    return f"{label.capitalize()} evidence is unavailable; {qualifier}"


def build_planning_trace(
    *,
    demand_history,
    recommendation=None,
    scheduled_demand=None,
    open_orders=None,
    vendor_economics=None,
    part_attributes=None,
) -> PlanningTraceView:
    """Build one additive, unavailable-safe BFF trace from public contracts.

    New recommendations carry their exact served arithmetic. That carrier is
    copied field-for-field. Source snapshots are consulted only for raw
    observation statistics and for the explicitly labelled legacy reconstruction
    used by recommendations persisted before the carrier existed.
    """
    warnings: list[str] = []

    if demand_history is None:
        basis_values = {
            "observation_start": None,
            "observation_end": None,
            "exposure_days": 0,
            "bucket": None,
            "observed_periods": 0,
            "zero_filled_periods": 0,
            "demand_event_count": None,
            "event_count_source": "unavailable",
            "demanded_units": 0,
            "historical_per_day": 0.0,
        }
        warnings.append(
            "Demand-history evidence is unavailable; historical quantities are "
            "unavailable placeholders, not observed zeros."
        )
    else:
        basis = demand_basis_trace(demand_history)
        basis_values = {
            "observation_start": _iso(basis.observation_start),
            "observation_end": _iso(basis.observation_end),
            "exposure_days": basis.exposure_days,
            "bucket": basis.bucket,
            "observed_periods": basis.observed_periods,
            "zero_filled_periods": basis.zero_filled_periods,
            "demand_event_count": basis.demand_event_count,
            "event_count_source": basis.event_count_source,
            "demanded_units": basis.demanded_units,
            "historical_per_day": basis.historical_per_day,
        }
        if basis.event_count_source == "bucket_fallback":
            warnings.append(
                "Demand event count uses the legacy one-event-per-nonzero-bucket "
                "fallback; it is not an observed source-event count."
            )
        elif basis.event_count_source == "unavailable":
            warnings.append(
                "Demand event count is unavailable; demanded units remain distinct "
                "and are not presented as event counts."
            )
        window_source = getattr(basis, "observation_window_source", None)
        if window_source == "observed_span":
            warnings.append(
                "Configured observation bounds are unavailable; historical exposure "
                "was transparently inferred from the first observed bucket through "
                "the end of the last observed bucket."
            )
        elif (
            window_source == "unavailable"
            or basis.observation_start is None
            or basis.observation_end is None
        ):
            warnings.append(
                "The configured historical observation window is unavailable; "
                "no fixed fallback exposure was presented as observed truth."
            )

    constraints, constraint_warning = _constraint_trace(
        recommendation=recommendation,
        vendor_economics=vendor_economics,
        part_attributes=part_attributes,
    )
    if constraint_warning is not None:
        warnings.append(constraint_warning)

    calculation_evidence = (
        getattr(recommendation, "calculation_evidence", None)
        if recommendation is not None
        else None
    )
    if calculation_evidence is not None:
        as_of = _as_date(calculation_evidence.as_of)
        horizon_days = int(calculation_evidence.horizon_days)
        assert as_of is not None
        horizon_end = as_of + timedelta(days=horizon_days)
        scheduled_status = _availability_status(
            calculation_evidence, "scheduled_demand_status"
        )
        open_receipts_status = _availability_status(
            calculation_evidence, "open_receipts_status"
        )
        scheduled_due = float(calculation_evidence.scheduled_demand_due)
        open_receipts_due = float(calculation_evidence.open_receipts_due)
        scheduled_undated_lines = int(
            getattr(calculation_evidence, "scheduled_demand_undated_lines", 0)
        )
        scheduled_undated_units = int(
            getattr(calculation_evidence, "scheduled_demand_undated_units", 0)
        )
        open_undated_lines = int(
            getattr(calculation_evidence, "open_receipts_undated_lines", 0)
        )
        open_undated_units = int(
            getattr(calculation_evidence, "open_receipts_undated_units", 0)
        )
        overdue_receipts = float(calculation_evidence.overdue_open_receipts_due)
        for warning in (
            _availability_warning(
                label="scheduled-demand",
                status=scheduled_status,
                field="scheduled_demand_due",
                quantity=scheduled_due,
                undated_lines=scheduled_undated_lines,
                undated_units=scheduled_undated_units,
            ),
            _availability_warning(
                label="open-receipt",
                status=open_receipts_status,
                field="open_receipts_due",
                quantity=open_receipts_due,
                undated_lines=open_undated_lines,
                undated_units=open_undated_units,
            ),
        ):
            if warning is not None:
                warnings.append(warning)
        if overdue_receipts:
            warnings.append(
                f"{overdue_receipts:g} open receipt units are overdue as of "
                f"{as_of.isoformat()} but remain included because their source orders "
                "are still open; receipt is not guaranteed."
            )
        if float(calculation_evidence.repair_receipts_due) == 0.0:
            warnings.append(
                "Conservative Phase-1 repair-receipt rule: aggregate in-repair stock "
                "receives no supply credit until identity-aware, age-conditioned repair-"
                "return evidence exists. repair_receipts_due=0 is deliberate, not an "
                "observed return promise."
            )
        pooling_scope = str(
            getattr(calculation_evidence, "pooling_scope", "single_key")
        )
        excluded_member_keys = tuple(
            str(key)
            for key in getattr(
                calculation_evidence, "excluded_member_keys", ()
            )
        )
        if pooling_scope == "worklist_partial":
            warnings.append(
                "Interchange pooling is limited to the current worklist; excluded "
                "member keys: " + ", ".join(excluded_member_keys) + "."
            )

        return PlanningTraceView(
            **basis_values,
            calculation_source="served_calculation",
            as_of=_iso(as_of),
            horizon_days=horizon_days,
            horizon_end=_iso(horizon_end),
            projection_kind=str(calculation_evidence.projection_kind),
            served_historical_per_day=float(
                calculation_evidence.served_historical_per_day
            ),
            projected_historical_demand=float(
                calculation_evidence.projected_historical_demand
            ),
            scheduled_demand_status=scheduled_status,
            scheduled_demand_undated_lines=scheduled_undated_lines,
            scheduled_demand_undated_units=scheduled_undated_units,
            scheduled_demand_due=scheduled_due,
            projected_demand=float(calculation_evidence.projected_demand),
            dispatchable_available=float(
                calculation_evidence.dispatchable_available
            ),
            open_receipts_status=open_receipts_status,
            open_receipts_undated_lines=open_undated_lines,
            open_receipts_undated_units=open_undated_units,
            open_receipts_due=open_receipts_due,
            overdue_open_receipts_due=overdue_receipts,
            repair_receipts_due=float(calculation_evidence.repair_receipts_due),
            expected_receipts_due=float(calculation_evidence.expected_receipts_due),
            net_position=float(calculation_evidence.net_position),
            shortage_before_action=float(
                calculation_evidence.shortage_before_action
            ),
            pooled_group_id=calculation_evidence.pooled_group_id,
            pooling_scope=pooling_scope,
            excluded_member_keys=excluded_member_keys,
            members=tuple(
                PlanningMemberTraceView(
                    pn=str(member.pn),
                    location=str(member.location),
                    projection_kind=str(member.projection_kind),
                    projected_historical_demand=float(
                        member.projected_historical_demand
                    ),
                    scheduled_demand_status=_availability_status(
                        member, "scheduled_demand_status"
                    ),
                    scheduled_demand_undated_lines=int(
                        getattr(member, "scheduled_demand_undated_lines", 0)
                    ),
                    scheduled_demand_undated_units=int(
                        getattr(member, "scheduled_demand_undated_units", 0)
                    ),
                    scheduled_demand_due=float(member.scheduled_demand_due),
                    projected_demand=float(member.projected_demand),
                    dispatchable_available=float(
                        member.dispatchable_available
                    ),
                    open_receipts_status=_availability_status(
                        member, "open_receipts_status"
                    ),
                    open_receipts_undated_lines=int(
                        getattr(member, "open_receipts_undated_lines", 0)
                    ),
                    open_receipts_undated_units=int(
                        getattr(member, "open_receipts_undated_units", 0)
                    ),
                    open_receipts_due=float(member.open_receipts_due),
                    overdue_open_receipts_due=float(
                        member.overdue_open_receipts_due
                    ),
                    repair_receipts_due=float(member.repair_receipts_due),
                    expected_receipts_due=float(member.expected_receipts_due),
                    net_position=float(member.net_position),
                )
                for member in calculation_evidence.members
            ),
            constraints=constraints,
            warnings=tuple(warnings),
        )

    if recommendation is None:
        warnings.append(
            "No recommendation exists for this key; horizon-dependent trace values "
            "are unavailable."
        )
        return PlanningTraceView(
            **basis_values,
            calculation_source="unavailable",
            constraints=constraints,
            warnings=tuple(warnings),
        )

    # Compatibility only: reconstruct the evidence available to snapshots that
    # predate CalculationEvidence. Never claim this is the served model result.
    generated_at = _as_date(getattr(recommendation, "generated_at", None))
    raw_horizon = getattr(recommendation, "horizon_days", None)
    horizon_days = int(raw_horizon) if raw_horizon is not None else 0
    horizon_end = (
        generated_at + timedelta(days=horizon_days) if generated_at is not None else None
    )
    projected_historical = float(basis_values["historical_per_day"]) * horizon_days

    if scheduled_demand is None:
        scheduled_due = 0.0
        warnings.append(
            "Scheduled-demand evidence is unavailable; scheduled_demand_due=0 is "
            "an unavailable placeholder, not an observed zero."
        )
    elif generated_at is None:
        scheduled_due = 0.0
        warnings.append(
            "No selected recommendation as-of date is available; scheduled demand "
            "could not be assigned to a horizon."
        )
    else:
        scheduled_due = float(
            scheduled_units_in_horizon(
                scheduled_demand,
                as_of=generated_at,
                horizon_days=horizon_days,
            )
        )

    receipts_due, overdue_receipts, receipt_warnings = _open_receipts(
        open_orders=open_orders,
        as_of=generated_at,
        horizon_days=horizon_days,
    )
    warnings.extend(receipt_warnings)
    warnings.append(
        "This recommendation predates exact served-calculation evidence. Historical, "
        "scheduled-demand, and open-receipt values are legacy_reconstructed from "
        "available source snapshots; exact model, pooling, repair-receipt, and net-"
        "position reconciliation is unavailable."
    )

    selected_projection = float(getattr(recommendation, "projected_demand", 0.0))
    trace_projection = projected_historical + scheduled_due
    if abs(selected_projection - trace_projection) > 1e-6:
        warnings.append(
            "The legacy recommendation projected demand does not reconcile to the "
            "reconstructed key-level historical and scheduled evidence. It may include "
            "statistical or pooled demand that was not persisted."
        )

    return PlanningTraceView(
        **basis_values,
        calculation_source="legacy_reconstructed",
        as_of=_iso(generated_at),
        horizon_days=horizon_days,
        horizon_end=_iso(horizon_end),
        projected_historical_demand=projected_historical,
        scheduled_demand_status=(
            "available" if scheduled_demand is not None else "unavailable"
        ),
        scheduled_demand_due=scheduled_due,
        projected_demand=selected_projection,
        open_receipts_status=(
            "available" if open_orders is not None else "unavailable"
        ),
        open_receipts_due=float(receipts_due),
        overdue_open_receipts_due=float(overdue_receipts),
        constraints=constraints,
        warnings=tuple(warnings),
    )
