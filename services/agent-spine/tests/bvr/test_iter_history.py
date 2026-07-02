"""iter_history: tenant-wide ledger enumeration for the BVR (spec §2 inputs)."""

from __future__ import annotations

from trax_io_spine.contracts import WritebackRequest
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(tenant: str, pn: str, loc: str, rop: int) -> WritebackRequest:
    return WritebackRequest(
        tenant_id=tenant, pn=pn, location=loc, rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id=f"prov-{pn}-{loc}-{rop}",
        idempotency_key=f"idem-{pn}-{loc}-{rop}", tier=None,
    )


def test_iter_history_is_tenant_scoped_and_sorted():
    t = InMemoryWritebackTarget()
    t.write(_req("acme", "PN2", "YYZ", 5))
    t.write(_req("acme", "PN1", "YUL", 3))
    t.write(_req("acme", "PN1", "YUL", 4))  # second version for the same key
    t.write(_req("globex", "PN9", "LHR", 7))

    entries = t.iter_history("acme")
    assert [(e.pn, e.location, e.version) for e in entries] == [
        ("PN1", "YUL", 1), ("PN1", "YUL", 2), ("PN2", "YYZ", 1),
    ]
    assert all(e.tenant_id == "acme" for e in entries)


def test_iter_history_empty_tenant_is_empty_tuple():
    assert InMemoryWritebackTarget().iter_history("acme") == ()
