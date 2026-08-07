"""Production wiring for the native Iceberg + DynamoDB feature path.

The storage clients deliberately accept injected catalog/table objects for
tests. This module is the small composition root that turns deployment
configuration into those clients, and is shared by the Agent Spine bootstrap
and the population job so the two sides cannot drift onto different tables.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from trax_io_feature_store.client import TenantContext
from trax_io_feature_store.iceberg_store import GlueIcebergFeatureStore
from trax_io_feature_store.online_runtime import build_native_online_runtime_from_env
from trax_io_feature_store.online_store import DynamoDbOnlineStore
from trax_io_feature_store.online_writer import PopulateResult, populate_online

_CATALOG_NAME_ENV = "TRAX_IO_FEATURE_CATALOG_NAME"
_CATALOG_TYPE_ENV = "TRAX_IO_FEATURE_CATALOG_TYPE"
_WAREHOUSE_ENV = "TRAX_IO_FEATURE_WAREHOUSE"
_NAMESPACE_ENV = "TRAX_IO_FEATURE_NAMESPACE"
_TABLE_PREFIX_ENV = "TRAX_IO_FEATURE_TABLE_PREFIX"


@dataclass(frozen=True)
class NativeFeatureRuntime:
    """The tenant-scoped native feature clients used by serving and population."""

    tenant: TenantContext
    offline: GlueIcebergFeatureStore
    online: DynamoDbOnlineStore

    def pin_latest(self) -> NativeFeatureRuntime:
        """Freeze every offline read in this pass to one committed run id."""

        return NativeFeatureRuntime(
            tenant=self.tenant,
            offline=self.offline.pin_latest_run(tenant=self.tenant),
            online=self.online,
        )

    def keys(self) -> tuple[tuple[str, str], ...]:
        """Return the committed stock-backed inference universe deterministically."""

        return tuple(self.offline.iter_inference_keys(tenant=self.tenant))


@dataclass(frozen=True)
class NativePopulationResult:
    """Population outcome plus the exact input keyset used for the pass."""

    population: PopulateResult
    keys: tuple[tuple[str, str], ...]


def _load_catalog(config: Mapping[str, str]) -> Any:
    """Lazily import PyIceberg so Spark-only Glue modules stay dependency-light."""

    from pyiceberg.catalog import load_catalog

    properties: dict[str, str] = {
        "type": str(config.get(_CATALOG_TYPE_ENV) or "glue").strip(),
    }
    warehouse = str(config.get(_WAREHOUSE_ENV) or "").strip()
    if warehouse:
        properties["warehouse"] = warehouse
    name = str(config.get(_CATALOG_NAME_ENV) or "glue").strip()
    return load_catalog(name, **properties)


def build_native_runtime_from_env(
    tenant_id: str,
    *,
    environ: Mapping[str, str] | None = None,
    catalog: Any | None = None,
    dynamodb_resource: Any | None = None,
) -> NativeFeatureRuntime:
    """Build tenant-scoped production clients from deployment configuration.

    Required:
      ``TRAX_IO_FEATURE_ONLINE_TABLE``

    Optional catalog settings default to the AWS Glue catalog named ``glue``.
    Standard AWS credential/region configuration is intentionally delegated to
    the AWS SDK credential chain.
    """

    config = os.environ if environ is None else environ
    online_runtime = build_native_online_runtime_from_env(
        tenant_id,
        environ=config,
        dynamodb_resource=dynamodb_resource,
    )
    resolved_catalog = catalog if catalog is not None else _load_catalog(config)
    namespace = str(config.get(_NAMESPACE_ENV) or "").strip() or None
    table_prefix = (
        str(config[_TABLE_PREFIX_ENV])
        if _TABLE_PREFIX_ENV in config
        else None
    )
    return NativeFeatureRuntime(
        tenant=online_runtime.tenant,
        offline=GlueIcebergFeatureStore(
            catalog=resolved_catalog,
            namespace=namespace,
            table_prefix=table_prefix,
        ),
        online=online_runtime.online,
    )


def populate_native_online_from_env(
    tenant_id: str,
    *,
    environ: Mapping[str, str] | None = None,
    catalog: Any | None = None,
    dynamodb_resource: Any | None = None,
    demand_window: int | None = None,
) -> NativePopulationResult:
    """Populate DynamoDB from the latest committed Iceberg batch for a tenant."""

    runtime = build_native_runtime_from_env(
        tenant_id,
        environ=environ,
        catalog=catalog,
        dynamodb_resource=dynamodb_resource,
    ).pin_latest()
    keys = runtime.keys()
    population = populate_online(
        runtime.offline,
        runtime.online,
        tenant=runtime.tenant,
        keys=keys,
        demand_window=demand_window,
    )
    return NativePopulationResult(population=population, keys=keys)
