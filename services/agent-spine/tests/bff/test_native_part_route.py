"""Native population -> online bundle -> authenticated part-route smoke.

The feature-store suite separately proves ``GlueIcebergFeatureStore`` is
observationally equivalent to the in-memory client. This test owns the next
boundary: it invokes the real online-population entrypoint, boots Agent Spine
from the agreed native runtime contract, and verifies that only the matching
tenant claim can reach the populated part context.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("botocore")
boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from trax_io_feature_store import TenantContext  # noqa: E402
from trax_io_feature_store.online_store import DynamoDbOnlineStore  # noqa: E402
from trax_io_feature_store.runtime import (  # noqa: E402
    NativeFeatureRuntime,
    populate_native_online_from_env,
)
from trax_io_reco.data.extract_loader import build_stores_from_extract  # noqa: E402

_SAMPLE = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine"
    / "examples"
    / "extract_sample"
)
_TENANT_UUID = "753b64bd-9885-4639-b116-8f2c5c497232"
_TENANT_SLUG = "native-air"
_SECRET = "native-route-unit-test-secret-0123456789"
_KEY = ("HYD-PUMP-001", "YYZ")


class _OfflineWithKeys:
    def __init__(self, delegate, keys):
        self._delegate = delegate
        self._keys = tuple(keys)

    def iter_inference_keys(self, *, tenant):
        assert tenant == TenantContext(tenant_id=_TENANT_UUID)
        return list(self._keys)

    def __getattr__(self, name):
        return getattr(self._delegate, name)


class _CountingOnline:
    def __init__(self, delegate):
        self._delegate = delegate
        self.get_calls: list[tuple[str, str, str]] = []
        self.iter_calls: list[str] = []
        self.get_generations = []
        self.iter_generations = []
        self.current_generation_calls: list[str] = []
        self.puts = 0

    def current_generation(self, *, tenant):
        self.current_generation_calls.append(tenant.tenant_id)
        return self._delegate.current_generation(tenant=tenant)

    def begin_population(self, *, tenant):
        return self._delegate.begin_population(tenant=tenant)

    def put_bundle(self, bundle, *, stage):
        self.puts += 1
        return self._delegate.put_bundle(bundle, stage=stage)

    def commit_population(self, *, stage, key_count):
        return self._delegate.commit_population(stage=stage, key_count=key_count)

    def get_bundle(self, *, tenant, pn, location, generation=None):
        self.get_calls.append((tenant.tenant_id, pn, location))
        self.get_generations.append(generation)
        return self._delegate.get_bundle(
            tenant=tenant,
            pn=pn,
            location=location,
            generation=generation,
        )

    def iter_keys(self, *, tenant, generation=None):
        self.iter_calls.append(tenant.tenant_id)
        self.iter_generations.append(generation)
        return self._delegate.iter_keys(tenant=tenant, generation=generation)


def _token(tenant_id: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "native-planner",
            "aud": "authenticated",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "tenant_id": tenant_id,
            "tenant_role": "planner",
        },
        _SECRET,
        algorithm="HS256",
    )


@moto.mock_aws
def test_populated_native_bundle_reaches_only_its_protected_part_route(monkeypatch):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamodb.create_table(
        TableName="native-features",
        KeySchema=[
            {"AttributeName": "tenant_id", "KeyType": "HASH"},
            {"AttributeName": "pn_location", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "pn_location", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    source, _inventory, _tid, _keys = build_stores_from_extract(
        str(_SAMPLE),
        tenant_id=_TENANT_UUID,
    )
    offline = _OfflineWithKeys(source, [_KEY])
    online = _CountingOnline(DynamoDbOnlineStore(table=table))
    tenant = TenantContext(tenant_id=_TENANT_UUID)
    writer_runtime = NativeFeatureRuntime(
        tenant=tenant,
        offline=offline,
        online=online,
    )
    import trax_io_feature_store.runtime as native_runtime

    monkeypatch.setattr(
        native_runtime,
        "build_native_runtime_from_env",
        lambda tenant_id, **_kwargs: (
            writer_runtime
            if tenant_id == _TENANT_UUID
            else (_ for _ in ()).throw(AssertionError(f"unexpected tenant {tenant_id}"))
        ),
    )
    population = populate_native_online_from_env(
        _TENANT_UUID,
    )
    assert population.keys == (_KEY,)
    assert population.population.written == 1
    assert online.puts == 1

    # Import before enabling native mode: bff.asgi exposes a module-level app
    # for uvicorn, and its default construction must not consume this test's
    # patched native runtime.
    monkeypatch.delenv("TRAX_IO_FEATURE_ONLINE_TABLE", raising=False)
    monkeypatch.setenv("EXTRACT_DIR", str(_SAMPLE))
    from trax_io_feature_store import online_runtime
    from trax_io_feature_store.online_runtime import NativeOnlineRuntime

    from trax_io_spine.bff import asgi

    serving_runtime = NativeOnlineRuntime(
        tenant=tenant,
        online=online,
    )
    built_for: list[str] = []

    def _runtime_for(tenant_id: str):
        built_for.append(tenant_id)
        return serving_runtime

    monkeypatch.setattr(
        online_runtime,
        "build_native_online_runtime_from_env",
        _runtime_for,
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PLANNER_SNAPSHOT_DIR", raising=False)
    monkeypatch.delenv("PLANNER_RECS_FILE", raising=False)
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv("TRAX_IO_FEATURE_ONLINE_TABLE", "native-features")
    monkeypatch.setenv("PLANNER_TENANT", _TENANT_SLUG)
    monkeypatch.setenv("PLANNER_TENANT_UUID", _TENANT_UUID)
    monkeypatch.setenv("AUTH_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PLANNER_NOW", "2026-04-01T00:00:00+00:00")

    app = asgi.build_app()
    client = TestClient(app)
    assert built_for == [_TENANT_UUID]
    assert online.puts == 1  # BFF boot reads committed online state; it never repopulates.
    assert online.current_generation_calls == [_TENANT_UUID]
    assert online.iter_calls == [_TENANT_UUID]
    assert online.get_calls == [(_TENANT_UUID, *_KEY)]
    assert online.iter_generations == online.get_generations
    assert online.iter_generations[0] is not None

    route = f"/v1/tenants/{_TENANT_SLUG}/parts/{_KEY[0]}/{_KEY[1]}"
    # Tenant Query/GetItem happen once during trusted boot. Rejected requests
    # must not drive any additional online access before claims are accepted.
    reads_after_boot = (list(online.iter_calls), list(online.get_calls))
    assert client.get(route).status_code == 401
    assert client.get(
        route,
        headers={
            "Authorization": f"Bearer {_token('99999999-9999-9999-9999-999999999999')}"
        },
    ).status_code == 403
    assert (online.iter_calls, online.get_calls) == reads_after_boot

    response = client.get(
        route,
        headers={"Authorization": f"Bearer {_token(_TENANT_UUID)}"},
    )
    assert response.status_code == 200
    body = response.json()
    source_stock = source.get_stock_position(
        tenant=tenant,
        pn=_KEY[0],
        location=_KEY[1],
    )
    assert body["pn"] == _KEY[0]
    assert body["location"] == _KEY[1]
    assert body["stock"]["on_hand"] == source_stock.on_hand
    assert body["stock"]["serviceable"] == source_stock.serviceable
    assert body["attributes"]["description"] == "HYDRAULIC PUMP"
    assert body["planning_trace"]["calculation_source"] == "served_calculation"
    assert body["candidate_frontier"]["tenant_id"] == _TENANT_UUID
