from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.materialize import materialize_bundle
from trax_io_feature_store.online_store import OnlineGeneration
from trax_io_feature_store.schemas import (
    DemandHistory,
    DemandObservation,
    LeadTimeDistribution,
    OpenOrder,
    OpenOrdersSnapshot,
    PartAttributes,
    StockPosition,
)
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.regime.classifier import demanded_units_24mo_from

from trax_io_spine.bff.store import PlannerStore, RecommendationNotFound
from trax_io_spine.event_lane.online import InMemoryOnlineStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def _observed_supply_cycle(
    *,
    tenant_id: str = "acme",
    pn: str = "HYD-PUMP-001",
    condition: str,
    mean: float,
    observed_cycle_days: tuple[float, ...] = (),
) -> LeadTimeDistribution:
    return LeadTimeDistribution(
        tenant_id=tenant_id,
        pn=pn,
        vendor="DEFAULT",
        condition=condition,
        promised_lead_days=None,
        realized_mean_days=mean,
        realized_p50_days=mean - 1,
        realized_p90_days=mean + 3,
        realized_p99_days=mean + 7,
        n_observations=len(observed_cycle_days) or 12,
        observed_cycle_days=observed_cycle_days,
        extract_date=date(2026, 4, 1),
        evidence_status="observed",
        source="order_plan_closed_orders",
        grouping_level="part_condition",
        confidence="medium",
        data_cutoff=date(2026, 3, 31),
        model_version=(
            "supply-cycle-v2"
            if observed_cycle_days
            else "supply-cycle-v1"
        ),
        proxy_definition=(
            "order_creation_to_last_receipt" if condition == "REP" else None
        ),
        classification_source="explicit_order_type",
    )


def _open_order(
    *,
    order_id: str,
    line_id: str | None,
    order_type: str,
    quantity: int,
    opened_at: str | None,
    status: str = "OPEN",
    vendor: str | None = None,
    shop: str | None = None,
    serial_number: str | None = None,
    expected_rcv_date: date | None = None,
    location: str | None = "YYZ",
):
    return OpenOrder(
        order_id=order_id,
        order_line_id=line_id,
        order_type=order_type,
        vendor=vendor,
        shop=shop,
        qty_open=quantity,
        expected_rcv_date=expected_rcv_date,
        opened_at=opened_at,
        status=status,
        serial_number=serial_number,
        location=location,
    )


def _open_order_snapshot(*orders, tenant_id: str = "acme"):
    return OpenOrdersSnapshot(
        tenant_id=tenant_id,
        pn="HYD-PUMP-001",
        location="YYZ",
        snapshot_at=datetime(2026, 4, 1, tzinfo=UTC),
        extract_date=date(2026, 4, 1),
        orders=list(orders),
        total_open_qty=sum(order.qty_open for order in orders),
    )


def _stock_with_repair_wip(store: PlannerStore, quantity: int) -> StockPosition:
    source = store.fs.get_stock_position(
        tenant=store.tenant,
        pn="HYD-PUMP-001",
        location="YYZ",
    )
    return source.model_copy(update={"unserviceable_in_repair": quantity})


