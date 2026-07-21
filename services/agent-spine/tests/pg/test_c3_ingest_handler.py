"""Ingest handler end-to-end: sample canonical CSVs → validate → engine → seed."""
import pytest

from tests.pg.conftest import as_tenant  # noqa: F401
from trax_io_spine.pg.ingest import run_ingest

T = "eeeeeeee-4444-4444-4444-eeeeeeee0c34"
PARTS = b"part_number,part_class,unit_cost,criticality\nP1,rotable,100,AOG\n"
STOCK = (b"part_number,location_code,on_hand,current_rop,current_eoq,"
         b"current_safety_stock,current_max\nP1,MIA,5,3,10,2,20\n")
DEMAND = b"part_number,location_code,period,quantity\nP1,MIA,2026-01-01,3\n"


class FakeStorage:
    def __init__(self, blobs):
        self._blobs = blobs

    def download(self, path):
        return self._blobs[path]


@pytest.fixture()
def tenant(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name, key_quota) values (%s, 'acme-c3t4', 'A', 5000) "
            "on conflict (id) do nothing",
            (T,),
        )
        conn.commit()
    return T


def _payload():
    return {
        "tenant_id": T, "tenant_slug": "acme-c3t4", "batch_id": "b1",
        "files": {"parts": "acme-c3t4/b1/parts.csv", "stock": "acme-c3t4/b1/stock.csv",
                  "demand_history": "acme-c3t4/b1/demand.csv"},
        "uploaded_by": "u1",
    }


def test_clean_ingest_seeds(tenant, admin_pool, pg_pool):
    storage = FakeStorage({
        "acme-c3t4/b1/parts.csv": PARTS,
        "acme-c3t4/b1/stock.csv": STOCK,
        "acme-c3t4/b1/demand.csv": DEMAND,
    })
    with admin_pool.connection() as conn:
        out = run_ingest(conn, pg_pool, _payload(), storage=storage, tenant_name="A")
    assert out["status"] == "done"
    assert out["result"]["keys"] >= 1
    # recommendations landed for the tenant
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid", (T,)
        ).fetchone()[0]
    assert n == out["result"]["recommendations"]


def test_dirty_ingest_fails_without_seeding(tenant, admin_pool, pg_pool):
    bad_stock = b"part_number,location_code,on_hand\nP1,MIA,lots\n"
    storage = FakeStorage({
        "acme-c3t4/b1/parts.csv": PARTS,
        "acme-c3t4/b1/stock.csv": bad_stock,
        "acme-c3t4/b1/demand.csv": DEMAND,
    })
    with admin_pool.connection() as conn:
        # ensure no prior rows
        conn.execute("delete from recommendations where tenant_id = %s::uuid", (T,))
        conn.commit()
        out = run_ingest(conn, pg_pool, _payload(), storage=storage, tenant_name="A")
    assert out["status"] == "failed" and out["errors"]
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid", (T,)
        ).fetchone()[0]
    assert n == 0
