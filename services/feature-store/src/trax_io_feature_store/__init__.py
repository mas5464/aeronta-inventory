"""Trax IO Feature Store public API.

The read-client exports are loaded lazily. AWS Glue 4.0 imports
``trax_io_feature_store.glue.*`` from the deployed source archive but does not
ship Pydantic, which the read-side schemas require. Keeping package
initialization dependency-light lets ETL jobs import their own Spark-only
modules without loading the application client or its optional dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("trax_io_feature_store.client"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
