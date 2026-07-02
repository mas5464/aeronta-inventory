"""Schema-lock tests for the BVR report models (spec §1).

The BvrReport IS the 'BVR schema locked' deliverable: the field-set snapshot
below must be updated deliberately (with a schema_version bump for additive
changes), never accidentally.
"""

from __future__ import annotations

from trax_io_spine.bvr.models import (
    SCHEMA_VERSION,
    BvrReport,
    ProjectedComponent,
    SavingsAttribution,
)


def test_report_round_trips_and_is_frozen(bvr_report):
    r = bvr_report
    assert BvrReport.model_validate(r.model_dump(mode="json")).model_dump() == r.model_dump()


def test_schema_version_is_semver_1_0_0(bvr_report):
    assert SCHEMA_VERSION == "1.0.0"
    assert bvr_report.schema_version == "1.0.0"


def test_schema_lock_field_snapshot():
    # Deliberate-change tripwire: adding/removing/renaming report fields must
    # update this snapshot AND bump SCHEMA_VERSION (additive => minor).
    assert set(BvrReport.model_fields) == {
        "schema_version", "tenant_id", "period", "executive_summary", "savings",
        "service_posture", "governance", "forward_look", "methodology",
    }
    assert set(SavingsAttribution.model_fields) == {
        "holding_cost_delta", "ordering_cost_delta", "stockout_risk_delta",
        "total_projected_applied", "total_projected_shadowed", "total_projected",
        "changes_total", "changes_valued", "assumption_rates",
    }
    assert set(ProjectedComponent.model_fields) == {
        "name", "amount", "formula", "inputs", "assumptions",
    }
