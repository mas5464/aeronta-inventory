"""End-to-end integration tests for EmpiricalBayesProjector + build_eb_projector (Slice C).

Model: tests/test_gb_integration.py — same fixtures/service-construction approach, only
projector= and regime differ.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from trax_io_feature_store import TenantContext
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.demand.projection import HistoricalScheduledProjector
from trax_io_reco.service import RecommendationService

from trax_io_forecasting.eb_projector import EmpiricalBayesProjector, build_eb_projector

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ultra_rare_contexts(make_context):  # make_context injected from conftest fixture
    """A peer group of >=3 ultra-rare PartLocationContext objects sharing the same
    (ata_chapter="32", canonical_tier=1, part_class="rotable") peer-group key.

    We use min_peers=2 in the tests, so 4 contexts is comfortably above threshold.

    Peer group mean rate computation:
      ctx0: removals=[1]        → count=1, rate=1/730 ≈ 0.00137
      ctx1: removals=[]         → count=0, exposure=0 (excluded from rate calc per peer_priors)
      ctx2: removals=[0, 1]     → count=1, rate=1/730 ≈ 0.00137
      ctx3: removals=[2]        → count=2, rate=2/730 ≈ 0.00274

    For the mirror-lambda-diff test we use ctx0 (removals=[1]).
    Deterministic lambda = count / basis = 1 / 730 ≈ 0.00137.
    EB lambda = posterior_rate(prior, 1, 730).

    The prior is fit from peer rates [1/730, 1/730, 2/730] (ctx1 has exposure=0 so is excluded).
    Prior mean ≈ mean(rates) ≈ 4/3/730 ≈ 0.00183.
    Posterior pulls ctx0's rate toward 0.00183 → EB lambda ≈ 0.00183 ≠ 0.00137 (det).
    The two values differ because the prior mean differs from ctx0's own historical rate.
    """
    return [
        make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[1]),
        make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[]),
        make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[0, 1]),
        make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[2]),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_eb_projector_fits_provider_from_batch(ultra_rare_contexts):
    proj = build_eb_projector(ultra_rare_contexts, min_peers=2)
    assert isinstance(proj, EmpiricalBayesProjector)
    dp = proj.project(context=ultra_rare_contexts[0], regime=Regime.ULTRA_RARE)
    assert dp.dist_kind == "COMPOUND_POISSON"


def test_projection_mirrors_deterministic_ultra_rare_shape_except_lambda(ultra_rare_contexts):
    # ctx0: removals=[1] — own historical rate 1/730; peer prior pulls toward ~4/3/730 → differs.
    ctx = ultra_rare_contexts[0]
    eb = build_eb_projector(ultra_rare_contexts, min_peers=2).project(
        context=ctx, regime=Regime.ULTRA_RARE
    )
    det = HistoricalScheduledProjector().project(context=ctx, regime=Regime.ULTRA_RARE)

    # Same dist shape, scheduled component, dimension breakdowns, basis, and param key set.
    assert eb.dist_kind == det.dist_kind == "COMPOUND_POISSON"
    assert eb.scheduled_component == det.scheduled_component
    assert eb.by_aircraft == det.by_aircraft and eb.by_task == det.by_task
    assert eb.basis_window_days == det.basis_window_days
    assert set(eb.dist_params) == set(det.dist_params)  # {"lambda", "clump_p"}

    # EB-shrunken lambda must differ from the raw historical rate used by the deterministic
    # projector.  ctx0 has count=1 → det lambda=1/730≈0.00137.  The peer prior mean is
    # ~4/3/730≈0.00183, so the posterior shrinks ctx0's rate toward the prior → EB lambda
    # is strictly above 0.00137 and the two are not equal.
    assert eb.dist_params["lambda"] != det.dist_params["lambda"]


def test_recommendation_service_runs_with_eb_projector(ultra_rare_contexts):
    """Mirror test_gb_integration.py exactly: swap only projector=."""
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    svc = RecommendationService(
        feature_store=fs,
        inventory_state=inv,
        projector=build_eb_projector(ultra_rare_contexts, min_peers=2),
    )
    batch = svc.run(
        tenant=TenantContext(tenant_id=tid), keys=keys, now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    assert batch.summary.total >= 0  # the injected EB projector runs end-to-end without error
