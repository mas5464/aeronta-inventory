from __future__ import annotations

from tests.fixtures.builders import demand_history
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.regime.classifier import classify, events_24mo_from


def test_thresholds() -> None:
    assert classify(events_24mo=5, history_days=730) == Regime.ULTRA_RARE
    assert classify(events_24mo=6, history_days=730) == Regime.INTERMITTENT
    assert classify(events_24mo=24, history_days=730) == Regime.INTERMITTENT
    assert classify(events_24mo=25, history_days=730) == Regime.MODERATE
    assert classify(events_24mo=200, history_days=730) == Regime.MODERATE
    assert classify(events_24mo=201, history_days=730) == Regime.HIGH_VOLUME


def test_new_pn_is_ultra_rare() -> None:
    assert classify(events_24mo=500, history_days=30) == Regime.ULTRA_RARE


def test_hysteresis_keeps_prior_within_band() -> None:
    # 22 is within 20% of the 25 boundary (20..30); a MODERATE prior is retained.
    assert classify(events_24mo=22, history_days=730, prior=Regime.MODERATE) == Regime.MODERATE
    # Without a prior, 22 classifies as INTERMITTENT.
    assert classify(events_24mo=22, history_days=730) == Regime.INTERMITTENT


def test_hysteresis_releases_outside_band() -> None:
    # 15 is outside the 25 boundary band (20..30); reverts to the raw INTERMITTENT.
    assert classify(events_24mo=15, history_days=730, prior=Regime.MODERATE) == Regime.INTERMITTENT


def test_events_from_history() -> None:
    h = demand_history(tenant_id="t", pn="P", location="L", monthly_units=[1, 2, 0, 3])
    assert events_24mo_from(h) == 6
