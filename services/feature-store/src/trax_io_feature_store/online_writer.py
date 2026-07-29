"""Copy-on-write population of the DynamoDB online feature snapshot.

All bundles are materialized and size-checked before the first write. Complete
bundles are then staged beneath a new invisible generation and one conditional
tenant pointer is committed last. Any materialization, staging, or pointer
failure leaves the prior committed generation fully visible and unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from trax_io_feature_store.client import TenantContext
from trax_io_feature_store.materialize import materialize_bundle

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_feature_store.client import FeatureStoreClient
    from trax_io_feature_store.online_store import DynamoDbOnlineStore

LOG = logging.getLogger("trax_io.feature_store.online_writer")

_REQUIRED = ("stock_position",)
_SAFE_DYNAMODB_ITEM_BYTES = 390 * 1024


@dataclass(frozen=True)
class PopulateResult:
    """Outcome of an online-population pass over a set of keys."""

    written: int = 0
    skipped_incomplete: int = 0
    failed_oversize: int = 0
    failed_writes: int = 0
    committed_generation: str | None = None

    @property
    def total(self) -> int:
        return (
            self.written
            + self.skipped_incomplete
            + self.failed_oversize
            + self.failed_writes
        )


def _estimated_item_bytes(bundle: object) -> int:
    """Conservatively estimate the staged DynamoDB string-item payload size."""

    body = bundle.model_dump_json()
    return (
        len("tenant_id")
        + len(bundle.tenant_id.encode("utf-8"))
        + len("pn_location")
        + 160  # generation prefix + separators + future-safe metadata margin
        + len(bundle.pn.encode("utf-8"))
        + len(bundle.location.encode("utf-8"))
        + len("body")
        + len(body.encode("utf-8"))
    )


def populate_online(
    offline: FeatureStoreClient,
    online: DynamoDbOnlineStore,
    *,
    tenant: TenantContext,
    keys: Iterable[tuple[str, str]],
    required: Sequence[str] = _REQUIRED,
    demand_window: int | None = None,
) -> PopulateResult:
    """Publish one atomic committed generation for the supplied offline keyset."""

    normalized: set[tuple[str, str]] = set()
    for raw_key in keys:
        if (
            not isinstance(raw_key, (tuple, list))
            or len(raw_key) != 2
            or not all(isinstance(value, str) and value for value in raw_key)
        ):
            raise ValueError(f"invalid online population key: {raw_key!r}")
        normalized.add((raw_key[0], raw_key[1]))

    # Preflight every materialization before allocating/staging a generation.
    bundles = []
    skipped = 0
    for pn, location in sorted(normalized):
        kwargs = {} if demand_window is None else {"demand_window": demand_window}
        bundle = materialize_bundle(
            offline, tenant=tenant, pn=pn, location=location, **kwargs
        )
        if any(getattr(bundle, group) is None for group in required):
            skipped += 1
            LOG.info("skip incomplete online key tenant=%s pn=%s location=%s", tenant.tenant_id,
                     pn, location)
            continue
        bundles.append(bundle)

    oversized = [
        bundle
        for bundle in bundles
        if _estimated_item_bytes(bundle) > _SAFE_DYNAMODB_ITEM_BYTES
    ]
    if oversized:
        for bundle in oversized:
            LOG.error(
                "online bundle exceeds safe DynamoDB item size "
                "tenant=%s pn=%s location=%s",
                tenant.tenant_id,
                bundle.pn,
                bundle.location,
            )
        return PopulateResult(
            skipped_incomplete=skipped,
            failed_oversize=len(oversized),
        )

    stage = online.begin_population(tenant=tenant)
    for bundle in bundles:
        try:
            online.put_bundle(bundle, stage=stage)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            is_oversize = (
                error.get("Code") == "ValidationException"
                and "size" in str(error.get("Message") or "").lower()
            )
            LOG.error(
                "online stage failed tenant=%s pn=%s location=%s: %s",
                tenant.tenant_id,
                bundle.pn,
                bundle.location,
                exc,
            )
            return PopulateResult(
                skipped_incomplete=skipped,
                failed_oversize=int(is_oversize),
                failed_writes=int(not is_oversize),
            )

    try:
        generation = online.commit_population(
            stage=stage,
            key_count=len(bundles),
        )
    except ClientError as exc:
        LOG.error(
            "online generation commit failed tenant=%s generation=%s: %s",
            tenant.tenant_id,
            stage.generation,
            exc,
        )
        return PopulateResult(
            skipped_incomplete=skipped,
            failed_writes=1,
        )
    return PopulateResult(
        written=len(bundles),
        skipped_incomplete=skipped,
        committed_generation=generation.generation,
    )
