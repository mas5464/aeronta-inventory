"""Optional thin FastAPI read API (spec §8). Behind the `api` extra. FastAPI is imported
lazily inside ``create_app`` so the core package imports without the web framework.

``run_batch`` decouples the HTTP surface from how the batch is produced — production wires
a real RecommendationService; tests pass a precomputed-batch callable.
"""

from __future__ import annotations

from collections.abc import Callable

from trax_io_reco.contracts.recommendation import RecommendationBatch

BatchProvider = Callable[[str, int], RecommendationBatch]  # (tenant_id, reporting_horizon) -> batch


def create_app(run_batch: BatchProvider):  # noqa: ANN201 (FastAPI imported lazily)
    from fastapi import FastAPI

    app = FastAPI(title="Trax IO Recommendation Engine", version="0.1.0")

    @app.get("/v1/recommendations")
    def list_recommendations(  # noqa: ANN202
        tenant: str,
        location: str | None = None,
        type: str | None = None,
        min_confidence: float = 0.0,
        reporting_horizon: int = 30,
    ):
        batch = run_batch(tenant, reporting_horizon)
        recs = batch.recommendations
        if location is not None:
            recs = tuple(r for r in recs if r.current_location == location)
        if type is not None:
            recs = tuple(r for r in recs if r.type.value == type)
        recs = tuple(r for r in recs if r.confidence_score >= min_confidence)
        return batch.model_copy(update={"recommendations": recs})

    @app.get("/v1/recommendations/{pn}/{location}")
    def get_for_part(pn: str, location: str, tenant: str, reporting_horizon: int = 30):  # noqa: ANN202
        batch = run_batch(tenant, reporting_horizon)
        recs = tuple(
            r
            for r in batch.recommendations
            if r.part_number == pn and r.current_location == location
        )
        return batch.model_copy(update={"recommendations": recs})

    return app
