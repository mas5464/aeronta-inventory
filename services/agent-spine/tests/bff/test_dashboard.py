from datetime import UTC, datetime
from pathlib import Path

from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def test_dashboard_aggregates_portfolio():
    store = _store()
    d = store.dashboard()
    assert d.parts == len(store.keys)  # portfolio-wide (all keys), not just recommendations
    assert d.total_on_hand >= 0
    assert d.total_on_hand_value >= 0
    assert d.total_shortage >= 0
    assert d.total_projected_demand >= 0
    assert d.aog_exposure >= 0
    assert d.open_recommendations >= 0
    assert isinstance(d.by_criticality, tuple)
    assert isinstance(d.by_ata, tuple)
    assert isinstance(d.by_part_class, tuple)
    assert isinstance(d.by_tier, tuple)
    # top_shortages sorted desc by shortage
    shorts = [s.shortage for s in d.top_shortages]
    assert shorts == sorted(shorts, reverse=True)
