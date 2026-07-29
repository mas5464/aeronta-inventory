"""#W3-4: offline precompute batch + PlannerStore.from_snapshot fast-boot path.

The BFF must not run the full RecommendationService at container boot (tens of minutes at
62K keys with the statistical projector). `bff/precompute.py` runs the engine once offline
and persists recommendations as JSON; `PlannerStore.from_snapshot` loads that JSON at boot
instead of recomputing. These tests prove: (1) the precompute CLI/function writes the
expected artifacts, and (2) `from_snapshot` over a precomputed file is behaviorally
equivalent to `from_extract` (same recommendation_ids, statuses, priority order) — i.e. the
precompute path is faithful to the original in-process computation.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from trax_io_feature_store.schemas import LeadTimeDistribution
from trax_io_reco.contracts.candidate import CandidateFrontier
from trax_io_reco.contracts.context import ScheduledDemandItem
from trax_io_reco.contracts.enums import EvidenceKind

import trax_io_spine.bff.precompute as precompute_module
from trax_io_spine.bff.models import TaskStatus
from trax_io_spine.bff.precompute import run as run_precompute
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)
_NOW_ISO = "2026-04-01T00:00:00+00:00"
_NOW = datetime(2026, 4, 1, tzinfo=UTC)
_ALL_TASK_STATUSES = tuple(TaskStatus)


def _precompute(tmp_path: Path, **overrides) -> tuple[Path, dict]:
    out_dir = tmp_path / "snapshot"
    args = argparse.Namespace(
        extract_dir=str(_SAMPLE),
        tenant="acme",
        now=_NOW_ISO,
        out=str(out_dir),
        pool_by_part=False,
        projector="historical",
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    meta = run_precompute(args)
    return out_dir, meta


def test_precompute_writes_recs_and_meta(tmp_path):
    out_dir, meta = _precompute(tmp_path)

    recs_path = out_dir / "recs.json"
    frontiers_path = out_dir / "frontiers.json"
    meta_path = out_dir / "meta.json"
    assert recs_path.exists()
    assert frontiers_path.exists()
    assert meta_path.exists()

    recs = json.loads(recs_path.read_text())
    assert isinstance(recs, list)
    assert len(recs) > 0
    assert all("recommendation_id" in r for r in recs)
    frontiers = [
        CandidateFrontier.model_validate(item)
        for item in json.loads(frontiers_path.read_text())
    ]
    assert frontiers
    assert all(
        sum(candidate.is_no_change for candidate in frontier.candidates) == 1
        for frontier in frontiers
    )

    on_disk_meta = json.loads(meta_path.read_text())
    assert on_disk_meta == meta
    assert meta["tenant"] == "acme"
    assert meta["pool_by_part"] is False
    assert meta["projector"] == "historical"
    assert meta["count"] == len(recs)
    assert meta["frontiers"] == len(frontiers)
    assert meta["now"] == _NOW_ISO


def test_precompute_preserves_business_offset_date_for_planning_as_of(tmp_path):
    out_dir, meta = _precompute(
        tmp_path,
        now="2026-04-01T23:30:00-04:00",
    )
    recs = json.loads((out_dir / "recs.json").read_text())

    assert meta["now"] == "2026-04-02T03:30:00+00:00"
    assert recs
    assert {rec["generated_at"] for rec in recs} == {
        "2026-04-02T03:30:00Z"
    }
    assert {
        rec["calculation_evidence"]["as_of"]
        for rec in recs
        if rec["calculation_evidence"] is not None
    } == {"2026-04-01"}


def test_from_snapshot_matches_from_extract_queue(tmp_path):
    out_dir, meta = _precompute(tmp_path)

    snapshot_store = PlannerStore.from_snapshot(
        tenant_id="acme",
        extract_dir=str(_SAMPLE),
        recs_file=str(out_dir / "recs.json"),
        now=_NOW,
        pool_by_part=False,
    )
    extract_store = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW, pool_by_part=False
    )

    for status in _ALL_TASK_STATUSES:
        snap_rows = snapshot_store.queue(status=status, limit=10_000)
        ext_rows = extract_store.queue(status=status, limit=10_000)

        # recommendation_id is a freshly-minted ULID per RecommendationService.run(), so a
        # brand-new from_extract() run mints different ids than the precomputed snapshot —
        # compare on the stable (pn, location, type, recommended_quantity) identity instead,
        # in the same priority order.
        def _key(rows):
            return [
                (r.pn, r.location, r.type, r.recommended_quantity, r.status, r.tier)
                for r in rows
            ]

        assert _key(snap_rows) == _key(ext_rows)
        assert len(snap_rows) == len(ext_rows)

    assert meta["count"] == sum(
        len(extract_store.queue(status=s, limit=10_000))
        for s in _ALL_TASK_STATUSES
    )


def test_from_snapshot_ids_are_loaded_verbatim_from_recs_file(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    recs = json.loads((out_dir / "recs.json").read_text())
    ids_on_disk = {r["recommendation_id"] for r in recs}

    snapshot_store = PlannerStore.from_snapshot(
        tenant_id="acme",
        extract_dir=str(_SAMPLE),
        recs_file=str(out_dir / "recs.json"),
        now=_NOW,
        pool_by_part=False,
    )
    all_rows = []
    for status in _ALL_TASK_STATUSES:
        all_rows += snapshot_store.queue(status=status, limit=10_000)
    ids_in_store = {r.recommendation_id for r in all_rows}

    assert ids_in_store == ids_on_disk


def test_from_extract_default_path_unchanged():
    # Guard against the from_snapshot refactor changing from_extract behavior: same
    # assertions as the pre-existing test_store_reads.py smoke checks.
    store = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW
    )
    rows = store.queue()
    assert len(rows) >= 1
    assert all(r.status is TaskStatus.PENDING for r in rows)
    scores = [r.priority_score for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_precompute_writes_full_snapshot_dir(tmp_path):
    # Fast-boot slice: --out is a COMPLETE snapshot dir — feature store, keys
    # universe, and manifest land next to recs.json/meta.json so the BFF can boot
    # with no extract dir at all (PlannerStore.from_snapshot_dir).
    out_dir, meta = _precompute(tmp_path)

    for artifact in (
        "feature_store.json",
        "keys.json",
        "manifest.json",
        "scheduled_demand.json",
    ):
        assert (out_dir / artifact).exists(), f"missing {artifact}"

    assert meta["snapshot_format"] == 1
    assert meta["keys"] > 0

    keys = json.loads((out_dir / "keys.json").read_text())
    assert isinstance(keys, list)
    assert len(keys) == meta["keys"]
    assert all(isinstance(k, list) and len(k) == 2 for k in keys)

    fs_raw = json.loads((out_dir / "feature_store.json").read_text())
    assert fs_raw["format"] == 1
    assert "acme" in fs_raw["tenants"]
    assert "stock_position" in fs_raw["tenants"]["acme"]


def test_from_snapshot_dir_matches_from_extract(tmp_path):
    out_dir, _meta = _precompute(tmp_path)

    snap = PlannerStore.from_snapshot_dir(tenant_id="acme", snapshot_dir=str(out_dir))
    ext = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW, pool_by_part=False
    )

    def _key(rows):
        return [
            (r.pn, r.location, r.type, r.recommended_quantity, r.status, r.tier)
            for r in rows
        ]

    for status in _ALL_TASK_STATUSES:
        assert _key(snap.queue(status=status, limit=10_000)) == _key(
            ext.queue(status=status, limit=10_000)
        )

    # The snapshot's feature store serves the same reads as the extract-built one.
    assert sorted(snap.keys) == sorted(ext.keys)
    assert snap.dashboard().model_dump() == ext.dashboard().model_dump()
    pn, location = sorted(snap.keys)[0]
    assert snap.part_context(pn, location).model_dump() == ext.part_context(
        pn, location
    ).model_dump()
    selected = next(
        entry
        for entry in snap._entries.values()
        if (entry.rec.part_number, entry.rec.current_location)
        == ("HYD-PUMP-001", "YYZ")
        and entry.rec.policy is None
    )
    selected_context = snap.part_context(
        "HYD-PUMP-001",
        "YYZ",
        recommendation_id=selected.rec.recommendation_id,
    )
    assert selected_context.proposed_policy is not None
    assert selected_context.planning_trace.projected_demand == (
        selected.rec.calculation_evidence.projected_demand
    )
    # Manifest travels inside the snapshot dir (feeds view input).
    assert snap._manifest == ext._manifest
    assert snap._manifest != {}


def test_precompute_round_trips_new_and_rep_supply_cycle_lanes(
    monkeypatch,
    tmp_path,
):
    original_build = precompute_module.build_stores_from_extract

    def _build_with_supply_lanes(*args, **kwargs):
        fs, inventory_state, tenant_id, keys = original_build(*args, **kwargs)
        for condition, mean in (("NEW", 17.0), ("REP", 53.0)):
            fs.seed(
                tenant_id,
                "lead_time_distribution",
                ("HYD-PUMP-001", "DEFAULT", condition),
                LeadTimeDistribution(
                    tenant_id=tenant_id,
                    pn="HYD-PUMP-001",
                    vendor="DEFAULT",
                    condition=condition,
                    realized_mean_days=mean,
                    realized_p50_days=mean - 1,
                    realized_p90_days=mean + 4,
                    realized_p99_days=mean + 8,
                    n_observations=14,
                    extract_date=date(2026, 4, 1),
                    evidence_status="observed",
                    source="order_plan_closed_orders",
                    grouping_level="part_condition",
                    confidence="medium",
                    data_cutoff=date(2026, 3, 31),
                    model_version="supply-cycle-v1",
                    proxy_definition=(
                        "order_creation_to_last_receipt"
                        if condition == "REP"
                        else None
                    ),
                    classification_source="explicit_order_type",
                ),
            )
        return fs, inventory_state, tenant_id, keys

    monkeypatch.setattr(
        precompute_module,
        "build_stores_from_extract",
        _build_with_supply_lanes,
    )
    out_dir, _meta = _precompute(tmp_path)

    context = PlannerStore.from_snapshot_dir(
        tenant_id="acme",
        snapshot_dir=str(out_dir),
    ).part_context("HYD-PUMP-001", "YYZ")

    assert context.procurement_lead_time.status == "observed"
    assert context.procurement_lead_time.mean_days == 17
    assert context.procurement_lead_time.proxy_definition is None
    assert context.repair_cycle_time.status == "observed"
    assert context.repair_cycle_time.mean_days == 53
    assert (
        context.repair_cycle_time.proxy_definition
        == "order_creation_to_last_receipt"
    )
    assert context.repair_cycle_time.proxy_label == "RO cycle-time proxy"
    assert context.lead_time is not None
    assert context.lead_time.realized_mean_days == 17


def test_old_feature_snapshot_defaults_to_unavailable_typed_lane(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    path = out_dir / "feature_store.json"
    payload = json.loads(path.read_text())
    lead_time_bucket = payload["tenants"]["acme"]["lead_time_distribution"]
    legacy_value = next(
        value
        for value in lead_time_bucket["values"]
        if value["condition"] == "NEW" and value["vendor"] == "DEFAULT"
    )
    target_pn = legacy_value["pn"]
    for field in (
        "evidence_status",
        "source",
        "grouping_level",
        "confidence",
        "data_cutoff",
        "model_version",
        "proxy_definition",
        "classification_source",
    ):
        legacy_value.pop(field)
    path.write_text(json.dumps(payload))

    store = PlannerStore.from_snapshot_dir(
        tenant_id="acme",
        snapshot_dir=str(out_dir),
    )
    location = next(location for pn, location in store.keys if pn == target_pn)
    context = store.part_context(target_pn, location)

    assert context.lead_time is not None
    assert context.procurement_lead_time.status == "unavailable"
    assert context.procurement_lead_time.mean_days is None
    assert context.procurement_lead_time.source is None
    assert "predates trustworthy provenance" in (
        context.procurement_lead_time.unavailable_reason or ""
    )


def test_candidate_frontiers_round_trip_exactly_through_snapshot_paths(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    on_disk = tuple(
        CandidateFrontier.model_validate(item)
        for item in json.loads((out_dir / "frontiers.json").read_text())
    )

    snapshot_dir_store = PlannerStore.from_snapshot_dir(
        tenant_id="acme",
        snapshot_dir=str(out_dir),
    )
    recs_only_store = PlannerStore.from_snapshot(
        tenant_id="acme",
        extract_dir=str(_SAMPLE),
        recs_file=str(out_dir / "recs.json"),
        now=_NOW,
    )

    expected_by_key = {
        (frontier.candidates[0].pn, frontier.candidates[0].location): frontier
        for frontier in on_disk
    }
    assert snapshot_dir_store._candidate_frontiers == expected_by_key
    assert recs_only_store._candidate_frontiers == expected_by_key
    for key, frontier in expected_by_key.items():
        context = snapshot_dir_store.part_context(*key)
        assert context.candidate_frontier == frontier
        assert (
            context.candidate_frontier.model_dump(mode="json")
            == frontier.model_dump(mode="json")
        )


def test_selected_recommendation_keeps_frontier_while_selecting_exact_trace(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    store = PlannerStore.from_snapshot_dir(
        tenant_id="acme",
        snapshot_dir=str(out_dir),
    )
    key = ("HYD-PUMP-001", "YYZ")
    selected = next(
        entry
        for entry in store._entries.values()
        if (entry.rec.part_number, entry.rec.current_location) == key
        and entry.rec.policy is None
    )

    default_context = store.part_context(*key)
    selected_context = store.part_context(
        *key,
        recommendation_id=selected.rec.recommendation_id,
    )

    assert selected_context.candidate_frontier == default_context.candidate_frontier
    assert selected_context.candidate_frontier is not None
    assert selected_context.planning_trace.projected_demand == (
        selected.rec.calculation_evidence.projected_demand
    )


def test_legacy_snapshot_without_frontiers_is_default_safe(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    (out_dir / "frontiers.json").unlink()

    snapshot_dir_store = PlannerStore.from_snapshot_dir(
        tenant_id="acme",
        snapshot_dir=str(out_dir),
    )
    recs_only_store = PlannerStore.from_snapshot(
        tenant_id="acme",
        extract_dir=str(_SAMPLE),
        recs_file=str(out_dir / "recs.json"),
        now=_NOW,
    )
    key = snapshot_dir_store.keys[0]

    assert snapshot_dir_store.part_context(*key).candidate_frontier is None
    assert recs_only_store.part_context(*key).candidate_frontier is None


def test_snapshot_dir_rejects_cross_tenant_candidate_frontier(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    path = out_dir / "frontiers.json"
    frontiers = json.loads(path.read_text())
    frontiers[0]["tenant_id"] = "globex"
    for candidate in frontiers[0]["candidates"]:
        candidate["tenant_id"] = "globex"
    path.write_text(json.dumps(frontiers))

    with pytest.raises(ValueError, match="candidate frontier tenant"):
        PlannerStore.from_snapshot_dir(
            tenant_id="acme",
            snapshot_dir=str(out_dir),
        )


def test_from_snapshot_dir_ids_are_loaded_verbatim(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    ids_on_disk = {
        r["recommendation_id"] for r in json.loads((out_dir / "recs.json").read_text())
    }
    snap = PlannerStore.from_snapshot_dir(tenant_id="acme", snapshot_dir=str(out_dir))
    ids_in_store = set()
    for status in _ALL_TASK_STATUSES:
        ids_in_store |= {r.recommendation_id for r in snap.queue(status=status, limit=10_000)}
    assert ids_in_store == ids_on_disk


def test_from_snapshot_dir_tenant_mismatch_fails(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    with pytest.raises(ValueError, match="tenant"):
        PlannerStore.from_snapshot_dir(tenant_id="globex", snapshot_dir=str(out_dir))


def test_from_snapshot_dir_rejects_mixed_tenant_recommendations(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    recs_path = out_dir / "recs.json"
    recs = json.loads(recs_path.read_text())
    assert len(recs) > 1
    recs[0]["tenant_id"] = "globex"
    recs_path.write_text(json.dumps(recs))

    with pytest.raises(ValueError, match="recommendation tenant"):
        PlannerStore.from_snapshot_dir(
            tenant_id="acme",
            snapshot_dir=str(out_dir),
        )


def test_from_snapshot_rejects_crossed_tenant_recommendations(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    recs_path = out_dir / "crossed-recs.json"
    recs = json.loads((out_dir / "recs.json").read_text())
    for rec in recs:
        rec["tenant_id"] = "globex"
    recs_path.write_text(json.dumps(recs))

    with pytest.raises(ValueError, match="recommendation tenant"):
        PlannerStore.from_snapshot(
            tenant_id="acme",
            extract_dir=str(_SAMPLE),
            recs_file=str(recs_path),
            now=_NOW,
        )


def test_from_snapshot_dir_missing_artifact_fails(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    (out_dir / "keys.json").unlink()
    with pytest.raises(FileNotFoundError, match="keys.json"):
        PlannerStore.from_snapshot_dir(tenant_id="acme", snapshot_dir=str(out_dir))


def test_from_snapshot_dir_unknown_snapshot_format_fails(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    meta = json.loads((out_dir / "meta.json").read_text())
    meta["snapshot_format"] = 99
    (out_dir / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="snapshot_format"):
        PlannerStore.from_snapshot_dir(tenant_id="acme", snapshot_dir=str(out_dir))


def test_snapshot_dir_round_trips_non_empty_scheduled_demand(monkeypatch, tmp_path):
    original_build = precompute_module.build_stores_from_extract

    def _build_with_schedule(*args, **kwargs):
        fs, inventory_state, tenant_id, keys = original_build(*args, **kwargs)
        inventory_state.seed(
            tenant_id,
            "scheduled_demand",
            ("HYD-PUMP-001", "YYZ"),
            (
                ScheduledDemandItem(
                    due_date=date(2026, 5, 31),  # selected policy horizon end
                    qty=3,
                    source_ref="WO-boundary",
                    source_kind=EvidenceKind.WORK_ORDER,
                ),
                ScheduledDemandItem(
                    due_date=date(2026, 6, 1),
                    qty=50,
                    source_ref="WO-after",
                    source_kind=EvidenceKind.WORK_ORDER,
                ),
            ),
        )
        return fs, inventory_state, tenant_id, keys

    monkeypatch.setattr(
        precompute_module, "build_stores_from_extract", _build_with_schedule
    )
    out_dir, _meta = _precompute(tmp_path)

    snapshot_store = PlannerStore.from_snapshot_dir(
        tenant_id="acme", snapshot_dir=str(out_dir)
    )
    trace = snapshot_store.part_context("HYD-PUMP-001", "YYZ").planning_trace

    assert trace.horizon_end == "2026-05-31"
    assert trace.scheduled_demand_due == 3


def test_snapshot_dir_round_trips_known_empty_scheduled_demand_availability(
    tmp_path,
) -> None:
    out_dir, _meta = _precompute(tmp_path)
    payload = json.loads((out_dir / "scheduled_demand.json").read_text())
    keys = {tuple(key) for key in json.loads((out_dir / "keys.json").read_text())}

    assert payload["format"] == 2
    assert {
        (entry["pn"], entry["location"]) for entry in payload["entries"]
    } == keys
    assert all(entry["items"] == [] for entry in payload["entries"])

    snapshot_store = PlannerStore.from_snapshot_dir(
        tenant_id="acme",
        snapshot_dir=str(out_dir),
    )
    for pn, location in keys:
        assert (
            snapshot_store.inventory_state.get_scheduled_demand(
                tenant=snapshot_store.tenant,
                pn=pn,
                location=location,
            )
            == ()
        )
        assert (
            snapshot_store.inventory_state.get_scheduled_demand_status(
                tenant=snapshot_store.tenant,
                pn=pn,
                location=location,
            )
            == "available"
        )


def test_snapshot_dir_reads_legacy_non_empty_scheduled_demand_format(tmp_path) -> None:
    out_dir, _meta = _precompute(tmp_path)
    keys = sorted(
        tuple(key) for key in json.loads((out_dir / "keys.json").read_text())
    )
    legacy_key = keys[0]
    legacy_item = ScheduledDemandItem(
        due_date=date(2026, 4, 20),
        qty=2,
        source_ref="LEGACY-WO",
        source_kind=EvidenceKind.WORK_ORDER,
    )
    (out_dir / "scheduled_demand.json").write_text(
        json.dumps(
            {
                "format": 1,
                "entries": [
                    {
                        "pn": legacy_key[0],
                        "location": legacy_key[1],
                        "items": [legacy_item.model_dump(mode="json")],
                    }
                ],
            }
        )
    )

    snapshot_store = PlannerStore.from_snapshot_dir(
        tenant_id="acme",
        snapshot_dir=str(out_dir),
    )
    inventory_state = snapshot_store.inventory_state
    assert inventory_state.get_scheduled_demand_status(
        tenant=snapshot_store.tenant,
        pn=legacy_key[0],
        location=legacy_key[1],
    ) == "available"
    assert inventory_state.get_scheduled_demand(
        tenant=snapshot_store.tenant,
        pn=legacy_key[0],
        location=legacy_key[1],
    ) == (legacy_item,)
    unavailable_key = keys[1]
    assert inventory_state.get_scheduled_demand_status(
        tenant=snapshot_store.tenant,
        pn=unavailable_key[0],
        location=unavailable_key[1],
    ) == "unavailable"


def test_snapshot_dir_without_scheduled_artifact_remains_loadable(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    (out_dir / "scheduled_demand.json").unlink()

    snapshot_store = PlannerStore.from_snapshot_dir(
        tenant_id="acme", snapshot_dir=str(out_dir)
    )
    pn, location = snapshot_store.keys[0]
    trace = snapshot_store.part_context(pn, location).planning_trace

    # The selected recommendation now persists its exact served calculation, so
    # deleting the optional raw schedule artifact cannot erase or downgrade that
    # decision evidence.
    assert trace.calculation_source == "served_calculation"
    assert trace.scheduled_demand_due == 0
    assert not any(
        "scheduled-demand evidence is unavailable" in warning.lower()
        for warning in trace.warnings
    )
