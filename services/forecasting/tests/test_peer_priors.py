# tests/test_peer_priors.py
from trax_io_forecasting.peer_priors import PeerPriorProvider, PeerRecord


def _rec(ata, tier, cls, count, exp=730.0):
    return PeerRecord(ata_chapter=ata, canonical_tier=tier, part_class=cls,
                      count=count, exposure=exp)


def test_get_prior_uses_finest_group_when_enough_peers():
    recs = [_rec("32", 1, "rotable", c) for c in (0, 1, 2, 0, 3)]  # 5 peers at L0
    p = PeerPriorProvider.fit(recs, min_peers=5)
    prior = p.get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    # group mean rate = mean(count)/730
    assert abs(prior.mean - (sum([0, 1, 2, 0, 3]) / 5 / 730.0)) < 1e-9

def test_get_prior_backs_off_when_group_too_small():
    # only 2 peers at L0 (32,1,rotable) but 6 share tier 1 -> back off to L2 (tier)
    recs = [_rec("32", 1, "rotable", 1), _rec("32", 1, "rotable", 2)]
    recs += [_rec("79", 1, "repairable", c) for c in (0, 1, 0, 2)]
    p = PeerPriorProvider.fit(recs, min_peers=5)
    prior = p.get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    tier_counts = [1, 2, 0, 1, 0, 2]
    assert abs(prior.mean - (sum(tier_counts) / len(tier_counts) / 730.0)) < 1e-9

def test_get_prior_falls_back_to_global_then_floor():
    recs = [_rec("32", 1, "rotable", 1), _rec("79", 2, "repairable", 3)]
    p = PeerPriorProvider.fit(recs, min_peers=5)
    # no group meets min_peers -> global prior over all records
    prior = p.get_prior(ata_chapter="11", canonical_tier=4, part_class="expendable")
    assert prior.alpha > 0.0 and prior.beta > 0.0

def test_fit_empty_returns_floor_global():
    p = PeerPriorProvider.fit([], min_peers=5)
    prior = p.get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    assert prior.alpha > 0.0 and prior.beta > 0.0


def test_fit_tolerates_zero_exposure_records():
    # A new-PN record has exposure 0.0; fit must not divide by zero and must
    # build the prior from the positive-exposure peers only.
    recs = [PeerRecord("32", 1, "rotable", 0.0, 0.0)]  # new PN, no history
    recs += [_rec("32", 1, "rotable", c) for c in (0, 1, 2, 0, 3)]  # 5 real peers @730d
    p = PeerPriorProvider.fit(recs, min_peers=5)
    prior = p.get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    assert abs(prior.mean - (sum([0, 1, 2, 0, 3]) / 5 / 730.0)) < 1e-9


def test_fit_all_zero_exposure_group_falls_back_to_floor():
    recs = [PeerRecord("32", 1, "rotable", 0.0, 0.0) for _ in range(5)]
    p = PeerPriorProvider.fit(recs, min_peers=5)
    prior = p.get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    assert prior.alpha > 0.0 and prior.beta > 0.0  # floor prior, no crash
