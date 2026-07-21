"""Queue-read parity: PgPlannerStore vs the in-memory store over the SAME seed."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.models import QueueSortKey, TaskStatus
from trax_io_spine.bff.store import PlannerStore, RecommendationNotFound
from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture(scope="module")
def mem_store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )


@pytest.fixture(scope="module")
def pg_store(admin_pool, pg_pool, mem_store):
    report = seed_store(admin_pool, store=mem_store, slug="acme-q9", name="Acme Air")
    return PgPlannerStore(pg_pool, tenant_slug="acme-q9", tenant_uuid=report.tenant_uuid)


def _ids(rows):
    return [r.recommendation_id for r in rows]


def test_default_queue_parity(mem_store, pg_store):
    assert _ids(pg_store.queue()) == _ids(mem_store.queue())


def test_paging_and_total_parity(mem_store, pg_store):
    m_rows, m_total = mem_store.list_queue_page(limit=2, offset=1)
    p_rows, p_total = pg_store.list_queue_page(limit=2, offset=1)
    assert (_ids(p_rows), p_total) == (_ids(m_rows), m_total)


@pytest.mark.parametrize("sort_by", list(QueueSortKey))
@pytest.mark.parametrize("sort_dir", ["asc", "desc"])
def test_sort_parity_all_keys(mem_store, pg_store, sort_by, sort_dir):
    m = mem_store.list_queue_all(sort_by=sort_by, sort_dir=sort_dir)
    p = pg_store.list_queue_all(sort_by=sort_by, sort_dir=sort_dir)
    assert _ids(p) == _ids(m)


def test_status_filter_parity(mem_store, pg_store):
    for status in (TaskStatus.PENDING, TaskStatus.APPROVED, TaskStatus.REJECTED):
        assert _ids(pg_store.list_queue_all(status=status)) == _ids(
            mem_store.list_queue_all(status=status)
        )


def test_detail_parity(mem_store, pg_store):
    rid = mem_store.queue()[0].recommendation_id
    assert pg_store.detail(rid) == mem_store.detail(rid)


def test_detail_unknown_raises(pg_store):
    with pytest.raises(RecommendationNotFound):
        pg_store.detail("nope")