def test_from_online_serves_the_tenant_bound_materialized_bundle():
    tenant_id = "native-tenant"
    key = ("HYD-PUMP-001", "YYZ")
    offline, _inventory, _tid, _keys = build_stores_from_extract(
        str(_SAMPLE),
        tenant_id=tenant_id,
    )
    tenant = TenantContext(tenant_id=tenant_id)
    bundle = materialize_bundle(
        offline,
        tenant=tenant,
        pn=key[0],
        location=key[1],
    )

    store = PlannerStore.from_online(
        tenant_id=tenant_id,
        online_store=InMemoryOnlineStore([bundle]),
        keys=[key],
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    context = store.part_context(*key)
    source_stock = offline.get_stock_position(
        tenant=tenant,
        pn=key[0],
        location=key[1],
    )

    assert store.tenant == tenant
    assert store.keys == [key]
    assert context.stock is not None
    assert context.stock.on_hand == source_stock.on_hand
    assert context.stock.serviceable == source_stock.serviceable
    assert context.attributes.description == "HYDRAULIC PUMP"
    assert context.planning_trace.calculation_source == "served_calculation"
    assert context.candidate_frontier is not None
    assert context.candidate_frontier.tenant_id == tenant_id


def test_from_online_rejects_a_bundle_with_a_different_tenant_identity():
    tenant_id = "native-tenant"
    key = ("HYD-PUMP-001", "YYZ")
    offline, _inventory, _tid, _keys = build_stores_from_extract(
        str(_SAMPLE),
        tenant_id="other-tenant",
    )
    foreign_bundle = materialize_bundle(
        offline,
        tenant=TenantContext(tenant_id="other-tenant"),
        pn=key[0],
        location=key[1],
    )

    class _CorruptOnlineStore:
        def get_bundle(self, **_kwargs):
            return foreign_bundle

    with pytest.raises(FeatureStoreLookupError, match="identity mismatch"):
        PlannerStore.from_online(
            tenant_id=tenant_id,
            online_store=_CorruptOnlineStore(),
            keys=[key],
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )


def test_from_online_pins_every_bundle_read_to_one_generation():
    tenant_id = "native-tenant"
    key = ("HYD-PUMP-001", "YYZ")
    offline, _inventory, _tid, _keys = build_stores_from_extract(
        str(_SAMPLE),
        tenant_id=tenant_id,
    )
    tenant = TenantContext(tenant_id=tenant_id)
    bundle = materialize_bundle(
        offline,
        tenant=tenant,
        pn=key[0],
        location=key[1],
    )
    generation = OnlineGeneration(
        tenant_id=tenant_id,
        generation="generation-a",
        key_count=1,
    )

    class _PinnedOnlineStore:
        calls = []

        def get_bundle(self, *, tenant, pn, location, generation):
            self.calls.append((tenant, pn, location, generation))
            return bundle

    online_store = _PinnedOnlineStore()
    PlannerStore.from_online(
        tenant_id=tenant_id,
        online_store=online_store,
        keys=[key],
        generation=generation,
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )

    assert online_store.calls == [(tenant, *key, generation)]


def test_from_online_rejects_cross_tenant_generation_before_bundle_read():
    class _UnreadOnlineStore:
        def get_bundle(self, **_kwargs):
            raise AssertionError("cross-tenant generation must fail before any read")

    with pytest.raises(FeatureStoreLookupError, match="generation tenant mismatch"):
        PlannerStore.from_online(
            tenant_id="native-tenant",
            online_store=_UnreadOnlineStore(),
            keys=[("P1", "YYZ")],
            generation=OnlineGeneration(
                tenant_id="globex",
                generation="generation-b",
                key_count=1,
            ),
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )


def test_part_context_assembles_from_feature_store():
    store = _store()
    pn, loc = store.keys[0]
    ctx = store.part_context(pn, loc)
    assert ctx.pn == pn and ctx.location == loc
    assert ctx.attributes.description  # from PartAttributes
    assert ctx.stock is None or ctx.stock.on_hand >= 0
    # demand history may be present with zero observations for some sample keys;
    # only assert internal consistency, not a specific non-zero count
    assert ctx.demand is None or len(ctx.demand.points) >= 0
    assert ctx.total_open_qty >= 0

    # a key with an actual demand series should show points and a matching total
    pn2, loc2 = "HYD-PUMP-001", "YYZ"
    ctx2 = store.part_context(pn2, loc2)
    assert ctx2.demand is not None
    assert len(ctx2.demand.points) >= 1
    history = store.fs.get_demand_history(tenant=store.tenant, pn=pn2, location=loc2)
    assert ctx2.demand.total_24mo == demanded_units_24mo_from(history)
    assert ctx2.planning_trace.constraints
    assert any(
        constraint.name == "minimum_order_quantity"
        and constraint.source == "vendor_economics.minimum_order_qty"
        for constraint in ctx2.planning_trace.constraints
    )


def test_part_context_exposes_new_and_rep_as_independent_observed_lanes():
    store = _store()
    pn, location = "HYD-PUMP-001", "YYZ"
    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "NEW"),
        _observed_supply_cycle(condition="NEW", mean=18),
    )
    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "REP"),
        _observed_supply_cycle(condition="REP", mean=46),
    )

    context = store.part_context(pn, location)

    assert context.procurement_lead_time.model_dump() == {
        "condition": "NEW",
        "status": "observed",
        "mean_days": 18.0,
        "p50_days": 17.0,
        "p90_days": 21.0,
        "p99_days": 25.0,
        "n_observations": 12,
        "source": "order_plan_closed_orders",
        "grouping_level": "part_condition",
        "confidence": "medium",
        "data_cutoff": "2026-03-31",
        "model_version": "supply-cycle-v1",
        "classification_source": "explicit_order_type",
        "proxy_definition": None,
        "proxy_label": None,
        "unavailable_reason": None,
    }
    assert context.repair_cycle_time.model_dump() == {
        "condition": "REP",
        "status": "observed",
        "mean_days": 46.0,
        "p50_days": 45.0,
        "p90_days": 49.0,
        "p99_days": 53.0,
        "n_observations": 12,
        "source": "order_plan_closed_orders",
        "grouping_level": "part_condition",
        "confidence": "medium",
        "data_cutoff": "2026-03-31",
        "model_version": "supply-cycle-v1",
        "classification_source": "explicit_order_type",
        "proxy_definition": "order_creation_to_last_receipt",
        "proxy_label": "RO cycle-time proxy",
        "unavailable_reason": None,
    }
    # The compatibility carrier is still and only the NEW lane.
    assert context.lead_time is not None
    assert context.lead_time.realized_mean_days == 18


