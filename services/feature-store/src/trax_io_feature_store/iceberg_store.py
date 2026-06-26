"""GlueIcebergFeatureStore — the production read client backed by the Iceberg lake.

Conforms to the same `FeatureStoreClient` Protocol as `InMemoryFeatureStore` (ADR-0002), so the
Agent Spine swaps it in via DI with no call-site changes. It reads the tables the Phase-2 Glue
jobs materialize (`glue_catalog.<namespace>.<feature_group>`, partitioned by
``(tenant_id, extract_date)``) and returns the same pydantic models the in-memory stub returns —
the two are observationally equivalent (verified by the shared contract test).

The pyiceberg `Catalog` is injected: production passes a `GlueCatalog` (Glue Data Catalog + S3 +
the tenant's KMS key, enforced by the job/role IAM); tests pass a local `SqlCatalog`. The client
is catalog-agnostic. pyiceberg is pure-Python, so no Spark/JVM is needed to read.

Tenant isolation: every method requires a `TenantContext` (`_require_tenant`) and filters on the
``tenant_id`` partition, so a cross-tenant key simply finds no rows -> `FeatureStoreLookupError`,
identical to the in-memory stub. Reads always resolve the **latest** ``extract_date`` for a key.
"""

from __future__ import annotations

from functools import reduce
from typing import TYPE_CHECKING, Any

from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import And, EqualTo

