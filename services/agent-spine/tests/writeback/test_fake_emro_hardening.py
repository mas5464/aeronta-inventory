from datetime import UTC, datetime

import httpx

from trax_io_spine.contracts import (
    RollbackRequest,
    RollbackStatus,
    WritebackRequest,
    WritebackStatus,
)
from trax_io_spine.writeback.fake_emro import create_fake_emro
from trax_io_spine.writeback.rest import RestWritebackClient


def _client(app):
    return RestWritebackClient(client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                        base_url="http://emro.test"))


def _req(key, *, rop, shadow=False):
    return WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=key, shadow=shadow,
    )


def test_applied_write_then_history_round_trips():
    c = _client(create_fake_emro())
    assert c.write(_req("k1", rop=5)).status is WritebackStatus.WRITTEN
    assert c.write(_req("k2", rop=7)).status is WritebackStatus.WRITTEN
    hist = c.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert [e.version for e in hist] == [1, 2]
    assert hist[1].old_values["rop"] == 5 and hist[1].new_values["rop"] == 7


def test_shadow_write_over_the_wire_is_shadowed_and_not_applied():
    c = _client(create_fake_emro())
    assert c.write(_req("k1", rop=5)).status is WritebackStatus.WRITTEN
    res = c.write(_req("k2", rop=99, shadow=True))
    assert res.status is WritebackStatus.SHADOWED
    assert c.write(_req("k3", rop=7)).old_values["rop"] == 5  # shadow didn't apply


def test_rollback_over_the_wire():
    app = create_fake_emro()
    c = _client(app)
    c.write(_req("k1", rop=5))
    c.write(_req("k2", rop=7))
    res = c.rollback(RollbackRequest(
        tenant_id="acme", pn="P1", location="YYZ", reason="bad",
        requested_at=datetime.now(UTC),
    ))
    assert res.status is RollbackStatus.ROLLED_BACK
    assert res.to_values["rop"] == 5
