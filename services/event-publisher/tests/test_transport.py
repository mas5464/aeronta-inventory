import pytest

from trax_io_event_publisher.transport import (
    FakeTransport,
    HttpsMtlsTransport,
    TransportError,
    TransportResponse,
)


def test_fake_transport_records_and_returns_default_202():
    t = FakeTransport()
    resp = t.send(tenant_id="acme-air", body=b"{}")
    assert resp.status_code == 202
    assert t.sent == [("acme-air", b"{}")]


def test_fake_transport_scripts_responses_in_order():
    t = FakeTransport([500, TransportResponse(status_code=202)])
    assert t.send(tenant_id="a", body=b"x").status_code == 500
    assert t.send(tenant_id="a", body=b"x").status_code == 202


def test_fake_transport_can_raise_transport_error():
    t = FakeTransport([TransportError("conn reset")])
    with pytest.raises(TransportError):
        t.send(tenant_id="a", body=b"x")


def test_https_mtls_transport_is_deferred():
    with pytest.raises(NotImplementedError):
        HttpsMtlsTransport().send(tenant_id="a", body=b"x")
