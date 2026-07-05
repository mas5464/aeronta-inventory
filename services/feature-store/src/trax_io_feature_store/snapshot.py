"""JSON snapshot persistence for `InMemoryFeatureStore` (fast BFF boot).

`dump_store` serializes a built (typically network-pooled) store to one JSON file;
`load_store` rebuilds an equivalent store. Values are interned by object identity:
pooling assigns the SAME `DemandHistory`/`StockPosition` instance to every planning
key of a PN (~2.3 keys/PN in real eMRO data), so per-bucket `values[]` holds each
unique model once and `entries[]` maps key tuples to indices — preserving disk size
AND, on load, the in-memory sharing (RAM parity with a freshly built store).

JSON, not pickle: the offline precompute host and the container may run different
Python versions (3.14 vs 3.12). Validation stays ON at load — `extra="forbid"` on the
feature models makes schema drift fail loudly at boot, never silently.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from trax_io_feature_store.client import InMemoryFeatureStore
from trax_io_feature_store.schemas.features import (
    CausalUtilization,
    Criticality,
    CurrentPolicy,
    DemandHistory,
    InterchangeableGraph,
    LeadTimeDistribution,
    LocationGraph,
    OpenOrdersSnapshot,
    PartAttributes,
    RequisitionSnapshot,
    StockPosition,
    VendorEconomics,
    WashRateHistory,
)

SNAPSHOT_FORMAT = 1

# One entry per InMemoryFeatureStore bucket; test_snapshot.py pins this map against
# `InMemoryFeatureStore._BUCKETS` so a future 14th bucket cannot silently not-snapshot.
_BUCKET_MODELS: dict[str, type[BaseModel]] = {
    "demand_history": DemandHistory,
    "causal_utilization": CausalUtilization,
    "lead_time_distribution": LeadTimeDistribution,
    "wash_rate_history": WashRateHistory,
    "vendor_economics": VendorEconomics,
    "part_attributes": PartAttributes,
    "criticality": Criticality,
    "interchangeable_graph": InterchangeableGraph,
    "location_graph": LocationGraph,
    "open_orders_snapshot": OpenOrdersSnapshot,
    "requisition_snapshot": RequisitionSnapshot,
    "stock_position": StockPosition,
    "current_policy": CurrentPolicy,
}


class SnapshotFormatError(ValueError):
    """A snapshot cannot be dumped/loaded: unknown format version, unknown bucket,
    non-model value at dump time, or a value that fails model validation at load."""


def dump_store(store: InMemoryFeatureStore, path: str | Path) -> dict[str, int]:
    """Serialize `store` to `path`. Returns counts for operator logging."""
    tenants: dict[str, dict] = {}
    n_buckets = n_entries = n_values = 0
    for tenant_id, buckets in store._data.items():
        tenant_payload: dict[str, dict] = {}
        for bucket, entries in buckets.items():
            values: list[dict] = []
            index_by_id: dict[int, int] = {}
            out_entries: list[list] = []
            for key, value in entries.items():
                if not isinstance(value, BaseModel):
                    raise SnapshotFormatError(
                        f"bucket {bucket!r} key {key!r}: expected a pydantic model, "
                        f"got {type(value).__name__}"
                    )
                idx = index_by_id.get(id(value))
                if idx is None:
                    idx = len(values)
                    index_by_id[id(value)] = idx
                    values.append(value.model_dump(mode="json"))
                out_entries.append([list(key), idx])
            tenant_payload[bucket] = {"values": values, "entries": out_entries}
            n_buckets += 1
            n_entries += len(out_entries)
            n_values += len(values)
        tenants[tenant_id] = tenant_payload
    Path(path).write_text(json.dumps({"format": SNAPSHOT_FORMAT, "tenants": tenants}))
    return {
        "tenants": len(tenants),
        "buckets": n_buckets,
        "entries": n_entries,
        "unique_values": n_values,
    }


def load_store(path: str | Path) -> InMemoryFeatureStore:
    """Rebuild an `InMemoryFeatureStore` from a `dump_store` file.

    Each unique value validates ONCE; entries sharing an index share one model
    instance (exactly as pooling built them). Fail-loud on any drift: a truncated/
    corrupt file, a missing top-level `tenants` key, a bucket missing its
    `values`/`entries`, and an out-of-range value index all raise
    `SnapshotFormatError` naming the file or bucket, rather than letting the
    underlying `json.JSONDecodeError`/`KeyError`/`IndexError` escape raw.
    """
    try:
        raw = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise SnapshotFormatError(f"snapshot file {path} is not valid JSON") from exc
    fmt = raw.get("format") if isinstance(raw, dict) else None
    if fmt != SNAPSHOT_FORMAT:
        raise SnapshotFormatError(f"unsupported snapshot format: {fmt!r}")
    tenants = raw.get("tenants")
    if not isinstance(tenants, dict):
        raise SnapshotFormatError(f"snapshot file {path} is missing a top-level 'tenants' object")
    store = InMemoryFeatureStore()
    for tenant_id, buckets in tenants.items():
        for bucket, payload in buckets.items():
            model_cls = _BUCKET_MODELS.get(bucket)
            if model_cls is None:
                raise SnapshotFormatError(f"unknown feature bucket in snapshot: {bucket!r}")
            values = payload.get("values")
            entries = payload.get("entries")
            if values is None or entries is None:
                raise SnapshotFormatError(
                    f"snapshot bucket {bucket!r} is missing values/entries"
                )
            try:
                instances = [model_cls.model_validate(v) for v in values]
            except ValidationError as exc:
                raise SnapshotFormatError(
                    f"snapshot bucket {bucket!r}: a value failed "
                    f"{model_cls.__name__} validation"
                ) from exc
            try:
                for key, idx in entries:
                    store.seed(tenant_id, bucket, tuple(key), instances[idx])
            except IndexError as exc:
                raise SnapshotFormatError(
                    f"snapshot bucket {bucket!r} has an out-of-range value index"
                ) from exc
    return store
