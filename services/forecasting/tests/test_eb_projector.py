import math
from datetime import date

from trax_io_feature_store.schemas import DemandHistory, DemandObservation
from trax_io_reco.contracts.context import ScheduledDemandItem
from trax_io_reco.contracts.enums import EvidenceKind, Regime

from tests.conftest import make_context
from trax_io_forecasting.eb_projector import EmpiricalBayesProjector
from trax_io_forecasting.peer_priors import PeerPriorProvider, PeerRecord


def _provider() -> PeerPriorProvider:
    recs = [PeerRecord("32", 1, "rotable", c, 730.0) for c in (0, 1, 2, 0, 3)]
    return PeerPriorProvider.fit(recs, min_peers=5)


def test_non_ultra_rare_delegates_to_fallback() -> None:
    calls: dict[str, Regime] = {}

    class FB:
        def project(self, *, context, regime):
            calls["hit"] = regime
            return "DELEGATED"

    proj = EmpiricalBayesProjector(_provider(), fallback=FB())
    ctx = make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[1])
    assert proj.project(context=ctx, regime=Regime.MODERATE) == "DELEGATED"
    assert calls["hit"] == Regime.MODERATE


def test_ultra_rare_emits_compound_poisson_with_shrunken_lambda() -> None:
    # The compatibility override is explicit for legacy history. New persisted
    # histories use their configured observation interval.
    proj = EmpiricalBayesProjector(_provider(), basis_window_days=730)
    ctx = make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[1])
    dp = proj.project(context=ctx, regime=Regime.ULTRA_RARE)
    assert dp.dist_kind == "COMPOUND_POISSON"
    assert dp.dist_params["clump_p"] == 1.0
    own_rate = 1.0 / 730.0
    assert dp.dist_params["lambda"] > own_rate  # shrunk up toward the peer mean
    assert dp.std_per_day >= math.sqrt(dp.dist_params["lambda"])  # widened beyond Poisson
    assert dp.forecast_model == "gamma-poisson-empirical-bayes"
    assert dp.forecast_version == "gamma-poisson-eb-v1"


def test_new_pn_zero_history_shrinks_to_peer_mean() -> None:
    proj = EmpiricalBayesProjector(_provider())
    ctx = make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[])
    dp = proj.project(context=ctx, regime=Regime.ULTRA_RARE)
    prior = _provider().get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    assert abs(dp.historical_component - prior.mean) < 1e-9


def test_project_fails_safe_to_fallback_when_eb_path_raises() -> None:
    class BoomProvider:
        def get_prior(self, **kwargs):
            raise RuntimeError("boom")

    class FB:
        def project(self, *, context, regime):
            return "FALLBACK"

    proj = EmpiricalBayesProjector(BoomProvider(), fallback=FB())
    ctx = make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[1])
    assert proj.project(context=ctx, regime=Regime.ULTRA_RARE) == "FALLBACK"


def test_ultra_rare_uses_configured_exposure_and_keeps_schedule_discrete() -> None:
    proj = EmpiricalBayesProjector(_provider())
    ctx = make_context(
        ata_chapter="32",
        canonical_tier=1,
        part_class="rotable",
        removals=[1],
    )
    history = DemandHistory(
        tenant_id=ctx.tenant_id,
        pn=ctx.pn,
        location=ctx.location,
        observations=(
            DemandObservation(
                bucket="month",
                period_start=date(2024, 1, 1),
                removals=4,
                issues=0,
                removal_events=1,
                issue_events=0,
            ),
        ),
        observation_start=date(2024, 1, 1),
        observation_end=date(2025, 12, 31),
        bucket="month",
        event_count_source="observed",
        extract_date=ctx.demand_history.extract_date,
    )
    ctx = ctx.model_copy(
        update={
            "demand_history": history,
            "scheduled_demand": (
                ScheduledDemandItem(
                    due_date=date(2026, 1, 31),
                    qty=6,
                    source_ref="EB-SCHEDULE",
                    source_kind=EvidenceKind.TASK_CARD,
                ),
            ),
        }
    )

    projection = proj.project(context=ctx, regime=Regime.ULTRA_RARE)

    assert projection.basis_window_days == 731
    assert projection.mean_per_day == projection.historical_component
    assert projection.scheduled_component == 0.0
    assert projection.scheduled_demand_total == 6.0
    assert projection.scheduled_by_date == {date(2026, 1, 31): 6.0}
