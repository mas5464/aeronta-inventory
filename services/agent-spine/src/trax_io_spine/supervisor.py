"""Deterministic Supervisor: #2 -> #11 -> guardrail -> writeback -> OrchestrationResult.

The seam an LLM Supervisor wraps later. All collaborators are injected; by default it builds
the real #11 RecommendationService and writes to an in-memory target.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from trax_io_feature_store import FeatureStoreClient, TenantContext
from trax_io_reco.contracts.recommendation import Recommendation
from trax_io_reco.service import RecommendationService

from trax_io_spine.contracts import (
    GuardrailStatus,
    OrchestrationResult,
    WritebackRequest,
    WritebackResult,
    WritebackStatus,
)
from trax_io_spine.guardrail.enforce import GuardrailEnforcer
from trax_io_spine.identity import tenant_scope
from trax_io_spine.writeback.target import InMemoryWritebackTarget, WritebackTarget


def to_writeback_request(rec: Recommendation, *, idempotency_key: str) -> WritebackRequest:
    if rec.policy is None:  # pragma: no cover -- supervisor only calls this for approved policies
        raise ValueError("recommendation has no policy to write")
    p = rec.policy
    return WritebackRequest(
        tenant_id=rec.tenant_id, pn=rec.part_number, location=rec.current_location,
        rop=p.rop, eoq=p.eoq, safety_stock=p.safety_stock, max_stock=p.max_stock,
        provenance_id=p.provenance_id, idempotency_key=idempotency_key,
    )


class Supervisor:
    def __init__(
        self,
        *,
        feature_store: FeatureStoreClient,
        inventory_state: Any,
        enforcer: GuardrailEnforcer | None = None,
        writeback: WritebackTarget | None = None,
        config: Any = None,
        service: Any = None,
    ) -> None:
        self._service = service or RecommendationService(
            feature_store=feature_store, inventory_state=inventory_state, config=config
        )
        self._enforcer = enforcer or GuardrailEnforcer()
        self._writeback: WritebackTarget = writeback or InMemoryWritebackTarget()

    def run(
        self,
        *,
        tenant: TenantContext,
        keys: list[tuple[str, str]],
        now: datetime,
        reporting_horizon_days: int = 30,
    ) -> OrchestrationResult:
        with tenant_scope(tenant):
            batch = self._service.run(
                tenant=tenant, keys=keys, now=now,
                reporting_horizon_days=reporting_horizon_days,
            )
            written: list[WritebackResult] = []
            deferred: list[WritebackResult] = []
            failed: list[WritebackResult] = []
            queued = []
            rejected = []

            for rec in batch.recommendations:
                outcome = self._enforcer.enforce(rec)
                if outcome.status is GuardrailStatus.REJECTED_HARD_GUARDRAIL:
                    rejected.append(outcome)
                elif outcome.status is GuardrailStatus.QUEUED_FOR_APPROVAL:
                    if outcome.approval_task is not None:
                        queued.append(outcome.approval_task)
                else:  # APPROVED_FOR_WRITE
                    # Idempotency keyed on the content-addressed input snapshot hash (not run
                    # date): re-running the same extract dedups; a new data snapshot is a new write.
                    idem = (
                        f"{rec.tenant_id}:{rec.part_number}:"
                        f"{rec.current_location}:{rec.input_snapshot_hash}"
                    )
                    result = self._writeback.write(to_writeback_request(rec, idempotency_key=idem))
                    if result.status is WritebackStatus.WRITTEN:
                        written.append(result)
                    elif result.status is WritebackStatus.DEFERRED_OPEN_ORDER:
                        deferred.append(result)
                    else:
                        failed.append(result)

            summary = Counter(
                {
                    "recommendations": len(batch.recommendations),
                    "written": len(written),
                    "deferred": len(deferred),
                    "failed": len(failed),
                    "queued": len(queued),
                    "rejected": len(rejected),
                    "skipped": len(batch.skipped),
                }
            )
            return OrchestrationResult(
                tenant_id=tenant.tenant_id, generated_at=now,
                written=tuple(written), deferred=tuple(deferred), failed=tuple(failed),
                queued=tuple(queued), rejected=tuple(rejected), skipped=batch.skipped,
                summary=dict(summary),
            )
