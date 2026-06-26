"""Pydantic schemas for the 10 v1 feature groups (design §4.2)."""

from trax_io_feature_store.schemas.features import (
    CausalUtilization,
    Criticality,
    DemandHistory,
    DemandObservation,
    InterchangeableGraph,
    InterchangeEdge,
    LeadTimeDistribution,
    LocationGraph,
    LocationNode,
    OpenOrder,
    OpenOrdersSnapshot,
    PartAttributes,
    VendorEconomics,
    WashRateHistory,
    WashRatePoint,
)

__all__ = [
    "CausalUtilization",
    "Criticality",
    "DemandHistory",
    "DemandObservation",
    "InterchangeEdge",
    "InterchangeableGraph",
    "LeadTimeDistribution",
    "LocationGraph",
    "LocationNode",
    "OpenOrder",
    "OpenOrdersSnapshot",
    "PartAttributes",
    "VendorEconomics",
    "WashRateHistory",
    "WashRatePoint",
]
