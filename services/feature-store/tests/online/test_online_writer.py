"""populate_online: materialize + upsert bundles, skipping incomplete keys (moto-backed)."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("boto3")
pytest.importorskip("moto")

from trax_io_feature_store.client import (  # noqa: E402
    FeatureStoreLookupError,
    InMemoryFeatureStore,
    TenantContext,
)
from trax_io_feature_store.online_store import DynamoDbOnlineStore  # noqa: E402
from trax_io_feature_store.online_writer import populate_online  # noqa: E402
from trax_io_feature_store.schemas import CurrentPolicy, StockPosition  # noqa: E402

ACME = TenantContext(tenant_id="acme")
D1 = date(2026, 4, 1)


def _offline_with_one_complete_one_stockless() -> InMemoryFeatureStore:
    fs = InMemoryFeatureStore()
    # (PN-A, LOC-1): has stock -> complete -> written
    fs.seed("acme", "stock_position", ("PN-A", "LOC-1"), StockPosition(
        tenant_id="acme", pn="PN-A", location="LOC-1", on_hand=10, serviceable=8,
        unserviceable_in_repair=1, allocated_reserved=1, rental=0, loan=0, extract_date=D1))
    # (PN-B, LOC-1): policy but NO stock -> incomplete -> skipped (not written as null-stock)
    fs.seed("acme", "current_policy", ("PN-B", "LOC-1"), CurrentPolicy(
        tenant_id="acme", pn="PN-B", location="LOC-1", rop=5, eoq=4, safety_stock=2, max_stock=40,
        replenishment_lead_days=21.0, extract_date=D1))
    return fs


def test_populate_writes_complete_skips_incomplete(online_table) -> None:
    offline = _offline_with_one_complete_one_stockless()
    online = DynamoDbOnlineStore(table=online_table)
    keys = [("PN-A", "LOC-1"), ("PN-B", "LOC-1")]

    result = populate_online(offline, online, tenant=ACME, keys=keys)

    assert result.written == 1
    assert result.skipped_incomplete == 1
    assert result.failed_oversize == 0
    assert result.failed_writes == 0
    assert result.committed_generation is not None
    assert result.total == 2
    # complete key is online...
    got = online.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1")
    assert got.stock_position.serviceable == 8
    # ...and the stockless key was NOT written (no misleading null-stock bundle)
    with pytest.raises(FeatureStoreLookupError):
        online.get_bundle(tenant=ACME, pn="PN-B", location="LOC-1")


def test_populate_empty_keys_is_noop(online_table) -> None:
    online = DynamoDbOnlineStore(table=online_table)
    result = populate_online(InMemoryFeatureStore(), online, tenant=ACME, keys=[])
    assert result.written == 0
    assert result.committed_generation is not None
    assert online.iter_keys(tenant=ACME) == ()


def test_new_generation_removes_incomplete_and_removed_prior_keys(online_table) -> None:
    online = DynamoDbOnlineStore(table=online_table)
    first = _offline_with_one_complete_one_stockless()
    # Make PN-B complete in the first generation.
    first.seed("acme", "stock_position", ("PN-B", "LOC-1"), StockPosition(
        tenant_id="acme", pn="PN-B", location="LOC-1", on_hand=2, serviceable=2,
        unserviceable_in_repair=0, allocated_reserved=0, rental=0, loan=0,
        extract_date=D1))
    populate_online(
        first,
        online,
        tenant=ACME,
        keys=[("PN-A", "LOC-1"), ("PN-B", "LOC-1")],
    )
    assert online.iter_keys(tenant=ACME) == (
        ("PN-A", "LOC-1"),
        ("PN-B", "LOC-1"),
    )

    # PN-B is now incomplete and a formerly present key is omitted entirely.
    second = _offline_with_one_complete_one_stockless()
    result = populate_online(
        second,
        online,
        tenant=ACME,
        keys=[("PN-B", "LOC-1")],
    )

    assert result.skipped_incomplete == 1
    assert online.iter_keys(tenant=ACME) == ()
    with pytest.raises(FeatureStoreLookupError):
        online.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1")
    with pytest.raises(FeatureStoreLookupError):
        online.get_bundle(tenant=ACME, pn="PN-B", location="LOC-1")


def test_partial_stage_failure_preserves_last_committed_generation(
    online_table,
) -> None:
    from botocore.exceptions import ClientError

    online = DynamoDbOnlineStore(table=online_table)
    first = _offline_with_one_complete_one_stockless()
    committed = populate_online(
        first,
        online,
        tenant=ACME,
        keys=[("PN-A", "LOC-1")],
    )
    old_generation = online.current_generation(tenant=ACME)
    assert committed.committed_generation == old_generation.generation

    class FailSecondStage:
        def __init__(self, delegate):
            self.delegate = delegate
            self.stage_calls = 0

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def put_bundle(self, bundle, *, stage):
            self.stage_calls += 1
            if self.stage_calls == 2:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ProvisionedThroughputExceededException",
                            "Message": "injected stage failure",
                        }
                    },
                    "PutItem",
                )
            return self.delegate.put_bundle(bundle, stage=stage)

    second = _offline_with_one_complete_one_stockless()
    second.seed("acme", "stock_position", ("PN-C", "LOC-1"), StockPosition(
        tenant_id="acme", pn="PN-C", location="LOC-1", on_hand=3, serviceable=3,
        unserviceable_in_repair=0, allocated_reserved=0, rental=0, loan=0,
        extract_date=D1))
    result = populate_online(
        second,
        FailSecondStage(online),
        tenant=ACME,
        keys=[("PN-C", "LOC-1"), ("PN-A", "LOC-1")],
    )

    assert result.failed_writes == 1
    assert result.committed_generation is None
    assert online.current_generation(tenant=ACME) == old_generation
    assert online.iter_keys(tenant=ACME) == (("PN-A", "LOC-1"),)
    assert online.get_bundle(
        tenant=ACME,
        pn="PN-A",
        location="LOC-1",
    ).stock_position.serviceable == 8


def test_all_bundles_materialize_before_first_dynamo_mutation(
    online_table,
    monkeypatch,
) -> None:
    import trax_io_feature_store.online_writer as writer

    offline = _offline_with_one_complete_one_stockless()
    offline.seed("acme", "stock_position", ("PN-B", "LOC-1"), StockPosition(
        tenant_id="acme", pn="PN-B", location="LOC-1", on_hand=2, serviceable=2,
        unserviceable_in_repair=0, allocated_reserved=0, rental=0, loan=0,
        extract_date=D1))
    original = writer.materialize_bundle
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected materialization failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(writer, "materialize_bundle", fail_second)

    with pytest.raises(RuntimeError, match="materialization failure"):
        populate_online(
            offline,
            DynamoDbOnlineStore(table=online_table),
            tenant=ACME,
            keys=[("PN-A", "LOC-1"), ("PN-B", "LOC-1")],
        )

    assert online_table.scan(Select="COUNT")["Count"] == 0
