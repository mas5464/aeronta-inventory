"""DynamoDbOnlineStore — the low-latency online-feature read/write path (design §4.2).

The online layer is a thin DynamoDB table keyed on ``(tenant_id, pn, location)`` that serves one
denormalized `FeatureBundle` per inference key, so event-triggered inference does a single
sub-10ms point lookup instead of ~12 separate feature reads. It is populated by nightly Glue and
updated incrementally by the event lane (see `materialize.materialize_bundle` for the assembly
core); this module is just the read/write codec over the table.

The boto3 ``Table`` resource is injected (DI): production passes the per-tenant CMK-encrypted
table from the CDK stack; tests pass a moto-backed table. Item shape matches the CDK key schema —
partition key ``tenant_id``, sort key ``pn_location`` (``f"{pn}#{location}"``) — with the bundle
JSON in ``body``. Reads require a `TenantContext`, and the partition key makes cross-tenant reads
structurally impossible (a foreign tenant simply finds no item).
"""

from __future__ import annotations

from typing import Any

from trax_io_feature_store.client import FeatureStoreLookupError, TenantContext, _require_tenant
from trax_io_feature_store.schemas import FeatureBundle


def _sort_key(pn: str, location: str) -> str:
    """Injective ``(pn, location)`` -> sort-key encoding.

    A plain ``f"{pn}#{location}"`` is ambiguous — ``("A#B","C")`` and ``("A","B#C")`` both encode
    to ``"A#B#C"`` and silently collide onto one DynamoDB item. eMRO part numbers and location
    codes can contain ``#`` (and any other punctuation), so the length prefix makes the encoding
    provably injective regardless of their contents.
    """
    return f"{len(pn)}#{pn}#{location}"


class DynamoDbOnlineStore:
    """Read/write `FeatureBundle`s in the online DynamoDB table."""

    def __init__(self, *, table: Any) -> None:
        # `table` is a boto3 DynamoDB Table resource (real in prod, moto-backed in tests).
        self._table = table

    def put_bundle(self, bundle: FeatureBundle) -> None:
        """Upsert the online row for ``(bundle.tenant_id, bundle.pn, bundle.location)``.

        DynamoDB caps an item at 400 KB and ``put_item`` *raises* (hard fail, no silent truncation)
        if the JSON body exceeds it. The bundle is meant to be thin: keep unbounded lists
        (notably ``demand_history.observations``) windowed at materialization time so the busiest
        parts don't fail — full history lives in Iceberg. The Glue/event-lane writer should treat
        an oversize ``put`` as an observable, metered failure rather than a silent gap.
        """
        self._table.put_item(
            Item={
                "tenant_id": bundle.tenant_id,
                "pn_location": _sort_key(bundle.pn, bundle.location),
                "body": bundle.model_dump_json(),
            }
        )

    def get_bundle(self, *, tenant: TenantContext, pn: str, location: str) -> FeatureBundle:
        """Fetch the online bundle for ``(tenant, pn, location)``.

        Requires a `TenantContext` (the isolation chokepoint) and keys the read on the
        ``tenant_id`` partition, so a cross-tenant lookup finds nothing ->
        `FeatureStoreLookupError`, identical to the in-memory stub's miss behavior.
        """
        tenant = _require_tenant(tenant)
        resp = self._table.get_item(
            Key={"tenant_id": tenant.tenant_id, "pn_location": _sort_key(pn, location)}
        )
        item = resp.get("Item")
        if not item or "body" not in item:
            raise FeatureStoreLookupError(
                f"no online bundle for tenant={tenant.tenant_id} pn={pn} location={location}"
            )
        bundle = FeatureBundle.model_validate_json(item["body"])
        # Defense-in-depth against any future key drift: the item's own body must describe the
        # key we asked for (the body carries the true pn/location).
        if bundle.tenant_id != tenant.tenant_id or bundle.pn != pn or bundle.location != location:
            raise FeatureStoreLookupError(
                f"online bundle key mismatch for tenant={tenant.tenant_id} pn={pn} "
                f"location={location} (got {bundle.tenant_id}/{bundle.pn}/{bundle.location})"
            )
        return bundle
