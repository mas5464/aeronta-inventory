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

import json
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
    RequisitionLine,
    RequisitionSnapshot,
    StockPosition,
    VendorEconomics,
    WashRateHistory,
    WashRatePoint,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pydantic import BaseModel
    from pyiceberg.catalog import Catalog

_FEATURE_SOURCE_DOMAINS: dict[str, frozenset[str]] = {
    "demand_history": frozenset(
        {"demand_history_rotables", "demand_history_expendables"}
    ),
    "causal_utilization": frozenset({"causal_values"}),
    "vendor_economics": frozenset({"pn_vendor_price"}),
    "part_attributes": frozenset({"part_master"}),
    "criticality": frozenset({"part_master"}),
    "interchangeable_graph": frozenset({"part_chain_details"}),
    "location_graph": frozenset({"location_master"}),
    "open_orders_snapshot": frozenset({"order_plan"}),
    "requisition_snapshot": frozenset({"order_plan_data_requisition"}),
    "stock_position": frozenset({"stock_amount"}),
    "current_policy": frozenset({"stock_level_upload"}),
}
_FEATURE_ANY_SOURCE_DOMAINS: dict[str, frozenset[str]] = {
    # Configured promises can materialize from price alone; observed cycles can
    # materialize from closed orders alone. Requiring both would hide valid
    # closed-only evidence, while requiring neither could serve a stale batch.
    "lead_time_distribution": frozenset(
        {"pn_vendor_price", "order_plan_closed_orders"}
    ),
}
_LEGACY_LEAD_TIME_PROVENANCE_FIELDS = frozenset(
    {
        "evidence_status",
        "source",
        "grouping_level",
        "confidence",
        "data_cutoff",
        "model_version",
        "proxy_definition",
        "classification_source",
    }
)


