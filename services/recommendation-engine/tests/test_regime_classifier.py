from __future__ import annotations

from datetime import date

from trax_io_feature_store.schemas import DemandHistory, DemandObservation

from tests.fixtures.builders import demand_history
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.regime.classifier import (
    classify,
    demanded_units_24mo_from,
    events_24mo_from,
)


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
    # Legacy histories predate explicit counts, so classification conservatively
    # falls back to one event per non-zero bucket (units remain 6).
    assert events_24mo_from(h) == 3


def test_legacy_trailing_window_anchors_to_observed_span_not_processing_date() -> None:
    observations = [
        DemandObservation(
            bucket="month",
            period_start=date(2024, 1, 1),
            issues=7,
        ),
        DemandObservation(
            bucket="month",
            period_start=date(2025, 12, 1),
            issues=2,
        ),
    ]
    early_processing = DemandHistory(
        tenant_id="t",
        pn="P",
        location="L",
        observations=observations,
        extract_date=date(2026, 1, 1),
    )
    late_reprocessing = early_processing.model_copy(
        update={"extract_date": date(2030, 1, 1)}
    )

    assert events_24mo_from(early_processing) == 2
    assert events_24mo_from(late_reprocessing) == 2
    assert demanded_units_24mo_from(early_processing) == 9
    assert demanded_units_24mo_from(late_reprocessing) == 9


def test_events_24mo_excludes_events_from_first_year_of_36_month_window() -> None:
    history = DemandHistory(
        tenant_id="t",
        pn="P",
        location="L",
        observation_start=date(2023, 4, 16),
        observation_end=date(2026, 4, 16),
        bucket="month",
        event_count_source="observed",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2023, 5, 1),
                issues=50,
                removal_events=0,
                issue_events=50,
            ),
            DemandObservation(
                bucket="month",
                period_start=date(2024, 5, 1),
                issues=7,
                removal_events=0,
                issue_events=1,
            ),
        ],
        extract_date=date(2026, 4, 16),
    )

    assert events_24mo_from(history) == 1
    assert demanded_units_24mo_from(history) == 7
