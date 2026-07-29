"""DynamoDbOnlineStore put/get round-trip + tenant isolation (moto-backed; skips without deps)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("boto3")
pytest.importorskip("moto")

from botocore.exceptions import ClientError  # noqa: E402

from trax_io_feature_store.client import (  # noqa: E402
    FeatureStoreLookupError,
    MissingTenantContextError,
    TenantContext,
)
from trax_io_feature_store.online_store import (  # noqa: E402
    DynamoDbOnlineStore,
    OnlineGeneration,
    PopulationStage,
    _bundle_sort_key,
    _decode_sort_key,
    _sort_key,
)
from trax_io_feature_store.schemas import (  # noqa: E402
    FeatureBundle,
    StockPosition,
    VendorEconomics,
)

ACME = TenantContext(tenant_id="acme")
D1 = date(2026, 4, 1)


def _bundle(tenant="acme", pn="PN-A", location="LOC-1") -> FeatureBundle:
    return FeatureBundle(
        tenant_id=tenant, pn=pn, location=location,
        stock_position=StockPosition(tenant_id=tenant, pn=pn, location=location, on_hand=10,
                                     serviceable=8, unserviceable_in_repair=1,
                                     allocated_reserved=1, rental=0, loan=0, extract_date=D1),
        vendor_economics={
            "DEFAULT": VendorEconomics(tenant_id=tenant, pn=pn, vendor="DEFAULT",
                                       unit_cost=Decimal("4200.5000"), minimum_order_qty=3,
                                       currency="USD", extract_date=D1),
        },
    )


def _commit(
    store: DynamoDbOnlineStore,
    *bundles: FeatureBundle,
    tenant: TenantContext = ACME,
) -> OnlineGeneration:
    stage = store.begin_population(tenant=tenant)
    for bundle in bundles:
        store.put_bundle(bundle, stage=stage)
    return store.commit_population(stage=stage, key_count=len(bundles))


def test_put_get_roundtrip_is_lossless(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    bundle = _bundle()
    generation = _commit(store, bundle)
    got = store.get_bundle(
        tenant=ACME,
        pn="PN-A",
        location="LOC-1",
        generation=generation,
    )
    assert got == bundle  # nested models + Decimal + vendor map all round-trip
    assert got.vendor_economics["DEFAULT"].unit_cost == Decimal("4200.5000")
    assert got.stock_position.serviceable == 8


def test_staged_bundle_key_is_immutable(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    stage = store.begin_population(tenant=ACME)
    original = _bundle()
    store.put_bundle(original, stage=stage)
    changed = original.model_copy(
        update={
            "stock_position": original.stock_position.model_copy(
                update={"serviceable": 99}
            )
        }
    )

    with pytest.raises(ClientError, match="ConditionalCheckFailedException"):
        store.put_bundle(changed, stage=stage)

    generation = store.commit_population(stage=stage, key_count=1)
    assert (
        store.get_bundle(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
            generation=generation,
        ).stock_position.serviceable
        == 8
    )


def test_missing_key_raises_lookup(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=ACME, pn="NOPE", location="LOC-1")


def test_missing_tenant_raises(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    _commit(store, _bundle())
    with pytest.raises(MissingTenantContextError):
        store.get_bundle(tenant=None, pn="PN-A", location="LOC-1")  # type: ignore[arg-type]


def test_cross_tenant_isolation(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    _commit(store, _bundle(tenant="acme"))
    other = TenantContext(tenant_id="other")
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=other, pn="PN-A", location="LOC-1")


def test_put_is_upsert(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    first = _commit(store, _bundle())
    updated = _bundle()
    updated = updated.model_copy(update={
        "stock_position": updated.stock_position.model_copy(update={"serviceable": 99}),
    })
    second = _commit(store, updated)
    got = store.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1")
    assert got.stock_position.serviceable == 99
    old = store.get_bundle(
        tenant=ACME,
        pn="PN-A",
        location="LOC-1",
        generation=first,
    )
    assert old.stock_position.serviceable == 8
    assert second.generation != first.generation


def test_hash_in_keys_does_not_collide(online_table) -> None:
    # eMRO PNs/locations can contain '#'. ("A#B","C") and ("A","B#C") must NOT collide.
    store = DynamoDbOnlineStore(table=online_table)
    _commit(
        store,
        _bundle(pn="A#B", location="C"),
        _bundle(pn="A", location="B#C"),
    )
    one = store.get_bundle(tenant=ACME, pn="A#B", location="C")
    two = store.get_bundle(tenant=ACME, pn="A", location="B#C")
    assert (one.pn, one.location) == ("A#B", "C")  # distinct items, correct reads
    assert (two.pn, two.location) == ("A", "B#C")


def test_online_key_discovery_is_tenant_scoped_query(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    _commit(
        store,
        _bundle(tenant="acme", pn="PN-Z", location="LOC-2"),
        _bundle(tenant="acme", pn="A#B", location="C#D"),
    )
    other = TenantContext(tenant_id="other")
    _commit(
        store,
        _bundle(tenant="other", pn="FOREIGN", location="LOC-X"),
        tenant=other,
    )

    assert store.iter_keys(tenant=ACME) == (
        ("A#B", "C#D"),
        ("PN-Z", "LOC-2"),
    )


def test_online_key_discovery_paginates_query_without_scan() -> None:
    generation = OnlineGeneration(
        tenant_id="acme",
        generation="generation-1",
        key_count=2,
    )

    class QueryOnlyTable:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "Items": [
                        {
                            "pn_location": _bundle_sort_key(
                                generation.generation,
                                "PN-B",
                                "LOC-2",
                            )
                        }
                    ],
                    "LastEvaluatedKey": {
                        "tenant_id": "acme",
                        "pn_location": _bundle_sort_key(
                            generation.generation,
                            "PN-B",
                            "LOC-2",
                        ),
                    },
                }
            return {
                "Items": [
                    {
                        "pn_location": _bundle_sort_key(
                            generation.generation,
                            "PN-A",
                            "LOC-1",
                        )
                    }
                ]
            }

        def scan(self, **_kwargs):
            raise AssertionError("online serving must never Scan")

    table = QueryOnlyTable()
    keys = DynamoDbOnlineStore(table=table).iter_keys(
        tenant=ACME,
        generation=generation,
    )

    assert keys == (("PN-A", "LOC-1"), ("PN-B", "LOC-2"))
    assert len(table.calls) == 2
    assert table.calls[0]["ExpressionAttributeValues"] == {
        ":tenant_id": "acme",
        ":generation_prefix": "_bundle#generation-1#",
    }
    assert "ExclusiveStartKey" not in table.calls[0]
    assert table.calls[1]["ExclusiveStartKey"] == {
        "tenant_id": "acme",
        "pn_location": _bundle_sort_key(
            generation.generation,
            "PN-B",
            "LOC-2",
        ),
    }


@pytest.mark.parametrize("key_count", [True, False, 1.0, "1", Decimal("1")])
def test_external_generation_rejects_non_exact_int_key_count(key_count) -> None:
    generation = OnlineGeneration(
        tenant_id="acme",
        generation="generation-1",
        key_count=key_count,
    )

    class NoReadTable:
        def query(self, **_kwargs):
            raise AssertionError("invalid generation must fail before DynamoDB query")

    with pytest.raises(FeatureStoreLookupError, match="invalid online generation"):
        DynamoDbOnlineStore(table=NoReadTable()).iter_keys(
            tenant=ACME,
            generation=generation,
        )


@pytest.mark.parametrize("key_count", [True, False, "1", Decimal("1.5")])
def test_committed_pointer_rejects_non_integer_key_count(
    online_table,
    key_count,
) -> None:
    online_table.put_item(
        Item={
            "tenant_id": ACME.tenant_id,
            "pn_location": "_meta#population",
            "generation": "generation-1",
            "key_count": key_count,
        }
    )

    with pytest.raises(FeatureStoreLookupError, match="invalid committed online population"):
        DynamoDbOnlineStore(table=online_table).current_generation(tenant=ACME)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "abc",
        "-1#A#B",
        "3#AB#LOC",
        "1#A",
        "1##LOC",
    ],
)
def test_malformed_online_sort_key_fails_closed(value: str) -> None:
    with pytest.raises(ValueError, match="invalid pn_location"):
        _decode_sort_key(value)


def test_corrupt_nested_identity_in_dynamo_fails_closed(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    stage = store.begin_population(tenant=ACME)
    bundle_payload = json.loads(_bundle().model_dump_json())
    bundle_payload["stock_position"]["tenant_id"] = "other"
    online_table.put_item(
        Item={
            "tenant_id": "acme",
            "pn_location": _bundle_sort_key(
                stage.generation,
                "PN-A",
                "LOC-1",
            ),
            "body": json.dumps(bundle_payload),
        }
    )
    generation = store.commit_population(stage=stage, key_count=1)

    with pytest.raises(FeatureStoreLookupError, match="invalid online bundle"):
        store.get_bundle(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
            generation=generation,
        )


def test_no_committed_generation_fails_closed_even_with_legacy_item(online_table) -> None:
    online_table.put_item(
        Item={
            "tenant_id": "acme",
            "pn_location": _sort_key("PN-A", "LOC-1"),
            "body": _bundle().model_dump_json(),
        }
    )
    store = DynamoDbOnlineStore(table=online_table)

    with pytest.raises(FeatureStoreLookupError, match="no committed online population"):
        store.iter_keys(tenant=ACME)
    with pytest.raises(FeatureStoreLookupError, match="no committed online population"):
        store.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1")


def test_concurrent_population_commit_loser_is_invisible(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    first_stage = store.begin_population(tenant=ACME)
    second_stage = store.begin_population(tenant=ACME)
    store.put_bundle(_bundle(pn="WINNER"), stage=first_stage)
    store.put_bundle(_bundle(pn="LOSER"), stage=second_stage)

    winner = store.commit_population(stage=first_stage, key_count=1)
    with pytest.raises(ClientError, match="ConditionalCheckFailedException"):
        store.commit_population(stage=second_stage, key_count=1)

    assert store.current_generation(tenant=ACME) == winner
    assert store.iter_keys(tenant=ACME) == (("WINNER", "LOC-1"),)


@pytest.mark.parametrize(
    "stage",
    [
        PopulationStage(
            tenant_id="acme",
            generation="invalid generation",
            previous_generation=None,
        ),
        PopulationStage(
            tenant_id="acme",
            generation="generation-2",
            previous_generation="invalid generation",
        ),
        PopulationStage(
            tenant_id="",
            generation="generation-2",
            previous_generation=None,
        ),
    ],
)
def test_external_invalid_population_stage_cannot_corrupt_pointer(
    online_table,
    stage,
) -> None:
    store = DynamoDbOnlineStore(table=online_table)

    with pytest.raises(ValueError):
        store.commit_population(stage=stage, key_count=0)

    assert online_table.scan(Select="COUNT")["Count"] == 0