def test_part_context_lane_updates_cannot_cross_contaminate():
    store = _store()
    pn, location = "HYD-PUMP-001", "YYZ"
    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "NEW"),
        _observed_supply_cycle(condition="NEW", mean=18),
    )
    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "REP"),
        _observed_supply_cycle(condition="REP", mean=46),
    )
    before = store.part_context(pn, location)

    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "NEW"),
        _observed_supply_cycle(condition="NEW", mean=29),
    )
    after_new = store.part_context(pn, location)
    assert after_new.procurement_lead_time.mean_days == 29
    assert after_new.repair_cycle_time == before.repair_cycle_time

    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "REP"),
        _observed_supply_cycle(condition="REP", mean=61),
    )
    after_rep = store.part_context(pn, location)
    assert after_rep.repair_cycle_time.mean_days == 61
    assert after_rep.procurement_lead_time == after_new.procurement_lead_time
    assert after_rep.lead_time == after_new.lead_time


def test_part_context_exposes_configured_new_and_missing_rep_without_blending():
    context = _store().part_context("HYD-PUMP-001", "YYZ")

    assert context.procurement_lead_time.status == "configured_fallback"
    assert context.procurement_lead_time.source == "pn_vendor_price"
    assert context.procurement_lead_time.n_observations == 0
    assert context.repair_cycle_time.status == "unavailable"
    assert context.repair_cycle_time.mean_days is None
    assert context.repair_cycle_time.source is None
    assert context.repair_cycle_time.proxy_definition is None
    assert context.repair_cycle_time.proxy_label is None
    assert context.repair_cycle_time.unavailable_reason


