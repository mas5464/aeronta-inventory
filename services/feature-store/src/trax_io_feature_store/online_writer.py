"""Populate the DynamoDB online layer from an offline `FeatureStoreClient`.

This is the core of the nightly-Glue / event-lane job (design §4.2): for each inference key,
assemble the bundle from the offline lake and upsert it into the online table. It is the writer
counterpart to `online_store.DynamoDbOnlineStore` (read) and `materialize.materialize_bundle`
(assemble), wired together with the production contract the engine relies on:

- **Skip incomplete keys.** A key whose required groups (default: ``stock_position``) are absent in
  the lake is NOT written — a bundle with ``stock_position=None`` would be indistinguishable from
  zero stock downstream, so we fail closed and leave the key unpopulated (its online read then
  raises ``FeatureStoreLookupError``, which the engine handles) rather than write a misleading row.
- **Meter oversize failures.** DynamoDB caps an item at 400 KB and ``put_item`` raises; an oversize
  bundle (despite demand windowing) is counted and logged, never silently dropped, so the busiest
  parts are observable instead of an unhandled stack trace mid-batch.
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


@dataclass(frozen=True)
class PopulateResult:
    """Outcome of an online-population pass over a set of keys."""

    written: int = 0
    skipped_incomplete: int = 0
    failed_oversize: int = 0

    @property
    def total(self) -> int:
        return self.written + self.skipped_incomplete + self.failed_oversize


def populate_online(
    offline: FeatureStoreClient,
    online: DynamoDbOnlineStore,
    *,
    tenant: TenantContext,
    keys: Iterable[tuple[str, str]],
    required: Sequence[str] = _REQUIRED,
    demand_window: int | None = None,
) -> PopulateResult:
    """Materialize each ``(pn, location)`` from ``offline`` and upsert it into ``online``."""
    written = skipped = failed = 0
    for pn, location in keys:
        kwargs = {} if demand_window is None else {"demand_window": demand_window}
        bundle = materialize_bundle(
            offline, tenant=tenant, pn=pn, location=location, **kwargs
        )
        if any(getattr(bundle, group) is None for group in required):
            skipped += 1
            LOG.info("skip incomplete online key tenant=%s pn=%s location=%s", tenant.tenant_id,
                     pn, location)
            continue
        try:
            online.put_bundle(bundle)
            written += 1
        except ClientError as exc:
            failed += 1
            LOG.error("online put failed (likely >400KB) tenant=%s pn=%s location=%s: %s",
                      tenant.tenant_id, pn, location, exc)
    return PopulateResult(written=written, skipped_incomplete=skipped, failed_oversize=failed)