class GlueIcebergFeatureStore:
    """`FeatureStoreClient` implementation reading the Iceberg feature lake via pyiceberg."""

    def __init__(
        self,
        *,
        catalog: Catalog,
        namespace: str | None = None,
        table_prefix: str | None = None,
        pinned_run_id: str | None = None,
    ) -> None:
        self._catalog = catalog
        self._namespace = namespace
        self._table_prefix = (
            ("raw_" if namespace is None else "")
            if table_prefix is None
            else table_prefix
        )
        if pinned_run_id is not None and not pinned_run_id.strip():
            raise ValueError("pinned_run_id must be non-empty")
        self._pinned_run_id = pinned_run_id

    def _identifier(
        self,
        feature_group: str,
        tenant: TenantContext,
    ) -> str:
        namespace = self._namespace or (
            f"trax_io_lake_{tenant.tenant_id}".replace("-", "_")
        )
        return f"{namespace}.{self._table_prefix}{feature_group}"

    # ---- internals ----------------------------------------------------

    def _latest_run_status(
        self,
        tenant: TenantContext,
    ) -> dict[str, Any] | None:
        """Return the newest committed manifest ledger row, when provisioned.

        The run-status job is the snapshot commit marker and must run after the
        feature jobs for a manifest. Existing lakes without the ledger retain
        the legacy latest-table fallback during migration.
        """

        tenant = _require_tenant(tenant)
        try:
            table = self._catalog.load_table(
                self._identifier("extract_run_status", tenant)
            )
        except NoSuchTableError:
            return None
        rows = (
            table.scan(
                row_filter=EqualTo("tenant_id", tenant.tenant_id),
            )
            .to_arrow()
            .to_pylist()
        )
        if not rows:
            return None
        if self._pinned_run_id is not None:
            rows = [
                row
                for row in rows
                if row.get("run_id") == self._pinned_run_id
            ]
            if not rows:
                raise FeatureStoreLookupError(
                    "pinned extract run is unavailable for "
                    f"tenant={tenant.tenant_id} run_id={self._pinned_run_id!r}"
                )
        latest_date = max(row["extract_date"] for row in rows)
        rows = [
            row
            for row in rows
            if row["extract_date"] == latest_date
        ]
        ingestions = [
            row["ingested_at"]
            for row in rows
            if row.get("ingested_at") is not None
        ]
        if ingestions:
            latest_ingested_at = max(ingestions)
            rows = [
                row
                for row in rows
                if row.get("ingested_at") == latest_ingested_at
            ]
        payloads = {
            str(row.get("artifact_status_json") or "")
            for row in rows
        }
        if len(payloads) != 1:
            raise FeatureStoreLookupError(
                "conflicting latest extract-run status rows for "
                f"tenant={tenant.tenant_id} extract_date={latest_date}"
            )
        raw_statuses = next(iter(payloads))
        try:
            statuses = json.loads(raw_statuses)
        except (json.JSONDecodeError, TypeError) as exc:
            raise FeatureStoreLookupError(
                "invalid latest extract-run artifact status for "
                f"tenant={tenant.tenant_id} extract_date={latest_date}"
            ) from exc
        if not isinstance(statuses, dict) or any(
            not isinstance(domain, str) or not isinstance(status, str)
            for domain, status in statuses.items()
        ):
            raise FeatureStoreLookupError(
                "invalid latest extract-run artifact status for "
                f"tenant={tenant.tenant_id} extract_date={latest_date}"
            )
        return {
            "extract_date": latest_date,
            "artifact_statuses": statuses,
            "run_id": rows[0].get("run_id"),
            "run_status": rows[0].get("run_status"),
        }

    def pin_latest_run(
        self,
        *,
        tenant: TenantContext,
    ) -> GlueIcebergFeatureStore:
        """Return a reader fixed to one committed manifest run.

        Population may span thousands of keys. Selecting the run once prevents
        a newer ledger commit from switching later bundle reads onto a
        different extract while the same online generation is being staged.
        """

        tenant = _require_tenant(tenant)
        if self._pinned_run_id is not None:
            # Resolve once so a token constructed for the wrong tenant cannot
            # be carried forward silently.
            self._latest_run_status(tenant)
            return self
        status = self._latest_run_status(tenant)
        if status is None:
            # Migration compatibility for lakes that predate the run ledger.
            return self
        run_id = status.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise FeatureStoreLookupError(
                f"latest extract run has no run_id for tenant={tenant.tenant_id}"
            )
        return GlueIcebergFeatureStore(
            catalog=self._catalog,
            namespace=self._namespace,
            table_prefix=self._table_prefix,
            pinned_run_id=run_id,
        )

    @staticmethod
    def _require_feature_sources(
        feature_group: str,
        tenant_id: str,
        run_status: dict[str, Any] | None,
    ) -> None:
        if run_status is None:
            return
        statuses = run_status["artifact_statuses"]
        alternatives = _FEATURE_ANY_SOURCE_DOMAINS.get(feature_group)
        if alternatives is not None:
            if any(statuses.get(domain) == "succeeded" for domain in alternatives):
                return
            unavailable = {
                domain: statuses.get(domain, "absent")
                for domain in alternatives
            }
            raise FeatureStoreLookupError(
                f"latest {feature_group} source unavailable for "
                f"tenant={tenant_id} extract_date={run_status['extract_date']}: "
                f"{unavailable}"
            )
        required = _FEATURE_SOURCE_DOMAINS.get(feature_group, frozenset())
        unavailable = {
            domain: statuses.get(domain, "absent")
            for domain in required
            if statuses.get(domain) != "succeeded"
        }
        if unavailable:
            raise FeatureStoreLookupError(
                f"latest {feature_group} source unavailable for "
                f"tenant={tenant_id} extract_date={run_status['extract_date']}: "
                f"{unavailable}"
            )

    @staticmethod
    def _table_cutoff(
        table: Any,
        *,
        tenant_id: str,
        extract_date: Any | None,
    ) -> tuple[Any, Any | None] | None:
        """Newest complete append batch for a tenant, optionally pinned to a run date."""

        predicates = [EqualTo("tenant_id", tenant_id)]
        if extract_date is not None:
            predicates.append(EqualTo("extract_date", extract_date))
        row_filter = (
            reduce(And, predicates)
            if len(predicates) > 1
            else predicates[0]
        )
        rows = (
            table.scan(
                row_filter=row_filter,
                selected_fields=("extract_date", "ingested_at"),
            )
            .to_arrow()
            .to_pylist()
        )
        if not rows:
            return None
        latest_date = extract_date or max(row["extract_date"] for row in rows)
        rows = [
            row
            for row in rows
            if row["extract_date"] == latest_date
        ]
        ingestions = [
            row["ingested_at"]
            for row in rows
            if row.get("ingested_at") is not None
        ]
        return (
            latest_date,
            max(ingestions) if ingestions else None,
        )

    def _committed_feature_cutoff(
        self,
        *,
        feature_group: str,
        tenant_id: str,
        run_status: dict[str, Any],
    ) -> tuple[Any, Any | None, int]:
        """Resolve the completed materialization batch for the ledger run."""

        try:
            table = self._catalog.load_table(
                self._identifier(
                    "feature_batch_status",
                    TenantContext(tenant_id=tenant_id),
                )
            )
        except NoSuchTableError as exc:
            raise FeatureStoreLookupError(
                f"no completed {feature_group} batch for latest run "
                f"{run_status.get('run_id')!r}"
            ) from exc
        run_id = str(run_status.get("run_id") or "")
        if not run_id:
            raise FeatureStoreLookupError(
                "latest extract-run status has no run_id for "
                f"tenant={tenant_id}"
            )
        predicates = [
            EqualTo("tenant_id", tenant_id),
            EqualTo("extract_date", run_status["extract_date"]),
            EqualTo("feature_group", feature_group),
            EqualTo("run_id", run_id),
        ]
        rows = (
            table.scan(row_filter=reduce(And, predicates))
            .to_arrow()
            .to_pylist()
        )
        completed = [
            row
            for row in rows
            if row.get("status") == "completed"
        ]
        if not completed:
            raise FeatureStoreLookupError(
                f"no completed {feature_group} batch for latest run "
                f"{run_id!r}"
            )
        ingestions = [
            row["ingested_at"]
            for row in completed
            if row.get("ingested_at") is not None
        ]
        if ingestions:
            latest_commit = max(ingestions)
            completed = [
                row
                for row in completed
                if row.get("ingested_at") == latest_commit
            ]
        cutoffs = {
            (
                row.get("batch_ingested_at"),
                int(row.get("row_count") or 0),
            )
            for row in completed
        }
        if len(cutoffs) != 1:
            raise FeatureStoreLookupError(
                f"conflicting completed {feature_group} batches for latest run "
                f"{run_id!r}"
            )
        batch_ingested_at, row_count = next(iter(cutoffs))
        return (
            run_status["extract_date"],
            batch_ingested_at,
            row_count,
        )

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
        identifier = self._identifier(feature_group, tenant)
        try:
            table = self._catalog.load_table(identifier)
        except NoSuchTableError as exc:
            raise FeatureStoreLookupError(
                f"no {feature_group} table for tenant={tenant.tenant_id}"
            ) from exc

        run_status = self._latest_run_status(tenant)
        self._require_feature_sources(
            feature_group,
            tenant.tenant_id,
            run_status,
        )
        if run_status is not None:
            latest_date, latest_ingested_at, row_count = (
                self._committed_feature_cutoff(
                    feature_group=feature_group,
                    tenant_id=tenant.tenant_id,
                    run_status=run_status,
                )
            )
            cutoff = (
                (latest_date, latest_ingested_at)
                if row_count > 0
                else None
            )
        else:
            cutoff = self._table_cutoff(
                table,
                tenant_id=tenant.tenant_id,
                extract_date=None,
            )
        if cutoff is None:
            raise FeatureStoreLookupError(
                f"no {feature_group} row for tenant={tenant.tenant_id} key={key}"
            )
        latest_date, latest_ingested_at = cutoff

        predicates = [
            EqualTo("tenant_id", tenant.tenant_id),
            EqualTo("extract_date", latest_date),
        ]
        predicates += [EqualTo(col, val) for col, val in key.items()]
        row_filter = reduce(And, predicates) if len(predicates) > 1 else predicates[0]

        rows = table.scan(row_filter=row_filter).to_arrow().to_pylist()
        if latest_ingested_at is not None:
            rows = [
                row
                for row in rows
                if row.get("ingested_at") == latest_ingested_at
            ]
        if not rows:
            raise FeatureStoreLookupError(
                f"no {feature_group} row for tenant={tenant.tenant_id} key={key}"
            )
        return rows

    def _one(
        self, feature_group: str, tenant: TenantContext, key: dict[str, str]
    ) -> dict[str, Any]:
        return self._scan_latest(feature_group, tenant, key)[0]

    def iter_inference_keys(self, *, tenant: TenantContext) -> list[tuple[str, str]]:
        """Distinct ``(pn, location)`` with any ``stock_position`` row for the tenant.

        This is the universe of inference keys the online-layer writer materializes — stock is the
        load-bearing signal, so a (pn, location) with no stock is not a recommendation candidate.
        Returns ``[]`` when the table is absent (not yet provisioned).
        """
        tenant = _require_tenant(tenant)
        try:
            table = self._catalog.load_table(
                self._identifier("stock_position", tenant)
            )
        except NoSuchTableError:
            return []
        run_status = self._latest_run_status(tenant)
        try:
            self._require_feature_sources(
                "stock_position",
                tenant.tenant_id,
                run_status,
            )
        except FeatureStoreLookupError:
            return []
        if run_status is not None:
            latest_date, latest_ingested_at, row_count = (
                self._committed_feature_cutoff(
                    feature_group="stock_position",
                    tenant_id=tenant.tenant_id,
                    run_status=run_status,
                )
            )
            cutoff = (
                (latest_date, latest_ingested_at)
                if row_count > 0
                else None
            )
        else:
            cutoff = self._table_cutoff(
                table,
                tenant_id=tenant.tenant_id,
                extract_date=None,
            )
        if cutoff is None:
            return []
        latest_date, latest_ingested_at = cutoff
        rows = (
            table.scan(
                row_filter=And(
                    EqualTo("tenant_id", tenant.tenant_id),
                    EqualTo("extract_date", latest_date),
                ),
                selected_fields=(
                    "pn",
                    "location",
                    "ingested_at",
                ),
            )
            .to_arrow()
            .to_pylist()
        )
        if latest_ingested_at is not None:
            rows = [
                row
                for row in rows
                if row.get("ingested_at") == latest_ingested_at
            ]
        return sorted({(r["pn"], r["location"]) for r in rows})

    @staticmethod
    def _build(model_cls: type[BaseModel], row: dict[str, Any], tenant_id: str) -> Any:
        """Construct a flat-schema model from a row, keeping only its declared fields.

        Iceberg rows carry extra metadata columns (manifest_sha256, ingested_at) that the
        ``extra="forbid"`` models reject, so we project onto ``model_fields`` first.
        """
        data = {k: row[k] for k in model_cls.model_fields if k in row}
        if model_cls is LeadTimeDistribution and all(
            data.get(field) is None
            for field in _LEGACY_LEAD_TIME_PROVENANCE_FIELDS
        ):
            # After additive Iceberg evolution, historical rows expose null
            # provenance columns. Pydantic defaults apply only to missing
            # inputs, not explicit nulls, so omit the complete legacy-null
            # block. A partial block remains untouched and fails validation.
            for field in _LEGACY_LEAD_TIME_PROVENANCE_FIELDS:
                data.pop(field, None)
        if (
            model_cls is LeadTimeDistribution
            and data.get("observed_cycle_days") is None
        ):
            # Additive array evolution exposes null for historical rows. Omit
            # it so the legacy-safe empty-tuple default applies.
            data.pop("observed_cycle_days", None)
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
                removal_events=r.get("removal_events"),
                issue_events=r.get("issue_events"),
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
            observation_start=head.get("observation_start"),
            observation_end=head.get("observation_end"),
            event_count_source=head.get("event_count_source") or "unavailable",
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
        orders: list[OpenOrder] = []
        for order_row in row.get("orders") or []:
            payload = {
                field: order_row[field]
                for field in OpenOrder.model_fields
                if field in order_row
            }
            # The enclosing snapshot key is authoritative for legacy nested
            # structs that predate per-line location evidence.
            if not payload.get("location"):
                payload["location"] = row["location"]
            orders.append(OpenOrder(**payload))
        return OpenOrdersSnapshot(
            tenant_id=tenant.tenant_id,
            pn=row["pn"],
            location=row["location"],
            snapshot_at=row["snapshot_at"],
            orders=orders,
            total_open_qty=row["total_open_qty"],
            extract_date=row["extract_date"],
        )

    def get_requisition_snapshot(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> RequisitionSnapshot:
        row = self._one(
            "requisition_snapshot",
            tenant,
            {"pn": pn, "location": location},
        )
        lines = [
            RequisitionLine(
                **{
                    key: line[key]
                    for key in RequisitionLine.model_fields
                    if key in line
                }
            )
            for line in (row.get("lines") or [])
        ]
        return RequisitionSnapshot(
            tenant_id=tenant.tenant_id,
            pn=row["pn"],
            location=row["location"],
            snapshot_at=row["snapshot_at"],
            lines=lines,
            total_qty_needed=row["total_qty_needed"],
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
