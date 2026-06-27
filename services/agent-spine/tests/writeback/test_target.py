from trax_io_spine.contracts import WritebackRequest, WritebackStatus
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(**over: object) -> WritebackRequest:
    base: dict[str, object] = dict(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        rop=5, eoq=4, safety_stock=2, max_stock=12, provenance_id="p-1",
        idempotency_key="2026-04-01:acme:PN-A:LOC-1",
    )
    base.update(over)
    return WritebackRequest(**base)  # type: ignore[arg-type]


def test_write_persists_and_returns_new_values() -> None:
    t = InMemoryWritebackTarget()
    res = t.write(_req())
    assert res.status is WritebackStatus.WRITTEN
    assert res.new_values == {"rop": 5, "eoq": 4, "safety_stock": 2, "max_stock": 12}


def test_idempotent_replay_returns_same_result_once() -> None:
    t = InMemoryWritebackTarget()
    first = t.write(_req())
    second = t.write(_req(rop=999))  # same idempotency_key -> ignored
    assert second == first
    assert len(t.history) == 1


def test_open_order_defers() -> None:
    t = InMemoryWritebackTarget(open_orders={("acme", "PN-A", "LOC-1")})
    res = t.write(_req())
    assert res.status is WritebackStatus.DEFERRED_OPEN_ORDER
    assert t.history == []
