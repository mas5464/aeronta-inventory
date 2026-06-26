"""Trax IO Feature Store read client.

Phase 1 scaffold. Ships the FeatureStoreClient Protocol and an in-memory stub
implementation per ADR-0002 so the Agent Spine can build against a stable
contract before the production Iceberg + DynamoDB backend ships.
"""

from trax_io_feature_store.client import (
    FeatureStoreClient,
    FeatureStoreLookupError,
    InMemoryFeatureStore,
    MissingTenantContextError,
    TenantContext,
)

__all__ = [
    "FeatureStoreClient",
    "FeatureStoreLookupError",
    "InMemoryFeatureStore",
    "MissingTenantContextError",
    "TenantContext",
]
