"""Shared BVR test fixture: one fully-populated BvrReport."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest


@pytest.fixture
def bvr_report():
    from trax_io_spine.bvr.models import (
        SCHEMA_VERSION,
        BvrPeriod,
        BvrReport,
        ExecutiveSummary,
        ForwardLook,
        ForwardOpportunity,
        Governance,
        Methodology,
        ProjectedComponent,
        SavingsAttribution,
        ServicePosture,
        TierPosture,
    )

    def component(name: str, amount: str) -> ProjectedComponent:
        return ProjectedComponent(
            name=name, amount=Decimal(amount),
            formula="Δ(safety_stock + EOQ/2) × unit_cost × holding_rate × period_fraction",
            inputs={"changes": 1}, assumptions=("holding_cost_rate=0.25/yr",),
        )

    return BvrReport(
        schema_version=SCHEMA_VERSION,
        tenant_id="acme",
        period=BvrPeriod(
            extract_date="2024-04-01", decision_window_start=None,
            decision_window_end=None, generated_at=datetime(2026, 4, 1, tzinfo=UTC),
            label="Snapshot 2024-04-01",
        ),
        executive_summary=ExecutiveSummary(
            total_projected=Decimal("51.39"), changes_applied=1, changes_shadowed=0,
            keys_under_management=1, open_pipeline_value=Decimal("100.00"),
            service_headline="1/1 tiers at target posture",
        ),
        savings=SavingsAttribution(
            holding_cost_delta=component("holding_cost_delta", "-14.58"),
            ordering_cost_delta=component("ordering_cost_delta", "64.64"),
            stockout_risk_delta=component("stockout_risk_delta", "1.33"),
            total_projected_applied=Decimal("51.39"),
            total_projected_shadowed=Decimal("0.00"),
            total_projected=Decimal("51.39"),
            changes_total=1, changes_valued=1,
            assumption_rates={
                "holding_cost_rate": 0.25, "per_order_cost": 85.0,
                "stockout_proxy_fraction": 0.10, "period_fraction": 1 / 12,
            },
        ),
        service_posture=ServicePosture(
            tiers=(
                TierPosture(tier=1, target_fill_rate=0.995, keys=1,
                            keys_at_posture=1, posture_rate=1.0),
            ),
            note="Posture (ROP covers mean lead-time demand), not realized fill rate.",
        ),
        governance=Governance(
            recommendations_total=2, pending=1, approved=1, rejected=0, deferred=0,
            approval_rate=0.5, override_rate=0.0,
            writes_written=1, writes_shadowed=0, writes_failed=0,
            writes_deferred_open_order=0, rollbacks=0,
            tier_mix={"A": 0, "B": 1, "C": 0}, kill_switch_engaged=False,
        ),
        forward_look=ForwardLook(
            open_pipeline_value=Decimal("100.00"),
            projected_demand_horizon=12.5,
            top_opportunities=(
                ForwardOpportunity(pn="PN1", location="YYZ", type="purchase",
                                   estimated_cost_impact=Decimal("100.00")),
            ),
        ),
        methodology=Methodology(
            formulas=("holding: Δ(ss + EOQ/2) × unit_cost × 0.25/yr × 1/12",),
            assumption_rates={"holding_cost_rate": 0.25},
            ledger_entries=1, recommendations=2, keys=1,
            input_snapshot_hashes=("abc123",),
            agent_version="spine-0.1.0", generated_by="trax_io_spine.bvr",
        ),
    )
