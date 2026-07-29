"""DynamoDB-only serving composition for the native feature path.

This module intentionally has no dependency on the offline Iceberg client.
Agent Spine serving needs only DynamoDB Query/GetItem permissions and must not
load PyIceberg, access Glue, or enumerate keys from an untrusted environment
variable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from trax_io_feature_store.client import TenantContext
from trax_io_feature_store.online_store import (
    DynamoDbOnlineStore,
    OnlineGeneration,
)

ONLINE_TABLE_ENV = "TRAX_IO_FEATURE_ONLINE_TABLE"


def _required_nonblank(config: Mapping[str, str], name: str) -> str:
    value = str(config.get(name) or "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _load_dynamodb_resource() -> Any:
    import boto3

    return boto3.resource("dynamodb")


@dataclass(frozen=True)
class NativeOnlineSnapshot:
    """One committed generation pinned across key and bundle reads."""

    generation: OnlineGeneration
    keys: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class NativeOnlineRuntime:
    """Tenant-scoped online serving client and its query-backed key universe."""

    tenant: TenantContext
    online: DynamoDbOnlineStore

    def snapshot(self) -> NativeOnlineSnapshot:
        generation = self.online.current_generation(tenant=self.tenant)
        keys = self.online.iter_keys(
            tenant=self.tenant,
            generation=generation,
        )
        return NativeOnlineSnapshot(generation=generation, keys=keys)

    def keys(self) -> tuple[tuple[str, str], ...]:
        """Compatibility shortcut; serving should retain :meth:`snapshot`."""

        return self.snapshot().keys


def build_native_online_runtime_from_env(
    tenant_id: str,
    *,
    environ: Mapping[str, str] | None = None,
    dynamodb_resource: Any | None = None,
) -> NativeOnlineRuntime:
    """Build the serving runtime without importing or constructing Iceberg."""

    config = os.environ if environ is None else environ
    online_table_name = _required_nonblank(config, ONLINE_TABLE_ENV)
    resource = (
        dynamodb_resource
        if dynamodb_resource is not None
        else _load_dynamodb_resource()
    )
    return NativeOnlineRuntime(
        tenant=TenantContext(tenant_id=tenant_id),
        online=DynamoDbOnlineStore(table=resource.Table(online_table_name)),
    )
