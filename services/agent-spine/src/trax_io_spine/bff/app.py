"""FastAPI backend-for-frontend for the Planner UI ('Trax IO Review')."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from trax_io_spine.bff.models import (
    ActionResult,
    BulkApproveFilter,
    DeferRequest,
    KillSwitchState,
    PartContext,
    QueueRow,
    RecommendationDetail,
    RejectRequest,
    TaskStatus,
)
from trax_io_spine.bff.store import KillSwitchEngaged, PlannerStore, RecommendationNotFound
from trax_io_spine.contracts import HistoryEntry, RollbackRequest, RollbackResult


def create_planner_app(stores: dict[str, PlannerStore]) -> FastAPI:
    app = FastAPI(title="Trax IO Review — Planner BFF")

    def _store(tenant_id: str) -> PlannerStore:
        store = stores.get(tenant_id)
        if store is None:
            raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
        return store

    base = "/v1/tenants/{tenant_id}"

    @app.get(base + "/recommendations")
    def queue(
        tenant_id: str, status: TaskStatus = TaskStatus.PENDING, limit: int = 50
    ) -> list[QueueRow]:
        return _store(tenant_id).queue(status=status, limit=limit)

    @app.get(base + "/recommendations/{rec_id}")
    def detail(tenant_id: str, rec_id: str) -> RecommendationDetail:
        try:
            return _store(tenant_id).detail(rec_id)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(base + "/recommendations/{rec_id}/approve")
    def approve(tenant_id: str, rec_id: str) -> ActionResult:
        store = _store(tenant_id)
        try:
            return store.approve(rec_id)
        except KillSwitchEngaged as exc:
            raise HTTPException(status_code=423, detail="kill switch engaged") from exc
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(base + "/recommendations/{rec_id}/reject")
    def reject(tenant_id: str, rec_id: str, body: RejectRequest) -> ActionResult:
        try:
            return _store(tenant_id).reject(rec_id, body.reason, body.detail)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(base + "/recommendations/{rec_id}/defer")
    def defer(tenant_id: str, rec_id: str, body: DeferRequest) -> ActionResult:
        try:
            return _store(tenant_id).defer(rec_id, body.until)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(base + "/recommendations/bulk-approve")
    def bulk_approve(tenant_id: str, body: BulkApproveFilter) -> dict:
        store = _store(tenant_id)
        try:
            count, results = store.bulk_approve(body)
        except KillSwitchEngaged as exc:
            raise HTTPException(status_code=423, detail="kill switch engaged") from exc
        return {"approved_count": count, "results": [r.model_dump(mode="json") for r in results]}

    @app.get(base + "/history")
    def history(tenant_id: str, pn: str, location: str) -> list[HistoryEntry]:
        return list(_store(tenant_id).history(pn=pn, location=location))

    @app.post(base + "/rollback")
    def rollback(tenant_id: str, body: RollbackRequest) -> RollbackResult:
        return _store(tenant_id).rollback(body)

    @app.get(base + "/killswitch")
    def get_killswitch(tenant_id: str) -> KillSwitchState:
        return KillSwitchState(engaged=_store(tenant_id).kill_switch)

    @app.post(base + "/killswitch")
    def set_killswitch(tenant_id: str, body: KillSwitchState) -> KillSwitchState:
        _store(tenant_id).set_kill_switch(body.engaged)
        return body

    @app.get(base + "/parts/{pn}/{location}")
    def part_context(tenant_id: str, pn: str, location: str) -> PartContext:
        try:
            return _store(tenant_id).part_context(pn, location)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app
