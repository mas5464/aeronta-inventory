from datetime import UTC, datetime
from pathlib import Path

import pytest
from trax_io_reco.contracts.enums import AogRiskLevel

from trax_io_spine.bff.models import QueueSortKey, RejectReason, TaskStatus
from trax_io_spine.bff.store import PlannerStore, RecommendationNotFound

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def test_queue_returns_pending_rows_priority_desc():
    rows = _store().queue()
    assert len(rows) >= 1
    assert all(r.status is TaskStatus.PENDING for r in rows)
    scores = [r.priority_score for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_detail_returns_full_provenance():
    store = _store()
    rec_id = store.queue()[0].recommendation_id
    d = store.detail(rec_id)
    assert d.recommendation_id == rec_id
    assert d.projected_demand >= 0.0
    # a queued rec either has a proposed policy (approvable) or not (non_policy)
    assert d.proposed_policy is None or d.proposed_policy.rop >= 0


def test_detail_unknown_id_raises():
    with pytest.raises(RecommendationNotFound):
        _store().detail("nope")


def test_limit_caps_rows():
    assert len(_store().queue(limit=1)) <= 1


def test_queue_row_carries_part_fields():
    rows = _store().queue()
    assert rows, "sample extract should produce recommendations"
    r = rows[0]
    assert isinstance(r.description, str) and r.description
    assert isinstance(r.current_stock, int)
    assert r.shortage_quantity >= 0.0
    assert r.horizon_days > 0


def test_detail_carries_part_fields():
    store = _store()
    rec_id = store.queue()[0].recommendation_id
    d = store.detail(rec_id)
    assert d.description and isinstance(d.current_stock, int)
    assert d.horizon_days > 0


def test_list_queue_page_slices_and_reports_total():
    store = _store()
    full = store.queue(limit=1000)

    page, total = store.list_queue_page(limit=2, offset=0)
    assert total == len(full)
    assert [r.recommendation_id for r in page] == [r.recommendation_id for r in full[:2]]

    page2, total2 = store.list_queue_page(limit=2, offset=2)
    assert total2 == total
    assert [r.recommendation_id for r in page2] == [r.recommendation_id for r in full[2:4]]


def test_list_queue_page_sort_is_stable_priority_desc_tie_break_by_id():
    store = _store()
    page, _ = store.list_queue_page(limit=1000, offset=0)
    scores = [r.priority_score for r in page]
    assert scores == sorted(scores, reverse=True)
    # within any equal-priority run, recommendation_id must be ascending (deterministic tie-break)
    i = 0
    while i < len(page):
        j = i
        while j < len(page) and page[j].priority_score == page[i].priority_score:
            j += 1
        ids = [r.recommendation_id for r in page[i:j]]
        assert ids == sorted(ids)
        i = j


def test_list_queue_page_respects_status_filter():
    store = _store()
    rid = store.queue()[0].recommendation_id
    store.reject(rid, RejectReason.OTHER)
    page, total = store.list_queue_page(status=TaskStatus.REJECTED, limit=10, offset=0)
    assert total >= 1
    assert any(r.recommendation_id == rid for r in page)


# --------------------------------------------------------------------------- #
# Task F2 — server-side sort + filter on the queue endpoint
# --------------------------------------------------------------------------- #
def _sort_field(sort_by: QueueSortKey, row):
    return {
        QueueSortKey.PRIORITY: row.priority_score,
        QueueSortKey.COST_IMPACT: float(row.estimated_cost_impact),
        QueueSortKey.CONFIDENCE: row.confidence_score,
        QueueSortKey.CRITICALITY: row.criticality_tier,
    }[sort_by]


def _assert_full_ordering(rows, sort_by: QueueSortKey, sort_dir: str) -> None:
    """Full ordering check: the requested sort key (asc/desc), tie-broken by
    recommendation_id ASC — mirrors the two-pass stable sort in `_sorted_entries`."""
    values = [_sort_field(sort_by, r) for r in rows]
    assert values == sorted(values, reverse=(sort_dir == "desc"))
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and _sort_field(sort_by, rows[j]) == _sort_field(sort_by, rows[i]):
            j += 1
        ids = [r.recommendation_id for r in rows[i:j]]
        assert ids == sorted(ids)
        i = j


@pytest.mark.parametrize("sort_by", list(QueueSortKey))
@pytest.mark.parametrize("sort_dir", ["asc", "desc"])
def test_list_queue_page_sort_by_every_key_asc_and_desc(
    sort_by,
    sort_dir,
    seed_pending_recommendations,
):
    store = _store()
    seed_pending_recommendations(store)
    page, total = store.list_queue_page(limit=1000, offset=0, sort_by=sort_by, sort_dir=sort_dir)
    assert total == len(page)
    assert len(page) >= 2, "sample data must carry enough diversity to assert an ordering"
    _assert_full_ordering(page, sort_by, sort_dir)


def test_list_queue_page_filter_by_tier_in_isolation():
    store = _store()
    full, _ = store.list_queue_page(limit=1000, offset=0)
    tier = full[0].tier
    page, total = store.list_queue_page(limit=1000, offset=0, tier=tier)
    assert total == len(page)
    assert page, "expected at least one row for the sample tier"
    assert all(r.tier == tier for r in page)
    assert len(page) == sum(1 for r in full if r.tier == tier)


def test_list_queue_page_filter_by_type_in_isolation():
    store = _store()
    full, _ = store.list_queue_page(limit=1000, offset=0)
    type_ = full[0].type
    page, total = store.list_queue_page(limit=1000, offset=0, type_=type_)
    assert total == len(page)
    assert page, "expected at least one row for the sample type"
    assert all(r.type == type_ for r in page)
    assert len(page) == sum(1 for r in full if r.type == type_)


def test_list_queue_page_filter_by_aog_min_in_isolation(
    seed_pending_recommendations,
):
    store = _store()
    seed_pending_recommendations(store)
    full, _ = store.list_queue_page(limit=1000, offset=0)
    aog_min = AogRiskLevel.HIGH
    assert any(r.aog_risk_level >= aog_min for r in full), "sample must have a HIGH+ row"
    assert any(r.aog_risk_level < aog_min for r in full), "sample must have a sub-HIGH row too"
    page, total = store.list_queue_page(limit=1000, offset=0, aog_min=aog_min)
    assert total == len(page)
    assert all(r.aog_risk_level >= aog_min for r in page)
    assert len(page) == sum(1 for r in full if r.aog_risk_level >= aog_min)


def test_list_queue_page_combined_tier_type_aog_min_and_sort():
    store = _store()
    full, _ = store.list_queue_page(limit=1000, offset=0)
    target = full[0]
    page, total = store.list_queue_page(
        limit=1000,
        offset=0,
        tier=target.tier,
        type_=target.type,
        aog_min=AogRiskLevel.NONE,
        sort_by=QueueSortKey.COST_IMPACT,
        sort_dir="asc",
    )
    assert total == len(page)
    assert page
    assert all(r.tier == target.tier for r in page)
    assert all(r.type == target.type for r in page)
    assert all(r.aog_risk_level >= AogRiskLevel.NONE for r in page)
    _assert_full_ordering(page, QueueSortKey.COST_IMPACT, "asc")


def test_list_queue_page_zero_new_kwargs_matches_pre_f2_ordering():
    """Back-compat: calling `list_queue_page()` with none of the new F2 kwargs must
    reproduce the exact pre-F2 ordering — priority_score DESC, recommendation_id ASC
    tie-break — byte-for-byte."""
    store = _store()
    page, total = store.list_queue_page()
    full = store.queue(limit=1000)
    assert total == len(full)
    assert [r.recommendation_id for r in page] == [r.recommendation_id for r in full]
    scores = [r.priority_score for r in page]
    assert scores == sorted(scores, reverse=True)


def test_list_queue_page_pagination_deterministic_under_non_default_sort(
    seed_pending_recommendations,
):
    """No dup/skip across two pages when paging under a non-default sort+dir."""
    store = _store()
    seed_pending_recommendations(store)
    full, total = store.list_queue_page(
        limit=1000, offset=0, sort_by=QueueSortKey.CONFIDENCE, sort_dir="asc"
    )
    assert total == len(full)
    assert len(full) >= 3, "need at least 3 rows to meaningfully split across two pages"

    half = len(full) // 2 or 1
    page1, t1 = store.list_queue_page(
        limit=half, offset=0, sort_by=QueueSortKey.CONFIDENCE, sort_dir="asc"
    )
    page2, t2 = store.list_queue_page(
        limit=len(full) - half,
        offset=half,
        sort_by=QueueSortKey.CONFIDENCE,
        sort_dir="asc",
    )
    assert t1 == t2 == total
    stitched_ids = [r.recommendation_id for r in page1 + page2]
    full_ids = [r.recommendation_id for r in full]
    assert stitched_ids == full_ids
    assert len(set(stitched_ids)) == len(stitched_ids), "no duplicate rows across pages"


# --------------------------------------------------------------------------- #
# Task 1 — `list_queue_all` for unpaginated CSV export
# --------------------------------------------------------------------------- #
def test_list_queue_all_returns_every_pending_row_no_pagination():
    store = _store()
    all_rows = store.list_queue_all()
    # Same total the paged query reports, but every row in one list (no slicing).
    _, total = store.list_queue_page(limit=1, offset=0)
    assert len(all_rows) == total
    assert all(r.status is TaskStatus.PENDING for r in all_rows)


def test_list_queue_all_matches_paged_order_and_content():
    store = _store()
    all_rows = store.list_queue_all(sort_by=QueueSortKey.COST_IMPACT, sort_dir="asc")
    # A page larger than the whole set == the whole set, in identical order.
    page, total = store.list_queue_page(
        limit=100_000, offset=0, sort_by=QueueSortKey.COST_IMPACT, sort_dir="asc"
    )
    assert [r.recommendation_id for r in all_rows] == [r.recommendation_id for r in page]
    assert len(all_rows) == total


def test_list_queue_all_applies_tier_and_status_filters():
    store = _store()
    approved = store.list_queue_all(status=TaskStatus.APPROVED)
    assert all(r.status is TaskStatus.APPROVED for r in approved)
    tier1 = store.list_queue_all(tier=1)
    assert all(r.tier == 1 for r in tier1)
