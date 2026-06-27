from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trax_io_spine.contracts import (
    ApprovalTask,
    AutonomyTier,
    GuardrailOutcome,
    GuardrailStatus,
    OrchestrationResult,
    WritebackRequest,
    WritebackResult,
    WritebackStatus,
)


def test_guardrail_outcome_round_trips_json() -> None:
    out = GuardrailOutcome(
        recommendation_id="r-1",
        status=GuardrailStatus.APPROVED_FOR_WRITE,
        tier=AutonomyTier.AUTONOMOUS,
        delta_pct=0.18,
    )
    assert GuardrailOutcome.model_validate_json(out.model_dump_json()) == out


def test_approval_task_rejects_negative_priority() -> None:
    with pytest.raises(ValidationError):
        ApprovalTask(
            task_id="t-1", tenant_id="acme", pn="PN-A", location="LOC-1",
            tier=AutonomyTier.ADVISOR, priority_score=-1.0,
        )


def test_writeback_request_requires_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        WritebackRequest(
            tenant_id="acme", pn="PN-A", location="LOC-1",
            rop=5, eoq=4, safety_stock=2, max_stock=9, provenance_id="p-1",
            idempotency_key="",
        )


def test_orchestration_result_defaults_are_empty_tuples() -> None:
    res = OrchestrationResult(tenant_id="acme", generated_at=datetime.now(UTC))
    assert res.written == () and res.queued == () and res.rejected == ()


def test_writeback_result_carries_deferred_status() -> None:
    r = WritebackResult(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        status=WritebackStatus.DEFERRED_OPEN_ORDER,
    )
    assert r.status is WritebackStatus.DEFERRED_OPEN_ORDER
