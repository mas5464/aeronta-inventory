"""JSON snapshot round-trip for InMemoryFeatureStore (fast BFF boot).

Locks the three load-bearing properties of the snapshot format:
(1) lossless round-trip (same model types + payloads per tenant/bucket/key),
(2) identity interning — pooled objects shared across keys stay ONE instance
    after reload (disk size and container RAM both depend on this),
(3) fail-loud contract — unknown format/bucket, non-model values, and
    validation failures raise SnapshotFormatError naming the culprit.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from trax_io_feature_store.client import InMemoryFeatureStore
from trax_io_feature_store.schemas.features import (
    CurrentPolicy,
    DemandHistory,
    DemandObservation,
    StockPosition,
    VendorEconomics,
)
from trax_io_feature_store.snapshot import (
    _BUCKET_MODELS,
    SNAPSHOT_FORMAT,
    SnapshotFormatError,
    dump_store,
    load_store,
)

_D = date(2024, 4, 1)


def _sample_store() -> InMemoryFeatureStore:
    store = InMemoryFeatureStore()
    store.seed(
        "acme", "stock_position", ("PN1", "YYZ"),
        StockPosition(
            tenant_id="acme", pn="PN1", location="YYZ",
            on_hand=5, serviceable=4, extract_date=_D,
        ),
    )
    store.seed(
        "acme", "current_policy", ("PN1", "YYZ"),
        CurrentPolicy(
            tenant_id="acme", pn="PN1", location="YYZ",
            rop=2, eoq=3, safety_stock=1, max_stock=6, extract_date=_D,
        ),
    )
    store.seed(
        "acme", "vendor_economics", ("PN1", "V1"),
        VendorEconomics(
            tenant_id="acme", pn="PN1", vendor="V1",
            unit_cost=Decimal("12.34"), extract_date=_D,
        ),
    )
    demand = DemandHistory(
        tenant_id="acme", pn="PN1", location="YYZ",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2024, 3, 1),
                removals=2,
                issues=1,
                removal_events=1,
                issue_events=1,
            )
        ],
        observation_start=date(2024, 1, 1),
        observation_end=date(2024, 3, 31),
        bucket="month",
        event_count_source="observed",
        extract_date=_D,
    )
    # Pooling assigns the SAME instance to every planning key of a PN — model that here.
    store.seed("acme", "demand_history", ("PN1", "YYZ"), demand)
    store.seed("acme", "demand_history", ("PN1", "YUL"), demand)
    # Second tenant proves the tenant dimension round-trips.
    store.seed(
        "beta", "stock_position", ("PN9", "LHR"),
        StockPosition(
            tenant_id="beta", pn="PN9", location="LHR",
            on_hand=1, serviceable=1, extract_date=_D,
        ),
    )
    return store


def test_round_trip_preserves_types_and_payloads(tmp_path):
    store = _sample_store()
    stats = dump_store(store, tmp_path / "fs.json")
    loaded = load_store(tmp_path / "fs.json")

    assert set(loaded._data) == set(store._data)
    for tenant_id, buckets in store._data.items():
        assert set(loaded._data[tenant_id]) == set(buckets)
        for bucket, entries in buckets.items():
            loaded_entries = loaded._data[tenant_id][bucket]
            assert set(loaded_entries) == set(entries)
            for key, value in entries.items():
                got = loaded_entries[key]
                assert type(got) is type(value)
                assert got.model_dump() == value.model_dump()
    # Decimal survives the mode="json" string round-trip.
    ve = loaded._data["acme"]["vendor_economics"][("PN1", "V1")]
    assert ve.unit_cost == Decimal("12.34")
    assert stats == {"tenants": 2, "buckets": 5, "entries": 6, "unique_values": 5}


def test_shared_instances_stay_shared_after_reload(tmp_path):
    dump_store(_sample_store(), tmp_path / "fs.json")
    loaded = load_store(tmp_path / "fs.json")
    a = loaded._data["acme"]["demand_history"][("PN1", "YYZ")]
    b = loaded._data["acme"]["demand_history"][("PN1", "YUL")]
    assert a is b


def test_legacy_snapshot_without_demand_window_or_event_counts_still_loads(tmp_path):
    payload = {
        "format": SNAPSHOT_FORMAT,
        "tenants": {
            "acme": {
                "demand_history": {
                    "values": [
                        {
                            "tenant_id": "acme",
                            "pn": "PN1",
                            "location": "YYZ",
                            "observations": [
                                {
                                    "bucket": "month",
                                    "period_start": "2024-03-01",
                                    "removals": 2,
                                    "issues": 1,
                                }
                            ],
                            "extract_date": "2024-04-01",
                        }
                    ],
                    "entries": [[["PN1", "YYZ"], 0]],
                }
            }
        },
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload))

    loaded = load_store(path)
    history = loaded._data["acme"]["demand_history"][("PN1", "YYZ")]

    assert history.observation_start is None
    assert history.observation_end is None
    assert history.event_count_source == "unavailable"
    assert history.observations[0].removal_events is None
    assert history.observations[0].issue_events is None


def test_bucket_models_cover_every_store_bucket():
    assert set(_BUCKET_MODELS) == set(InMemoryFeatureStore._BUCKETS)


def test_unknown_format_version_fails_loud(tmp_path):
    (tmp_path / "fs.json").write_text(json.dumps({"format": 99, "tenants": {}}))
    with pytest.raises(SnapshotFormatError, match="format"):
        load_store(tmp_path / "fs.json")


def test_unknown_bucket_fails_loud(tmp_path):
    payload = {
        "format": SNAPSHOT_FORMAT,
        "tenants": {"acme": {"not_a_bucket": {"values": [], "entries": []}}},
    }
    (tmp_path / "fs.json").write_text(json.dumps(payload))
    with pytest.raises(SnapshotFormatError, match="not_a_bucket"):
        load_store(tmp_path / "fs.json")


def test_validation_failure_names_the_bucket(tmp_path):
    payload = {
        "format": SNAPSHOT_FORMAT,
        "tenants": {
            "acme": {
                "stock_position": {
                    "values": [{"tenant_id": "acme", "pn": "PN1"}],  # missing required fields
                    "entries": [[["PN1", "YYZ"], 0]],
                }
            }
        },
    }
    (tmp_path / "fs.json").write_text(json.dumps(payload))
    with pytest.raises(SnapshotFormatError, match="stock_position"):
        load_store(tmp_path / "fs.json")


def test_dump_rejects_non_model_values(tmp_path):
    store = InMemoryFeatureStore()
    store.seed("acme", "stock_position", ("PN1", "YYZ"), {"on_hand": 5})
    with pytest.raises(SnapshotFormatError, match="stock_position"):
        dump_store(store, tmp_path / "fs.json")


def test_truncated_json_file_fails_loud(tmp_path):
    (tmp_path / "fs.json").write_text('{"format": 1, "tena')
    with pytest.raises(SnapshotFormatError, match="not valid JSON"):
        load_store(tmp_path / "fs.json")


def test_missing_tenants_key_fails_loud(tmp_path):
    (tmp_path / "fs.json").write_text(json.dumps({"format": 1}))
    with pytest.raises(SnapshotFormatError):
        load_store(tmp_path / "fs.json")


def test_bucket_missing_values_fails_loud(tmp_path):
    payload = {
        "format": 1,
        "tenants": {"acme": {"stock_position": {"entries": []}}},
    }
    (tmp_path / "fs.json").write_text(json.dumps(payload))
    with pytest.raises(SnapshotFormatError, match="stock_position"):
        load_store(tmp_path / "fs.json")


def test_out_of_range_value_index_fails_loud(tmp_path):
    payload = {
        "format": 1,
        "tenants": {
            "acme": {
                "stock_position": {
                    "values": [],
                    "entries": [[["PN1", "YYZ"], 0]],
                }
            }
        },
    }
    (tmp_path / "fs.json").write_text(json.dumps(payload))
    with pytest.raises(SnapshotFormatError, match="stock_position"):
        load_store(tmp_path / "fs.json")
