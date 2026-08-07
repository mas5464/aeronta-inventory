"""Event lane handler: a domain event -> hot-parts recompute against the online bundle."""

from __future__ import annotations

from typing import Any

from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import FeatureBundle

from trax_io_spine.contracts import OrchestrationResult
from trax_io_spine.event_lane.adapters import BundleFeatureStore, BundleInventoryState
from trax_io_spine.event_lane.events import DomainEvent
from trax_io_spine.event_lane.keys import DirectKeyResolver, KeyResolver
from trax_io_spine.event_lane.online import OnlineStore
from trax_io_spine.supervisor import Supervisor
from trax_io_spine.writeback.target import WritebackTarget

_EMPTY_SUMMARY = {
    "recommendations": 0,
    "written": 0,
    "deferred": 0,
    "failed": 0,
    "queued": 0,
    "rejected": 0,
    "skipped": 0,
}


class EventLaneHandler:
    def __init__(
        self,
        online_store: OnlineStore,
        writeback: WritebackTarget,
        *,
        resolver: KeyResolver | None = None,
        enforcer: Any = None,
        config: Any = None,
    ) -> None:
        self._online = online_store
        self._writeback = writeback
        self._resolver: KeyResolver = resolver or DirectKeyResolver()
        self._enforcer = enforcer
        self._config = config

    def handle(self, event: DomainEvent) -> OrchestrationResult:
        tenant = TenantContext(tenant_id=event.tenant_id)
        keys = self._resolver.resolve(event)
        if not keys:
            return OrchestrationResult(
                tenant_id=event.tenant_id,
                generated_at=event.occurred_at,
                summary=dict(_EMPTY_SUMMARY),
            )

        bundles: dict[tuple[str, str], FeatureBundle] = {}
        for pn, location in keys:
            try:
                bundles[(pn, location)] = self._online.get_bundle(
                    tenant=tenant, pn=pn, location=location
                )
            except FeatureStoreLookupError:
                continue  # no online row for this key yet

        if not bundles:
            return OrchestrationResult(
                tenant_id=event.tenant_id,
                generated_at=event.occurred_at,
                summary=dict(_EMPTY_SUMMARY),
            )

        supervisor = Supervisor(
            feature_store=BundleFeatureStore(event.tenant_id, bundles),
            inventory_state=BundleInventoryState(event.tenant_id, bundles),
            writeback=self._writeback,
            enforcer=self._enforcer,
            config=self._config,
        )
        return supervisor.run(tenant=tenant, keys=list(bundles), now=event.occurred_at)