def test_legacy_new_feature_stays_legacy_projection_but_not_lane_evidence():
    store = _store()
    pn, location = "HYD-PUMP-001", "YYZ"
    legacy = LeadTimeDistribution(
        tenant_id="acme",
        pn=pn,
        vendor="DEFAULT",
        condition="NEW",
        promised_lead_days=42,
        realized_mean_days=38,
        realized_p50_days=36,
        realized_p90_days=48,
        realized_p99_days=55,
        n_observations=9,
        extract_date=date(2026, 4, 1),
    )
    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "NEW"),
        legacy,
    )

    context = store.part_context(pn, location)

    assert context.lead_time is not None
    assert context.lead_time.promised_days == 42
    assert context.lead_time.realized_mean_days == 38
    assert context.procurement_lead_time.status == "unavailable"
    assert context.procurement_lead_time.mean_days is None
    assert "predates trustworthy provenance" in (
        context.procurement_lead_time.unavailable_reason or ""
    )
    assert context.repair_cycle_time.status == "unavailable"


def test_part_context_rejects_cross_tenant_supply_cycle_identity():
    store = _store()
    pn, location = "HYD-PUMP-001", "YYZ"
    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "NEW"),
        _observed_supply_cycle(
            tenant_id="globex",
            condition="NEW",
            mean=999,
        ),
    )

    context = store.part_context(pn, location)

    assert context.lead_time is None
    assert context.procurement_lead_time.status == "unavailable"
    assert context.procurement_lead_time.mean_days is None
    assert context.procurement_lead_time.source is None
    assert context.repair_cycle_time.mean_days is None


def test_part_context_unknown_key_raises():
    with pytest.raises(RecommendationNotFound):
        _store().part_context("NOPE", "NOWHERE")


def test_part_context_degrades_without_500(monkeypatch):
    store = _store()
    pn, loc = store.keys[0]
    # a getter that blows up must degrade to None, not propagate
    def _boom(**_kwargs):
        raise RuntimeError

    monkeypatch.setattr(store.fs, "get_stock_position", _boom)
    ctx = store.part_context(pn, loc)
    assert ctx.stock is None
    assert ctx.repair_pipeline is None
    assert ctx.repair_return_profile is None


def test_part_context_discloses_unavailable_open_orders_instead_of_observed_zero(
    monkeypatch,
):
    store = _store()
    pn, loc = store.keys[0]

    def _boom(**_kwargs):
        raise RuntimeError

    monkeypatch.setattr(store.fs, "get_open_orders_snapshot", _boom)

    context = store.part_context(pn, loc)

    assert context.open_orders == ()
    assert context.total_open_qty == 0
    assert context.open_orders_status == "unavailable"
    assert context.repair_pipeline is not None
    assert context.repair_pipeline.status == "unavailable"


