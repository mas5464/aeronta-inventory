"""#W2-1: PlannerStore.from_extract threads the statistical projector selection through to
RecommendationService, defaulting to today's (deterministic) behavior unchanged."""

from datetime import UTC, datetime
from pathlib import Path

from trax_io_forecasting.projector import StatisticalProjector
from trax_io_reco.service import RecommendationService

from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def test_default_does_not_pass_statistical_projector(monkeypatch):
    captured = {}
    original_init = RecommendationService.__init__

    def spy_init(self, **kwargs):
        captured.update(kwargs)
        original_init(self, **kwargs)

    monkeypatch.setattr(RecommendationService, "__init__", spy_init)

    PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )

    assert "projector" in captured
    assert captured["projector"] is None


def test_use_statistical_true_passes_statistical_projector(monkeypatch):
    captured = {}
    original_init = RecommendationService.__init__

    def spy_init(self, **kwargs):
        captured.update(kwargs)
        original_init(self, **kwargs)

    monkeypatch.setattr(RecommendationService, "__init__", spy_init)

    PlannerStore.from_extract(
        tenant_id="acme",
        extract_dir=str(_SAMPLE),
        now=datetime(2026, 4, 1, tzinfo=UTC),
        use_statistical=True,
    )

    assert isinstance(captured.get("projector"), StatisticalProjector)


def test_use_statistical_false_matches_default_behavior():
    default_rows = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    ).queue(limit=100)
    explicit_rows = PlannerStore.from_extract(
        tenant_id="acme",
        extract_dir=str(_SAMPLE),
        now=datetime(2026, 4, 1, tzinfo=UTC),
        use_statistical=False,
    ).queue(limit=100)

    # recommendation_id is a freshly-minted ULID per run, so compare the stable
    # (pn, location, recommended_quantity) fields rather than the identifiers.
    def _key(rows):
        return [(r.pn, r.location, r.recommended_quantity) for r in rows]

    assert _key(default_rows) == _key(explicit_rows)
