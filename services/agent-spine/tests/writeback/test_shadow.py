from trax_io_spine.contracts import WritebackRequest, WritebackStatus
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(key, *, rop, shadow=False):
    return WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=key, shadow=shadow,
    )


def test_shadow_write_returns_shadowed_and_does_not_apply():
    t = InMemoryWritebackTarget()
    t.write(_req("k1", rop=5))                       # applied
    res = t.write(_req("k2", rop=99, shadow=True))   # shadow
    assert res.status is WritebackStatus.SHADOWED
    assert res.old_values == {"rop": 5, "eoq": 10, "safety_stock": 2, "max_stock": 20}
    assert res.new_values["rop"] == 99
    # a subsequent applied read shows the OLD value: shadow did not mutate
    after = t.write(_req("k3", rop=7))
    assert after.old_values["rop"] == 5  # not 99


def test_shadow_logs_a_shadowed_history_entry_not_in_dot_history():
    t = InMemoryWritebackTarget()
    res = t.write(_req("k1", rop=99, shadow=True))
    hist = t.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert len(hist) == 1 and hist[0].status is WritebackStatus.SHADOWED
    assert t.history == []  # success-only applied list is untouched
    assert res.status is WritebackStatus.SHADOWED