def test_part_context_exposes_reconciled_open_repair_pipeline_and_keeps_ro_out_of_receipts(
    monkeypatch,
):
    store = _store()
    pn, location = "HYD-PUMP-001", "YYZ"
    snapshot = _open_order_snapshot(
        _open_order(
            order_id="PO-1",
            line_id="10",
            order_type="PO",
            quantity=3,
            opened_at="2026-03-01T00:00:00Z",
            status="OPEN",
            vendor="SUPPLIER-1",
            expected_rcv_date=date(2026, 4, 10),
        ),
        _open_order(
            order_id="RO-1",
            line_id="20",
            order_type="RO",
            quantity=4,
            opened_at="2026-03-05T00:00:00Z",
            status="IN_PROGRESS",
            vendor="REPAIR-VENDOR-1",
            shop="SHOP-1",
            expected_rcv_date=date(2026, 4, 10),
        ),
    )
    stock = _stock_with_repair_wip(store, 6)
    monkeypatch.setattr(
        store.fs,
        "get_open_orders_snapshot",
        lambda **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        store.fs,
        "get_stock_position",
        lambda **_kwargs: stock,
    )
    # Force the legacy trace adapter to recalculate visible source receipts
    # from this controlled snapshot. The core helper must count PO only.
    for entry in store._entries.values():
        if (entry.rec.part_number, entry.rec.current_location) == (pn, location):
            entry.rec = entry.rec.model_copy(update={"calculation_evidence": None})

    context = store.part_context(pn, location)

    assert context.repair_pipeline is not None
    pipeline = context.repair_pipeline
    assert pipeline.tenant_id == "acme"
    assert pipeline.part_number == pn
    assert pipeline.location_code == location
    assert pipeline.as_of == date(2026, 4, 1)
    assert pipeline.status == "partial"
    assert pipeline.aggregate_wip_quantity == 6
    assert pipeline.identified_open_quantity == 4
    assert pipeline.eligible_quantity == 4
    assert pipeline.excluded_identifiable_quantity == 0
    assert pipeline.aggregate_residual_quantity == 2
    assert pipeline.source_overflow_quantity == 0
    assert pipeline.time_phased_credit_quantity == 0
    assert pipeline.warning_codes == ("repair_residual_unidentified",)
    assert len(pipeline.included) == 1
    included = pipeline.included[0]
    assert included.eligible_quantity == 4
    assert included.age_days == 27
    assert included.work_item.repair_order_id == "RO-1"
    assert included.work_item.repair_line_id == "20"
    assert included.work_item.quantity == 4
    assert included.work_item.location_code == "YYZ"
    assert included.work_item.status == "in_progress"
    assert included.work_item.shop_code == "SHOP-1"
    assert included.work_item.vendor_code == "REPAIR-VENDOR-1"
    assert included.work_item.serial_number is None
    assert pipeline.exclusions[0].reason == "unidentified_aggregate_residual"
    assert pipeline.exclusions[0].quantity == 2

    repair_order = next(
        order for order in context.open_orders if order.order_id == "RO-1"
    )
    assert repair_order.order_line_id == "20"
    assert repair_order.opened_at == "2026-03-05T00:00:00+00:00"
    assert repair_order.status == "IN_PROGRESS"
    assert repair_order.location == "YYZ"
    assert repair_order.shop == "SHOP-1"
    assert repair_order.serial_number is None
    assert context.total_open_qty == 7
    assert context.planning_trace.open_receipts_due == 3
    # The deliberately reconstructed legacy trace does not invent exact
    # repair-receipt arithmetic; the new pipeline above is the authoritative
    # zero-credit carrier.
    assert context.planning_trace.repair_receipts_due is None


def test_part_context_exposes_exclusions_partial_eligibility_and_wip_overflow(
    monkeypatch,
):
    store = _store()
    snapshot = _open_order_snapshot(
        _open_order(
            order_id="RO-AMBIG",
            line_id="1",
            order_type="RO",
            quantity=3,
            opened_at=None,
        ),
        _open_order(
            order_id="RO-VALID",
            line_id="1",
            order_type="RO",
            quantity=4,
            opened_at="2026-03-05T00:00:00Z",
        ),
    )
    stock = _stock_with_repair_wip(store, 5)
    monkeypatch.setattr(
        store.fs,
        "get_open_orders_snapshot",
        lambda **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        store.fs,
        "get_stock_position",
        lambda **_kwargs: stock,
    )

    pipeline = store.part_context("HYD-PUMP-001", "YYZ").repair_pipeline

    assert pipeline is not None
    assert pipeline.status == "partial"
    assert pipeline.identified_open_quantity == 7
    assert pipeline.eligible_quantity == 2
    assert pipeline.excluded_identifiable_quantity == 5
    assert pipeline.aggregate_residual_quantity == 0
    assert pipeline.source_overflow_quantity == 2
    assert pipeline.time_phased_credit_quantity == 0
    assert pipeline.included[0].work_item.repair_order_id == "RO-VALID"
    assert pipeline.included[0].eligible_quantity == 2
    assert {item.reason for item in pipeline.exclusions} == {
        "missing_opened_at",
        "aggregate_wip_cap",
    }
    assert pipeline.warning_codes == (
        "repair_age_missing",
        "repair_wip_mismatch",
        "repair_work_excluded",
    )


