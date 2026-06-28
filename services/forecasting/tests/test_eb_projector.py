import math

from trax_io_reco.contracts.enums import Regime

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
    proj = EmpiricalBayesProjector(_provider())
    ctx = make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[1])
    dp = proj.project(context=ctx, regime=Regime.ULTRA_RARE)
    assert dp.dist_kind == "COMPOUND_POISSON"
    assert dp.dist_params["clump_p"] == 1.0
    own_rate = 1.0 / 730.0
    assert dp.dist_params["lambda"] > own_rate  # shrunk up toward the peer mean
    assert dp.std_per_day >= math.sqrt(dp.dist_params["lambda"])  # widened beyond Poisson


def test_new_pn_zero_history_shrinks_to_peer_mean() -> None:
    proj = EmpiricalBayesProjector(_provider())
    ctx = make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[])
    dp = proj.project(context=ctx, regime=Regime.ULTRA_RARE)
    prior = _provider().get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    assert abs(dp.historical_component - prior.mean) < 1e-9
