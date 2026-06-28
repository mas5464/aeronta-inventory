from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import WritebackRequest, WritebackStatus
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(key="k1", *, rop=5, **over):
    base = dict(
        tenant_id="acme", pn="P1", location="YYZ", rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=key, tier=AutonomyTier.BOUNDED,
    )
    base.update(over)
    return WritebackRequest(**base)


def test_first_write_logs_version_1_with_no_parent():
    t = InMemoryWritebackTarget()
    t.write(_req("k1"))
    hist = t.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert len(hist) == 1
    e = hist[0]
    assert e.version == 1 and e.parent_version is None
    assert e.status is WritebackStatus.WRITTEN
    assert e.old_values is None
    assert e.new_values == {"rop": 5, "eoq": 10, "safety_stock": 2, "max_stock": 20}
    assert e.tier is AutonomyTier.BOUNDED and e.provenance_id == "prov-1"
    assert e.idempotency_key == "k1" and e.changed_by_principal == "agent-spine"


def test_second_write_chains_parent_version_and_old_values():
    t = InMemoryWritebackTarget()
    t.write(_req("k1", rop=5))
    t.write(_req("k2", rop=7))
    hist = t.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert [e.version for e in hist] == [1, 2]
    assert hist[1].parent_version == 1
    assert hist[1].old_values == {"rop": 5, "eoq": 10, "safety_stock": 2, "max_stock": 20}
    assert hist[1].new_values["rop"] == 7


def test_idempotent_rewrite_does_not_double_log():
    t = InMemoryWritebackTarget()
    t.write(_req("k1"))
    t.write(_req("k1"))  # same idempotency_key -> cached, no new history
    assert len(t.get_history(tenant_id="acme", pn="P1", location="YYZ")) == 1


def test_get_history_empty_for_unknown_key():
    t = InMemoryWritebackTarget()
    assert t.get_history(tenant_id="acme", pn="ZZZ", location="YYZ") == ()