def test_part_context_projects_age_conditioned_rep_returns_over_fixed_horizons(
    monkeypatch,
):
    store = _store()
    pn, location = "HYD-PUMP-001", "YYZ"
    snapshot = _open_order_snapshot(
        _open_order(
            order_id="RO-RETURN",
            line_id="1",
            order_type="RO",
            quantity=4,
            opened_at="2026-03-05T00:00:00Z",
            status="IN_PROGRESS",
            vendor="REPAIR-VENDOR",
            shop="SHOP-1",
        ),
    )
    stock = _stock_with_repair_wip(store, 4)
    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "REP"),
        _observed_supply_cycle(
            condition="REP",
            mean=46,
            observed_cycle_days=(
                30,
                32,
                34,
                36,
                38,
                40,
                42,
                44,
                46,
                48,
                50,
                52,
            ),
        ),
    )
    monkeypatch.setattr(
        store.fs,
        "get_open_orders_snapshot",
        lambda **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        store.fs,
        "get_stock_position",
        lambda **_kwargs: stock,
    )

    context = store.part_context(pn, location)

    assert context.repair_return_profile is not None
    profile = context.repair_return_profile
    assert profile.tenant_id == "acme"
    assert profile.part_number == pn
    assert profile.location_code == location
    assert profile.as_of == date(2026, 4, 1)
    assert profile.status == "available"
    assert profile.eligible_quantity == 4
    assert profile.excluded_quantity == 0
    assert profile.aggregate_residual_quantity == 0
    assert [horizon.horizon_days for horizon in profile.horizons] == [30, 60, 90]
    expected = [horizon.expected_units for horizon in profile.horizons]
    assert expected == sorted(expected)
    assert expected[0] > 0
    assert expected[-1] <= profile.eligible_quantity
    for horizon in profile.horizons:
        assert 0 <= horizon.p10_units <= horizon.expected_units
        assert horizon.expected_units <= horizon.p90_units <= 4
        assert len(horizon.item_probabilities) == 1
        item = horizon.item_probabilities[0]
        assert item.repair_order_id == "RO-RETURN"
        assert item.repair_line_id == "1"
        assert item.quantity == 4
        assert item.age_days == 27
        assert 0 <= item.serviceable_probability <= item.return_probability <= 1
    assert profile.evidence.method == "kaplan_meier"
    assert profile.evidence.completed_observations == 12
    assert profile.evidence.right_censored_observations == 4
    assert profile.evidence.serviceable_yield == 1
    assert profile.evidence.tat_multiplier == 1
    assert (
        profile.evidence.source
        == "order_plan_closed_orders+open_work_right_censoring"
    )
    assert profile.evidence.confidence == "medium"
    assert profile.evidence.data_cutoff == date(2026, 3, 31)
    assert profile.evidence.proxy_definition == "order_creation_to_last_receipt"
    assert profile.warning_codes == ()

    # A legacy aggregate REP distribution can still age-condition the line, but
    # it must not claim those open units participated in a censoring-aware fit.
    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "REP"),
        _observed_supply_cycle(condition="REP", mean=46),
    )
    fallback = store.part_context(pn, location).repair_return_profile
    assert fallback is not None
    assert fallback.status == "partial"
    assert fallback.evidence.method == "lognormal_quantile"
    assert fallback.evidence.right_censored_observations == 0
    assert (
        "repair_return_right_censoring_not_fitted"
        in fallback.warning_codes
    )

    future_rep = _observed_supply_cycle(
        condition="REP",
        mean=46,
        observed_cycle_days=(
            30,
            32,
            34,
            36,
            38,
            40,
            42,
            44,
            46,
            48,
            50,
            52,
        ),
    ).model_copy(
        update={
            "data_cutoff": date(2026, 4, 2),
            "extract_date": date(2026, 4, 2),
        }
    )
    store.fs.seed(
        "acme",
        "lead_time_distribution",
        (pn, "DEFAULT", "REP"),
        future_rep,
    )
    no_lookahead = store.part_context(pn, location).repair_return_profile
    assert no_lookahead is not None
    assert no_lookahead.status == "unavailable"
    assert no_lookahead.evidence.method == "unavailable"
    assert all(horizon.expected_units == 0 for horizon in no_lookahead.horizons)
    assert "repair_return_evidence_unavailable" in no_lookahead.warning_codes