from trax_io_feature_store.client import (
    FeatureStoreLookupError,
    TenantContext,
    _require_tenant,
)
from trax_io_feature_store.schemas import (
    CausalUtilization,
    Criticality,
    CurrentPolicy,
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
    StockPosition,
    VendorEconomics,
    WashRateHistory,
    WashRatePoint,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pydantic import BaseModel
    from pyiceberg.catalog import Catalog

_DEFAULT_NAMESPACE = "trax_io"


class GlueIcebergFeatureStore:
    """`FeatureStoreClient` implementation reading the Iceberg feature lake via pyiceberg."""

    def __init__(self, *, catalog: Catalog, namespace: str = _DEFAULT_NAMESPACE) -> None:
        self._catalog = catalog
        self._namespace = namespace

    # ---- internals ----------------------------------------------------

    def _scan_latest(
        self, feature_group: str, tenant: TenantContext, key: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Rows of the latest snapshot matching ``tenant_id`` + ``key``.

        The "latest snapshot" is the rows at the most recent ``extract_date`` AND, within that,
        the most recent ``ingested_at`` — because Iceberg appends do NOT dedupe, so a re-run /
        retry / backfill can leave more than one row-set for the same key+date. Keeping only the
        freshest ingestion gives last-write-wins semantics matching ``InMemoryFeatureStore``.

        Returns >1 row only for the exploded groups (``demand_history`` per period_start,
        ``wash_rate_history`` per period_month). Raises ``FeatureStoreLookupError`` when the table
        is missing OR exists but is empty (e.g. the unmaterialized causal/wash groups, whose
        tables the CDK creates but no Glue job populates) OR the key is absent — matching the
        in-memory stub's miss behavior.
        """
        tenant = _require_tenant(tenant)
        identifier = f"{self._namespace}.{feature_group}"
        try:
            table = self._catalog.load_table(identifier)
        except NoSuchTableError as exc:
            raise FeatureStoreLookupError(
                f"no {feature_group} table for tenant={tenant.tenant_id}"
            ) from exc

        predicates = [EqualTo("tenant_id", tenant.tenant_id)]
        predicates += [EqualTo(col, val) for col, val in key.items()]
        row_filter = reduce(And, predicates) if len(predicates) > 1 else predicates[0]

        rows = table.scan(row_filter=row_filter).to_arrow().to_pylist()
        if not rows:
            raise FeatureStoreLookupError(
                f"no {feature_group} row for tenant={tenant.tenant_id} key={key}"
            )
        latest_date = max(r["extract_date"] for r in rows)
        rows = [r for r in rows if r["extract_date"] == latest_date]
        ingestions = [r["ingested_at"] for r in rows if r.get("ingested_at") is not None]
        if ingestions:
            latest_ing = max(ingestions)
            rows = [r for r in rows if r.get("ingested_at") == latest_ing]
        return rows

    def _one(
        self, feature_group: str, tenant: TenantContext, key: dict[str, str]
    ) -> dict[str, Any]:
        return self._scan_latest(feature_group, tenant, key)[0]

    @staticmethod
    def _build(model_cls: type[BaseModel], row: dict[str, Any], tenant_id: str) -> Any:
        """Construct a flat-schema model from a row, keeping only its declared fields.

        Iceberg rows carry extra metadata columns (manifest_sha256, ingested_at) that the
        ``extra="forbid"`` models reject, so we project onto ``model_fields`` first.
        """
        data = {k: row[k] for k in model_cls.model_fields if k in row}
        data["tenant_id"] = tenant_id
        return model_cls(**data)

    # ---- FeatureStoreClient surface ----------------------------------

    def get_demand_history(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> DemandHistory:
        rows = self._scan_latest("demand_history", tenant, {"pn": pn, "location": location})
        observations = [
            DemandObservation(
                bucket=r["bucket"],
                period_start=r["period_start"],
                removals=r["removals"],
                issues=r["issues"],
            )
            for r in rows
        ]
        observations.sort(key=lambda o: o.period_start)
        head = rows[0]
        # `source` is intentionally omitted: the Glue column is "nightly-extract" (hyphen) while
        # the model Literal is "nightly_extract"; the default matches the in-memory stub.
        return DemandHistory(
            tenant_id=tenant.tenant_id,
            pn=head["pn"],
            location=head["location"],
            interchange_group_id=head.get("interchange_group_id"),
            observations=observations,
            extract_date=head["extract_date"],
        )

    def get_causal_utilization(
        self, *, tenant: TenantContext, ac_type: str, destination: str
    ) -> CausalUtilization:
        row = self._one(
            "causal_utilization", tenant, {"ac_type": ac_type, "destination": destination}
        )
        return self._build(CausalUtilization, row, tenant.tenant_id)

    def get_lead_time_distribution(
        self, *, tenant: TenantContext, pn: str, vendor: str, condition: str
    ) -> LeadTimeDistribution:
        row = self._one(
            "lead_time_distribution",
            tenant,
            {"pn": pn, "vendor": vendor, "condition": condition},
        )
        return self._build(LeadTimeDistribution, row, tenant.tenant_id)

    def get_wash_rate_history(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> WashRateHistory:
        # Exploded in the lake (one row per period_month), like demand_history -> aggregate.
        rows = self._scan_latest("wash_rate_history", tenant, {"pn": pn, "location": location})
        points = [
            WashRatePoint(period_month=r["period_month"], wash_rate=r["wash_rate"]) for r in rows
        ]
        points.sort(key=lambda p: p.period_month)
        head = rows[0]
        return WashRateHistory(
            tenant_id=tenant.tenant_id,
            pn=head["pn"],
            location=head["location"],
            points=points,
            extract_date=head["extract_date"],
        )

    def get_vendor_economics(
        self, *, tenant: TenantContext, pn: str, vendor: str
    ) -> VendorEconomics:
        row = self._one("vendor_economics", tenant, {"pn": pn, "vendor": vendor})
        return self._build(VendorEconomics, row, tenant.tenant_id)

    def get_part_attributes(self, *, tenant: TenantContext, pn: str) -> PartAttributes:
        row = self._one("part_attributes", tenant, {"pn": pn})
        return self._build(PartAttributes, row, tenant.tenant_id)

    def get_criticality(self, *, tenant: TenantContext, pn: str) -> Criticality:
        row = self._one("criticality", tenant, {"pn": pn})
        return self._build(Criticality, row, tenant.tenant_id)

    def get_interchangeable_graph(
        self, *, tenant: TenantContext, pn: str
    ) -> InterchangeableGraph:
        row = self._one("interchangeable_graph", tenant, {"pn": pn})
        edges = [
            InterchangeEdge(**{k: e[k] for k in InterchangeEdge.model_fields if k in e})
            for e in (row.get("edges") or [])
        ]
        return InterchangeableGraph(
            tenant_id=tenant.tenant_id,
            pn=row["pn"],
            group_id=row["group_id"],
            members=list(row.get("members") or []),
            edges=edges,
            extract_date=row["extract_date"],
        )

    def get_location_graph(self, *, tenant: TenantContext, location: str) -> LocationGraph:
        row = self._one("location_graph", tenant, {"location": location})
        node = LocationNode(
            location=row["location"],
            related_main_warehouse=row.get("related_main_warehouse"),
            role=row["role"],
        )
        return LocationGraph(
            tenant_id=tenant.tenant_id,
            location=row["location"],
            node=node,
            children=list(row.get("children") or []),
            extract_date=row["extract_date"],
        )

    def get_open_orders_snapshot(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> OpenOrdersSnapshot:
        row = self._one("open_orders_snapshot", tenant, {"pn": pn, "location": location})
        orders = [
            OpenOrder(**{k: o[k] for k in OpenOrder.model_fields if k in o})
            for o in (row.get("orders") or [])
        ]
        return OpenOrdersSnapshot(
            tenant_id=tenant.tenant_id,
            pn=row["pn"],
            location=row["location"],
            snapshot_at=row["snapshot_at"],
            orders=orders,
            total_open_qty=row["total_open_qty"],
            extract_date=row["extract_date"],
        )

    def get_stock_position(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> StockPosition:
        row = self._one("stock_position", tenant, {"pn": pn, "location": location})
        return self._build(StockPosition, row, tenant.tenant_id)

    def get_current_policy(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> CurrentPolicy:
        row = self._one("current_policy", tenant, {"pn": pn, "location": location})
        return self._build(CurrentPolicy, row, tenant.tenant_id)
