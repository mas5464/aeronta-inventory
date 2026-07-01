from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.models import RejectReason, TaskStatus
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
