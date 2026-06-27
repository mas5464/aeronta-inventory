import httpx
import pytest

pytest.importorskip("fastapi")

from trax_io_spine.contracts import WritebackRequest, WritebackStatus  # noqa: E402
from trax_io_spine.writeback.fake_emro import create_fake_emro  # noqa: E402
from trax_io_spine.writeback.rest import RestWritebackClient  # noqa: E402


def _client(app) -> RestWritebackClient:
    transport = httpx.ASGITransport(app=app)
    return RestWritebackClient(
        base_url="http://emro", client=httpx.AsyncClient(transport=transport)
    )


def _req(**over: object) -> WritebackRequest:
    base: dict[str, object] = dict(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        rop=5, eoq=4, safety_stock=2, max_stock=12, provenance_id="p-1",
        idempotency_key="2026-04-01:acme:PN-A:LOC-1",
    )
    base.update(over)
    return WritebackRequest(**base)  # type: ignore[arg-type]


def test_rest_write_maps_200_to_written() -> None:
    res = _client(create_fake_emro()).write(_req())
    assert res.status is WritebackStatus.WRITTEN
    assert res.new_values == {"rop": 5, "eoq": 4, "safety_stock": 2, "max_stock": 12}


def test_rest_write_maps_409_to_deferred() -> None:
    app = create_fake_emro(open_orders={("acme", "PN-A", "LOC-1")})
    res = _client(app).write(_req())
    assert res.status is WritebackStatus.DEFERRED_OPEN_ORDER
