from fastapi.testclient import TestClient

from trax_io_event_publisher.endpoint import create_app
from trax_io_event_publisher.publisher import EventPublisher, PublishStatus
from trax_io_event_publisher.samples import make_event
from trax_io_event_publisher.transport import AsgiTransport


def _client(**kw):
    app = create_app(**kw)
    return app, TestClient(app)


def _post(client, env):
    return client.post(
        f"/v1/tenants/{env.tenant_id}/events",
        content=env.model_dump_json(),
        headers={"content-type": "application/json"},
    )


def test_happy_path_returns_202_and_stores():
    app, client = _client()
    env = make_event("stock_moved")
    assert _post(client, env).status_code == 202
    assert (env.tenant_id, env.event_id) in app.state.accepted


def test_bad_schema_returns_400():
    _, client = _client()
    r = client.post(
        "/v1/tenants/acme-air/events", content=b'{"not":"valid"}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400


def test_tenant_mismatch_returns_403():
    _, client = _client()
    env = make_event("stock_moved", tenant_id="acme-air")
    r = client.post(
        "/v1/tenants/other-air/events", content=env.model_dump_json(),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 403


def test_duplicate_event_id_returns_409():
    _, client = _client()
    env = make_event("stock_moved")
    assert _post(client, env).status_code == 202
    assert _post(client, env).status_code == 409


def test_rate_limiter_returns_429_with_retry_after():
    _, client = _client(rate_limiter=lambda tenant, env: True)
    r = _post(client, make_event("stock_moved"))
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


def test_replay_returns_stored_events():
    _, client = _client()
    env = make_event("stock_moved")
    _post(client, env)
    r = client.post(f"/v1/tenants/{env.tenant_id}/events/replay")
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_asgi_transport_round_trip_with_publisher():
    app = create_app()
    pub = EventPublisher(AsgiTransport(app))
    env = make_event("stock_moved")
    assert pub.publish(env).status is PublishStatus.EMITTED
    # re-publish -> endpoint 409 -> still idempotent success
    assert pub.publish(env).status is PublishStatus.EMITTED
