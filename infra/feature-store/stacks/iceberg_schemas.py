"""Iceberg column schemas for feature groups and run-coherence ledgers.

Kept as simple Glue/Iceberg column tuples — no PyArrow dependency. Keeps the
CDK synth hermetic. The Pydantic contract for the SAME feature groups lives
in `services/feature-store/src/trax_io_feature_store/schemas/features.py`;
Contract tests compare the native readers and Pydantic models, while synth
tests lock schema-sensitive tables such as supply-cycle provenance.
"""

from __future__ import annotations

# (column_name, glue_iceberg_type)
FeatureGroupColumns = list[tuple[str, str]]


FEATURE_GROUP_SCHEMAS: dict[str, FeatureGroupColumns] = {
    "demand_history": [
        ("pn", "string"),
        ("location", "string"),
        ("interchange_group_id", "string"),
        ("bucket", "string"),  # day|week|month
        ("period_start", "date"),
        ("removals", "int"),
        ("issues", "int"),
        ("removal_events", "int"),
        ("issue_events", "int"),
        ("observation_start", "date"),
        ("observation_end", "date"),
        ("event_count_source", "string"),
        ("source", "string"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "causal_utilization": [
        ("ac_type", "string"),
        ("destination", "string"),
        ("observation_date", "date"),
        ("flight_hours", "double"),
        ("flight_cycles", "int"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "lead_time_distribution": [
        ("pn", "string"),
        ("vendor", "string"),
        ("condition", "string"),
        ("promised_lead_days", "double"),
        ("realized_mean_days", "double"),
        ("realized_p50_days", "double"),
        ("realized_p90_days", "double"),
        ("realized_p99_days", "double"),
        ("promised_vs_actual_delta_mean", "double"),
        ("n_observations", "int"),
        ("observed_cycle_days", "array<int>"),
        ("evidence_status", "string"),
        ("source", "string"),
        ("grouping_level", "string"),
        ("confidence", "string"),
        ("data_cutoff", "date"),
        ("model_version", "string"),
        ("proxy_definition", "string"),
        ("classification_source", "string"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "wash_rate_history": [
        ("pn", "string"),
        ("location", "string"),
        ("period_month", "date"),
        ("wash_rate", "double"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "vendor_economics": [
        ("pn", "string"),
        ("vendor", "string"),
        ("unit_cost", "decimal(18,4)"),
        ("market_value_unit_cost", "decimal(18,4)"),
        ("average_cost", "decimal(18,4)"),
        ("kit_cost", "decimal(18,4)"),
        ("repair_cost_24mo_avg", "decimal(18,4)"),
        ("minimum_order_qty", "int"),
        ("currency", "string"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "part_attributes": [
        ("pn", "string"),
        ("description", "string"),
        ("ata_chapter", "string"),
        ("part_class", "string"),
        ("shelf_life_days", "int"),
        ("hazardous_material", "boolean"),
        ("tool_control_item", "boolean"),
        ("fleet_effectivity_tail_count", "int"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "criticality": [
        ("pn", "string"),
        ("raw_essentiality_code", "string"),
        ("canonical_tier", "int"),
        ("mapping_source", "string"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "interchangeable_graph": [
        ("pn", "string"),
        ("group_id", "string"),
        ("members", "array<string>"),
        ("edges", "array<struct<from_pn:string,to_pn:string,one_way:boolean>>"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "location_graph": [
        ("location", "string"),
        ("related_main_warehouse", "string"),
        ("role", "string"),
        ("children", "array<string>"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "open_orders_snapshot": [
        ("pn", "string"),
        ("location", "string"),
        ("snapshot_at", "timestamp"),
        (
            "orders",
            "array<struct<order_id:string,order_type:string,vendor:string,"
            "qty_open:int,expected_rcv_date:date,order_line_id:string,"
            "opened_at:timestamp,status:string,serial_number:string,shop:string,"
            "location:string>>",
        ),
        ("total_open_qty", "int"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "requisition_snapshot": [
        ("pn", "string"),
        ("location", "string"),
        ("snapshot_at", "timestamp"),
        (
            "lines",
            "array<struct<requisition_id:string,qty_needed:int,need_by:date,"
            "alt_source_location:string>>",
        ),
        ("total_qty_needed", "int"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "stock_position": [
        ("pn", "string"),
        ("location", "string"),
        ("on_hand", "int"),
        ("serviceable", "int"),
        ("unserviceable_in_repair", "int"),
        ("allocated_reserved", "int"),
        ("rental", "int"),
        ("loan", "int"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "current_policy": [
        ("pn", "string"),
        ("location", "string"),
        ("rop", "int"),
        ("eoq", "int"),
        ("safety_stock", "int"),
        ("max_stock", "int"),
        ("replenishment_lead_days", "double"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "extract_run_status": [
        ("run_id", "string"),
        ("run_status", "string"),
        ("artifact_status_json", "string"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
    "feature_batch_status": [
        ("feature_group", "string"),
        ("run_id", "string"),
        ("status", "string"),
        ("batch_ingested_at", "timestamp"),
        ("row_count", "bigint"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ],
}
