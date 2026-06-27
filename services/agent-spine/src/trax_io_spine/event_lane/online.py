"""OnlineStore Protocol + an in-memory implementation for the event lane.

`DynamoDbOnlineStore` (#2) satisfies this Protocol structurally; `InMemoryOnlineStore` backs
fast, AWS-free tests.
"""

from __future__ import annotations

from typing import Protocol

from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import FeatureBundle


class OnlineStore(Protocol):
    def get_bundle(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> FeatureBundle: ...


class InMemoryOnlineStore:
    def __init__(self, bundles: list[FeatureBundle]) -> None:
        self._by_key = {(b.tenant_id, b.pn, b.location): b for b in bundles}

    def get_bundle(self, *, tenant: TenantContext, pn: str, location: str) -> FeatureBundle:
        b = self._by_key.get((tenant.tenant_id, pn, location))
        if b is None:
            raise FeatureStoreLookupError(
                f"no online bundle for tenant={tenant.tenant_id} pn={pn} location={location}"
            )
        return b
