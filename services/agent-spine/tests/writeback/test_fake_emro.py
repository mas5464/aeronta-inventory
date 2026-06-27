import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from trax_io_spine.writeback.fake_emro import create_fake_emro  # noqa: E402

_BODY = {
    "tenant_id": "acme", "pn": "PN-A", "location": "LOC-1",
    "rop": 5, "eoq": 4, "safety_stock": 2, "max_stock": 12,
    "provenance_id": "p-1", "idempotency_key": "2026-04-01:acme:PN-A:LOC-1",
}


def test_post_writes_and_records_history() -> None:
    client = TestClient(create_fake_emro())
    resp = client.post("/inventory-levels", json=_BODY)
    assert resp.status_code == 200
    assert resp.json()["status"] == "written"
    assert client.get("/history").json() == [{"tenant_id": "acme", "pn": "PN-A",
                                              "location": "LOC-1",
                                              "values": {"rop": 5, "eoq": 4,
                                                         "safety_stock": 2, "max_stock": 12}}]


def test_open_order_returns_409() -> None:
    client = TestClient(create_fake_emro(open_orders={("acme", "PN-A", "LOC-1")}))
    resp = client.post("/inventory-levels", json=_BODY)
    assert resp.status_code == 409


def test_idempotent_replay() -> None:
    client = TestClient(create_fake_emro())
    client.post("/inventory-levels", json=_BODY)
    resp = client.post("/inventory-levels", json={**_BODY, "rop": 999})
    assert resp.json()["new_values"]["rop"] == 5  # original write, replay ignored
    assert len(client.get("/history").json()) == 1