def test_part_context_withholds_projected_returns_for_nonrepairable_part(
    monkeypatch,
):
    store = _store()
    pn, location = "HYD-PUMP-001", "YYZ"
    attributes = store.fs.get_part_attributes(tenant=store.tenant, pn=pn)
    monkeypatch.setattr(
        store.fs,
        "get_part_attributes",
        lambda **_kwargs: attributes.model_copy(update={"part_class": "consumable"}),
    )

    context = store.part_context(pn, location)

    assert context.repair_pipeline is not None
    assert context.repair_return_profile is None


def test_part_context_rejects_explicit_cross_tenant_repair_snapshot(
    monkeypatch,
):
    store = _store()
    foreign = _open_order_snapshot(
        _open_order(
            order_id="RO-GLOBEX",
            line_id="1",
            order_type="RO",
            quantity=3,
            opened_at="2026-03-01T00:00:00Z",
        ),
        tenant_id="globex",
    )
    stock = _stock_with_repair_wip(store, 3)
    monkeypatch.setattr(
        store.fs,
        "get_open_orders_snapshot",
        lambda **_kwargs: foreign,
    )
    monkeypatch.setattr(
        store.fs,
        "get_stock_position",
        lambda **_kwargs: stock,
    )

    context = store.part_context("HYD-PUMP-001", "YYZ")

    assert context.open_orders == ()
    assert context.open_orders_status == "unavailable"
    assert context.repair_pipeline is not None
    assert context.repair_pipeline.tenant_id == "acme"
    assert context.repair_pipeline.status == "unavailable"
    assert context.repair_pipeline.identified_open_quantity == 0
    assert context.repair_pipeline.eligible_quantity == 0
    assert context.repair_pipeline.aggregate_residual_quantity == 3
    assert context.repair_pipeline.time_phased_credit_quantity == 0
    assert context.repair_pipeline.warning_codes == (
        "repair_pipeline_unavailable",
        "repair_residual_unidentified",
    )
    assert context.repair_return_profile is not None
    assert context.repair_return_profile.tenant_id == "acme"
    assert context.repair_return_profile.eligible_quantity == 0


def test_part_context_prefers_policy_recommendation_for_multi_recommendation_key():
    store = _store()
    key_entries = [
        entry
        for entry in store._entries.values()
        if (entry.rec.part_number, entry.rec.current_location) == ("HYD-PUMP-001", "YYZ")
    ]
    assert len(key_entries) > 1
    policy_entry = next(entry for entry in key_entries if entry.rec.policy is not None)
    action_entry = next(entry for entry in key_entries if entry.rec.policy is None)
    # Make the non-policy action first regardless of upstream ranking changes.
    store._entries = {
        action_entry.rec.recommendation_id: action_entry,
        policy_entry.rec.recommendation_id: policy_entry,
        **{
            rec_id: entry
            for rec_id, entry in store._entries.items()
            if entry not in (action_entry, policy_entry)
        },
    }

    context = store.part_context("HYD-PUMP-001", "YYZ")

    assert context.proposed_policy is not None
    assert context.proposed_policy.rop == policy_entry.rec.policy.rop


