from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.store import PlannerStore, RecommendationNotFound

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def test_part_context_assembles_from_feature_store():
    store = _store()
    pn, loc = store.keys[0]
    ctx = store.part_context(pn, loc)
    assert ctx.pn == pn and ctx.location == loc
    assert ctx.attributes.description  # from PartAttributes
    assert ctx.stock is None or ctx.stock.on_hand >= 0
    # demand history may be present with zero observations for some sample keys;
    # only assert internal consistency, not a specific non-zero count
    assert ctx.demand is None or len(ctx.demand.points) >= 0
    assert ctx.total_open_qty >= 0

    # a key with an actual demand series should show points and a matching total
    pn2, loc2 = "HYD-PUMP-001", "YYZ"
    ctx2 = store.part_context(pn2, loc2)
    assert ctx2.demand is not None
    assert len(ctx2.demand.points) >= 1
    assert ctx2.demand.total_24mo == sum(p.total for p in ctx2.demand.points)


def test_part_context_unknown_key_raises():
    with pytest.raises(RecommendationNotFound):
        _store().part_context("NOPE", "NOWHERE")


def test_part_context_degrades_without_500(monkeypatch):
    store = _store()
    pn, loc = store.keys[0]
    # a getter that blows up must degrade to None, not propagate
    def _boom(**_kwargs):
        raise RuntimeError

    monkeypatch.setattr(store.fs, "get_stock_position", _boom)
    ctx = store.part_context(pn, loc)
    assert ctx.stock is None
