"""Endpoint conformance: every valid kind is accepted; malformed bodies never 500.

These run under the `http` extra (no schemathesis dependency — see pyproject note).
"""

import pytest
from fastapi.testclient import TestClient

from trax_io_event_publisher.endpoint import create_app
from trax_io_event_publisher.samples import make_event
from trax_io_event_publisher.schemas import EventKind


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


@pytest.mark.parametrize(
    "bad", [b"", b"{}", b'{"kind":"stock_moved"}', b"not json"]
)
def test_malformed_body_never_500s(bad):
    client = TestClient(create_app())
    r = client.post(
        "/v1/tenants/acme-air/events", content=bad,
        headers={"content-type": "application/json"},
    )
    assert r.status_code in (400, 422)
