"""FastAPI backend-for-frontend for the Planner UI ('Trax IO Review')."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType

from trax_io_spine.bff.billing import BillingSummary
from trax_io_spine.bff.csv_export import queue_rows_to_csv
from trax_io_spine.bff.ingest_routes import router as ingest_router
from trax_io_spine.bff.members_routes import router as members_router
from trax_io_spine.bff.models import (
    ActionResult,
    BulkApproveFilter,
    DashboardSummary,
    DeferRequest,
    FeedsSummary,
    ForecastSummary,
    KillSwitchState,
    PagedQueue,
    PartContext,
    QueueSortKey,
    RecommendationDetail,
    RejectRequest,
    SaveScenarioRequest,
    Scenario,
    ScenarioAuditEvent,
    ScenarioParamsWire,
    ScenarioSolveResult,
    TaskStatus,
)
from trax_io_spine.bff.store import (
    KillSwitchEngaged,
    PlannerStore,
    RecommendationNotFound,
    ScenarioNotFound,
)
from trax_io_spine.bvr.models import BvrReport
from trax_io_spine.bvr.pdf import PdfUnavailable, render_pdf
from trax_io_spine.bvr.render import render_html
from trax_io_spine.contracts import HistoryEntry, RollbackRequest, RollbackResult


def create_planner_app(
    stores: dict[str, PlannerStore],
    *,
    verifier: object | None = None,
    tenant_uuids: dict[str, str] | None = None,
    admin_api: object | None = None,
    members_stores: dict | None = None,
    upload_minter: object | None = None,
    ingest_stores: dict | None = None,
    subscription_status_for=None,
    billing_reader=None,
) -> FastAPI:
    app = FastAPI(title="Trax IO Review — Planner BFF")

    if verifier is not None:
        from trax_io_spine.bff.auth import AuthMiddleware

        app.add_middleware(
            AuthMiddleware,
            verifier=verifier,
            tenant_uuids=tenant_uuids,
            subscription_status_for=subscription_status_for,
        )

    app.state.admin_api = admin_api
    app.state.members_stores = members_stores or {}
    # C3 Task 5: tenant_uuids also lives on app.state (not just the middleware
    # closure above) so ingest_routes.py can resolve a slug -> uuid for the
    # `{tenant_uuid}/{batch_id}/{name}` upload path without a third wiring path.
    app.state.tenant_uuids = tenant_uuids or {}
    app.state.upload_minter = upload_minter
    app.state.ingest_stores = ingest_stores or {}
    app.include_router(members_router)
    app.include_router(ingest_router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "tenants": sorted(stores)}

    def _store(tenant_id: str, request: Request | None = None) -> PlannerStore:
        store = stores.get(tenant_id)
        if store is None:
            raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
        # C3 Task 0a: attribute decisions/writeback to the verified caller.
        # AuthMiddleware stashes verified JWT claims at request.state.claims
        # (absent entirely in dev/no-verifier mode, or on routes called
        # without a request — getattr covers both). Only PgPlannerStore
        # exposes with_principal; the in-memory PlannerStore (local/dev/tests
        # without claims) is untouched and keeps its own "planner" default.
        if request is not None:
            claims = getattr(request.state, "claims", None)
            principal = claims.get("sub") if claims else None
            if principal and hasattr(store, "with_principal"):
                return store.with_principal(principal)
        return store

    def _bvr_or_500(tenant_id: str) -> BvrReport:
        # Unlike _safe() (store.py) — which degrades a single optional field to None —
        # a report response can't be partially built, so an unexpected construction
        # failure is mapped to a clean 500 instead of leaking a raw traceback. The
        # 404 from an unknown tenant (_store above) is a known, correct error and
        # propagates unchanged.
        store = _store(tenant_id)
        try:
            return store.bvr()
        except Exception as exc:  # noqa: BLE001 - defend the route, not a specific failure mode
            raise HTTPException(status_code=500, detail="failed to build BVR report") from exc

    base = "/v1/tenants/{tenant_id}"

    @app.get(base + "/recommendations")
    def queue(
        tenant_id: str,
        status: TaskStatus = TaskStatus.PENDING,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        sort_by: QueueSortKey = QueueSortKey.PRIORITY,
        sort_dir: Literal["asc", "desc"] = "desc",
        # Suppressed below: ruff's B008 "immutable FastAPI call" exemption doesn't
        # recognize Query(...) defaults typed with a custom Enum subtype (vs. builtins
        # like int/str) as immutable — a false positive; this is the standard FastAPI
        # optional-query-param pattern (see `limit`/`offset` above for the same
        # Query(...)-default idiom on builtin types, which ruff accepts unflagged).
        tier: AutonomyTier | None = Query(None),  # noqa: B008
        type: RecommendationType | None = Query(None),  # noqa: B008
        aog_min: AogRiskLevel | None = Query(None),  # noqa: B008
    ) -> PagedQueue:
        # Free-text search stays client-side over the loaded page for now — not
        # implemented server-side in this task (see store docstring). Sort/filter
        # (sort_by/sort_dir/tier/type/aog_min) are server-side (task F2); every
        # param defaults to reproducing the pre-F2 behavior byte-for-byte. Wire name
        # is `type` (matches BulkApproveFilter/QueueRow's `type` field); passed to the
        # store as `type_` since `type` shadows the builtin.
        items, total = _store(tenant_id).list_queue_page(
            status=status,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            tier=tier,
            type_=type,
            aog_min=aog_min,
        )
        return PagedQueue(items=tuple(items), total=total, limit=limit, offset=offset)

    @app.get(base + "/recommendations/export.csv")
    def export_csv(
        tenant_id: str,
        status: TaskStatus = TaskStatus.PENDING,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY,
        sort_dir: Literal["asc", "desc"] = "desc",
        tier: AutonomyTier | None = Query(None),  # noqa: B008
        type: RecommendationType | None = Query(None),  # noqa: B008
        aog_min: AogRiskLevel | None = Query(None),  # noqa: B008
    ) -> Response:
        # Full filtered set, no pagination (export must cover every matching row,
        # not one page). Same filter/sort params as the queue route above, minus
        # limit/offset. MUST be declared before /recommendations/{rec_id} or the
        # literal "export.csv" path is captured as rec_id. `type` shadows the
        # builtin only within this signature (matches the queue route's param name,
        # which the QueueRow/BulkApproveFilter wire contract uses); passed to the
        # store as type_.
        rows = _store(tenant_id).list_queue_all(
            status=status,
            sort_by=sort_by,
            sort_dir=sort_dir,
            tier=tier,
            type_=type,
            aog_min=aog_min,
        )
        return Response(
            content=queue_rows_to_csv(rows),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="trax-io-{status}-recommendations.csv"'
                ),
            },
        )

    @app.get(base + "/recommendations/{rec_id}")
    def detail(tenant_id: str, rec_id: str) -> RecommendationDetail:
        try:
            return _store(tenant_id).detail(rec_id)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(base + "/recommendations/{rec_id}/approve")
    def approve(tenant_id: str, rec_id: str, request: Request) -> ActionResult:
        store = _store(tenant_id, request)
        try:
            return store.approve(rec_id)
        except KillSwitchEngaged as exc:
            raise HTTPException(status_code=423, detail="kill switch engaged") from exc
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(base + "/recommendations/{rec_id}/reject")
    def reject(tenant_id: str, rec_id: str, body: RejectRequest, request: Request) -> ActionResult:
        try:
            return _store(tenant_id, request).reject(rec_id, body.reason, body.detail)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(base + "/recommendations/{rec_id}/defer")
    def defer(tenant_id: str, rec_id: str, body: DeferRequest, request: Request) -> ActionResult:
        try:
            return _store(tenant_id, request).defer(rec_id, body.until)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(base + "/recommendations/bulk-approve")
    def bulk_approve(tenant_id: str, body: BulkApproveFilter, request: Request) -> dict:
        store = _store(tenant_id, request)
        try:
            count, results = store.bulk_approve(body)
        except KillSwitchEngaged as exc:
            raise HTTPException(status_code=423, detail="kill switch engaged") from exc
        return {"approved_count": count, "results": [r.model_dump(mode="json") for r in results]}

    @app.get(base + "/history")
    def history(tenant_id: str, pn: str, location: str) -> list[HistoryEntry]:
        return list(_store(tenant_id).history(pn=pn, location=location))

    @app.post(base + "/rollback")
    def rollback(tenant_id: str, body: RollbackRequest, request: Request) -> RollbackResult:
        return _store(tenant_id, request).rollback(body)

    @app.get(base + "/killswitch")
    def get_killswitch(tenant_id: str) -> KillSwitchState:
        return KillSwitchState(engaged=_store(tenant_id).kill_switch)

    @app.post(base + "/killswitch")
    def set_killswitch(tenant_id: str, body: KillSwitchState, request: Request) -> KillSwitchState:
        _store(tenant_id, request).set_kill_switch(body.engaged)
        return body

    @app.get(base + "/parts/{pn}/{location}")
    def part_context(tenant_id: str, pn: str, location: str) -> PartContext:
        try:
            return _store(tenant_id).part_context(pn, location)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(base + "/dashboard")
    def dashboard(tenant_id: str) -> DashboardSummary:
        return _store(tenant_id).dashboard()

    @app.get(base + "/billing")
    def billing(tenant_id: str) -> BillingSummary:
        # A GET — never write-gated (AuthMiddleware's C4 gate only touches
        # writes; a tenant with a lapsed subscription must still be able to
        # see its own billing status). Resolved off app.state.tenant_uuids
        # (not the stores dict) since billing_reader reads Postgres directly
        # by uuid, independent of which PlannerStore is configured.
        if billing_reader is None:
            raise HTTPException(status_code=503, detail="billing not configured")
        uuid = app.state.tenant_uuids.get(tenant_id)
        if uuid is None:
            raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
        try:
            return billing_reader(uuid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(base + "/reports/bvr")
    def bvr_json(tenant_id: str) -> BvrReport:
        return _bvr_or_500(tenant_id)

    @app.get(base + "/reports/bvr.html")
    def bvr_html(tenant_id: str) -> Response:
        html = render_html(_bvr_or_500(tenant_id))
        return Response(content=html, media_type="text/html")

    @app.get(base + "/reports/bvr.pdf")
    def bvr_pdf(tenant_id: str) -> Response:
        html = render_html(_bvr_or_500(tenant_id))
        try:
            pdf = render_pdf(html)
        except PdfUnavailable as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="bvr-{tenant_id}.pdf"'},
        )

    @app.get(base + "/forecast")
    def forecast(tenant_id: str) -> ForecastSummary:
        return _store(tenant_id).forecast_summary()

    @app.get(base + "/feeds")
    def feeds(tenant_id: str) -> FeedsSummary:
        return _store(tenant_id).feeds_summary()

    @app.post(base + "/scenarios/solve")
    def solve_scenario(tenant_id: str, body: ScenarioParamsWire) -> ScenarioSolveResult:
        return _store(tenant_id).solve_scenario(body)

    @app.post(base + "/scenarios")
    def save_scenario(tenant_id: str, body: SaveScenarioRequest) -> Scenario:
        return _store(tenant_id).save_scenario(body.name, body.params, body.result)

    @app.get(base + "/scenarios")
    def list_scenarios(tenant_id: str) -> list[Scenario]:
        return _store(tenant_id).list_scenarios()

    @app.get(base + "/scenarios/{scenario_id}")
    def get_scenario(tenant_id: str, scenario_id: str) -> Scenario:
        try:
            return _store(tenant_id).get_scenario(scenario_id)
        except ScenarioNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete(base + "/scenarios/{scenario_id}")
    def delete_scenario(tenant_id: str, scenario_id: str) -> dict:
        try:
            _store(tenant_id).delete_scenario(scenario_id)
        except ScenarioNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": scenario_id}

    @app.post(base + "/scenarios/{scenario_id}/commit")
    def commit_scenario(tenant_id: str, scenario_id: str) -> ScenarioAuditEvent:
        try:
            return _store(tenant_id).commit_scenario(scenario_id)
        except ScenarioNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app
