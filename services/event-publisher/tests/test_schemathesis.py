import pytest

schemathesis = pytest.importorskip("schemathesis")

from fastapi.testclient import TestClient  # noqa: E402

from trax_io_event_publisher.endpoint import create_app  # noqa: E402
from trax_io_event_publisher.samples import make_event  # noqa: E402
from trax_io_event_publisher.schemas import EventKind  # noqa: E402


@pytest.mark.parametrize("kind", [k.value for k in EventKind])
def test_endpoint_accepts_every_valid_kind(kind):
    client = TestClient(create_app())
    env = make_event(kind)
    r = client.post(
        f"/v1/tenants/{env.tenant_id}/events",
        content=env.model_dump_json(),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 202


def test_malformed_body_never_500s():
    client = TestClient(create_app())
    for bad in [b"", b"{}", b'{"kind":"stock_moved"}', b"not json"]:
        r = client.post(
            "/v1/tenants/acme-air/events", content=bad,
            headers={"content-type": "application/json"},
        )
        assert r.status_code in (400, 422)