def test_selected_recommendation_drives_trace_without_changing_proposed_policy():
    store = _store()
    key = ("HYD-PUMP-001", "YYZ")
    default_context = store.part_context(*key)
    key_entries = [
        entry
        for entry in store._entries.values()
        if (entry.rec.part_number, entry.rec.current_location) == key
    ]
    policy_entry = next(entry for entry in key_entries if entry.rec.policy is not None)
    action_entry = next(entry for entry in key_entries if entry.rec.policy is None)
    assert action_entry.rec.calculation_evidence is not None

    context = store.part_context(
        *key,
        recommendation_id=action_entry.rec.recommendation_id,
    )

    assert context.proposed_policy is not None
    assert context.proposed_policy.rop == policy_entry.rec.policy.rop
    assert context.planning_trace.calculation_source == "served_calculation"
    assert (
        context.planning_trace.projected_demand
        == action_entry.rec.calculation_evidence.projected_demand
    )
    assert (
        context.planning_trace.projection_kind
        == action_entry.rec.calculation_evidence.projection_kind
    )
    assert context.repair_pipeline == default_context.repair_pipeline
    assert context.repair_return_profile == default_context.repair_return_profile


def test_selected_recommendation_unknown_or_wrong_key_is_non_enumerating_not_found():
    store = _store()
    key = ("HYD-PUMP-001", "YYZ")
    other = next(
        entry
        for entry in store._entries.values()
        if (entry.rec.part_number, entry.rec.current_location) != key
    )

    for recommendation_id in ("not-a-recommendation", other.rec.recommendation_id):
        with pytest.raises(RecommendationNotFound, match="HYD-PUMP-001/YYZ"):
            store.part_context(*key, recommendation_id=recommendation_id)


def test_legacy_trace_preserves_observed_empty_scheduled_demand_status():
    store = _store()
    key = ("HYD-PUMP-001", "YYZ")
    entry = next(
        entry
        for entry in store._entries.values()
        if (entry.rec.part_number, entry.rec.current_location) == key
    )
    entry.rec = entry.rec.model_copy(update={"calculation_evidence": None})

    class _ObservedEmptyInventory:
        def get_scheduled_demand(self, **_kwargs):
            return ()

        def get_scheduled_demand_status(self, **_kwargs):
            return "available"

    store.inventory_state = _ObservedEmptyInventory()

    context = store.part_context(
        *key,
        recommendation_id=entry.rec.recommendation_id,
    )

    assert context.planning_trace.calculation_source == "legacy_reconstructed"
    assert context.planning_trace.scheduled_demand_status == "available"
    assert context.planning_trace.scheduled_demand_due == 0
    assert not any(
        "Scheduled-demand evidence is unavailable" in warning
        for warning in context.planning_trace.warnings
    )


def test_part_context_total_24mo_excludes_first_year_but_keeps_full_chart_points():
    history = DemandHistory(
        tenant_id="acme",
        pn="P1",
        location="YYZ",
        observation_start=date(2023, 4, 16),
        observation_end=date(2026, 4, 16),
        bucket="month",
        event_count_source="observed",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2023, 5, 1),
                issues=50,
                removal_events=0,
                issue_events=1,
            ),
            DemandObservation(
                bucket="month",
                period_start=date(2024, 5, 1),
                issues=7,
                removal_events=0,
                issue_events=1,
            ),
        ],
        extract_date=date(2026, 4, 16),
    )

    class _FakeFs:
        def get_part_attributes(self, *, tenant, pn):
            return PartAttributes(
                tenant_id="acme",
                pn=pn,
                description="36-month part",
                extract_date=date(2026, 4, 16),
            )

        def get_demand_history(self, *, tenant, pn, location):
            return history

    store = PlannerStore(tenant_id="acme")
    store.fs = _FakeFs()
    store.tenant = TenantContext(tenant_id="acme")
    store.keys = [("P1", "YYZ")]

    context = store.part_context("P1", "YYZ")

    assert context.demand is not None
    assert context.demand.total_24mo == 7
    assert len(context.demand.points) == 2
    assert sum(point.total for point in context.demand.points) == 57
