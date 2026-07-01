"""Slice S7 — Data & Connections / feed health: store method + BFF route.

Asserts the mapping is truthfully derived from the real 21-domain extract registry
(tools/nightly-extract/src/trax_io_extract/domains.py) and what
services/recommendation-engine/.../extract_loader.py actually consumes — not a
spec-shaped fiction. See trax_io_spine.bff.feeds module docstring for the per-feed
evidence this test asserts against.
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.feeds import FEED_DEFINITIONS, FEED_DEFINITIONS_BY_ID
from trax_io_spine.bff.models import FeedConnectionStatus, FeedId
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store() -> PlannerStore:
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def _client():
    store = _store()
    return TestClient(create_planner_app({"acme": store})), store


# --------------------------------------------------------------------------- #
# Mapping truthfulness — cross-checked directly against domains.py / extract_loader.py
# --------------------------------------------------------------------------- #
def test_feed_definitions_cover_all_13_canonical_feeds_exactly_once():
    assert len(FEED_DEFINITIONS) == 13
    ids = [d.feed_id for d in FEED_DEFINITIONS]
    assert len(set(ids)) == 13
    assert set(ids) == set(FeedId)


def test_repair_orders_is_not_connected():
    """No dedicated repair-shop-order domain exists among the 21 extracts; RepairTat
    is an explicit zero-value stub in the engine's own contracts (context.py)."""
    d = FEED_DEFINITIONS_BY_ID[FeedId.REPAIR_ORDERS]
    assert d.status is FeedConnectionStatus.NOT_CONNECTED
    assert d.domains == ()


def test_serial_tracking_is_not_connected():
    """No domain or feature-store schema anywhere tracks individual serials by
    status/location/time-since-overhaul (verified: no "serial" field/transform in
    extract_loader.py or the extract SQL beyond the unrelated PartSerializable
    part-class flag)."""
    d = FEED_DEFINITIONS_BY_ID[FeedId.SERIAL_TRACKING]
    assert d.status is FeedConnectionStatus.NOT_CONNECTED
    assert d.domains == ()


def test_reliability_maintenance_quotations_contracts_are_not_connected():
    for feed_id in (
        FeedId.RELIABILITY,
        FeedId.MAINTENANCE_SCHEDULE,
        FeedId.QUOTATIONS,
        FeedId.CONTRACTS,
    ):
        d = FEED_DEFINITIONS_BY_ID[feed_id]
        assert d.status is FeedConnectionStatus.NOT_CONNECTED, feed_id
        assert d.domains == (), feed_id


def test_inventory_is_connected_with_stock_domains():
    d = FEED_DEFINITIONS_BY_ID[FeedId.INVENTORY]
    assert d.status is FeedConnectionStatus.CONNECTED
    assert set(d.domains) == {"stock_amount", "stock_level_upload", "part_master"}


def test_purchase_orders_vendor_master_interchangeability_are_connected():
    po = FEED_DEFINITIONS_BY_ID[FeedId.PURCHASE_ORDERS]
    assert po.status is FeedConnectionStatus.CONNECTED
    assert set(po.domains) == {"order_plan", "order_plan_closed_orders"}

    vm = FEED_DEFINITIONS_BY_ID[FeedId.VENDOR_MASTER]
    assert vm.status is FeedConnectionStatus.CONNECTED
    assert set(vm.domains) == {"pn_vendor_price", "vendor"}
    assert "DEFAULT" in vm.notes  # honest caveat: vendors collapse to one canonical id

    ic = FEED_DEFINITIONS_BY_ID[FeedId.INTERCHANGEABILITY]
    assert ic.status is FeedConnectionStatus.CONNECTED
    assert set(ic.domains) == {"part_chain", "part_chain_details"}


def test_requisitions_is_partial_extracted_but_not_consumed():
    d = FEED_DEFINITIONS_BY_ID[FeedId.REQUISITIONS]
    assert d.status is FeedConnectionStatus.PARTIAL
    assert d.domains == ("order_plan_data_requisition",)
    assert "not" in d.notes.lower() and "consum" in d.notes.lower()


def test_fleet_utilization_is_partial_extracted_but_not_consumed():
    """causal_values (#1) is extracted every run but extract_loader.py never reads it
    into any schema — a real, verifiable gap, not a guess."""
    d = FEED_DEFINITIONS_BY_ID[FeedId.FLEET_UTILIZATION]
    assert d.status is FeedConnectionStatus.PARTIAL
    assert d.domains == ("causal_values",)


def test_shelf_life_is_partial_duration_only():
    d = FEED_DEFINITIONS_BY_ID[FeedId.SHELF_LIFE]
    assert d.status is FeedConnectionStatus.PARTIAL
    assert d.domains == ("part_master",)


def test_status_rollup_matches_task_prompt_ground_truth():
    connected = {d.feed_id for d in FEED_DEFINITIONS if d.status is FeedConnectionStatus.CONNECTED}
    partial = {d.feed_id for d in FEED_DEFINITIONS if d.status is FeedConnectionStatus.PARTIAL}
    not_connected = {
        d.feed_id for d in FEED_DEFINITIONS if d.status is FeedConnectionStatus.NOT_CONNECTED
    }
    assert connected == {
        FeedId.INVENTORY,
        FeedId.PURCHASE_ORDERS,
        FeedId.VENDOR_MASTER,
        FeedId.INTERCHANGEABILITY,
    }
    assert partial == {FeedId.REQUISITIONS, FeedId.SHELF_LIFE, FeedId.FLEET_UTILIZATION}
    assert not_connected == {
        FeedId.REPAIR_ORDERS,
        FeedId.SERIAL_TRACKING,
        FeedId.RELIABILITY,
        FeedId.MAINTENANCE_SCHEDULE,
        FeedId.QUOTATIONS,
        FeedId.CONTRACTS,
    }
    assert len(connected) + len(partial) + len(not_connected) == 13


def test_every_not_connected_feed_has_no_domains_and_a_nonempty_note():
    for d in FEED_DEFINITIONS:
        if d.status is FeedConnectionStatus.NOT_CONNECTED:
            assert d.domains == (), d.feed_id
        else:
            assert d.domains, d.feed_id  # connected/partial feeds always cite domains
        assert d.notes.strip(), d.feed_id  # every row has a real, non-empty caveat


# --------------------------------------------------------------------------- #
# Store method — derives from the REAL loaded extract (manifest + fs/keys)
# --------------------------------------------------------------------------- #
def test_feeds_summary_health_strip_counts_match_the_13_rows():
    store = _store()
    summary = store.feeds_summary()

    assert len(summary.feeds) == 13
    connected = sum(1 for r in summary.feeds if r.status is FeedConnectionStatus.CONNECTED)
    partial = sum(1 for r in summary.feeds if r.status is FeedConnectionStatus.PARTIAL)
    not_connected = sum(1 for r in summary.feeds if r.status is FeedConnectionStatus.NOT_CONNECTED)

    assert summary.health.connected == connected == 4
    assert summary.health.partial == partial == 3
    assert summary.health.not_connected == not_connected == 6


def test_feeds_summary_last_sync_comes_from_the_real_manifest_extract_date():
    """The sample extract's manifest.json carries extract_date "2026-04-01" and lists
    every one of its domains as status "succeeded" — every feed with at least one
    backing domain should report that date as last_sync; feeds with zero domains
    (not_connected) must not fabricate a sync date."""
    store = _store()
    summary = store.feeds_summary()

    assert summary.health.extract_date == "2026-04-01"
    for row in summary.feeds:
        if row.status is FeedConnectionStatus.NOT_CONNECTED:
            assert row.last_sync is None, row.feed_id
        else:
            assert row.last_sync == "2026-04-01", row.feed_id


def test_feeds_summary_rows_is_none_when_manifest_lacks_row_counts():
    """The committed sample manifest has no row_count per artifact — rows must be
    None (not fabricated from len(store.keys), which is a recommendation-engine key
    count, not a raw per-domain extract row count)."""
    store = _store()
    summary = store.feeds_summary()
    for row in summary.feeds:
        assert row.rows is None, row.feed_id


def test_feeds_summary_degrades_gracefully_with_no_manifest():
    """Simulates a trimmed/absent manifest.json (the task's explicit degrade
    requirement) — status/domains/notes must stay identical to the manifest-backed
    case; only rows/last_sync/extract_date degrade to None."""
    store = _store()
    store._manifest = {}  # simulate an extract dir with no manifest.json

    summary = store.feeds_summary()
    assert summary.health.extract_date is None
    assert summary.health.connected == 4
    assert summary.health.partial == 3
    assert summary.health.not_connected == 6
    for row in summary.feeds:
        assert row.rows is None
        assert row.last_sync is None
        # status/domains/notes are unaffected by manifest absence
        expected = FEED_DEFINITIONS_BY_ID[row.feed_id]
        assert row.status == expected.status
        assert row.domains == expected.domains
        assert row.notes == expected.notes


def test_feeds_summary_degrades_gracefully_with_corrupt_manifest_artifacts():
    """A manifest present but with a malformed/partial artifacts list must not crash
    the summary and must not claim a last_sync for domains it can't attest to."""
    store = _store()
    store._manifest = {"extract_date": "2026-04-01", "artifacts": "not-a-list"}

    summary = store.feeds_summary()
    assert summary.health.extract_date == "2026-04-01"
    for row in summary.feeds:
        assert row.last_sync is None  # no artifact attests to any domain having run


# --------------------------------------------------------------------------- #
# Route
# --------------------------------------------------------------------------- #
def test_get_feeds_route():
    client, store = _client()
    r = client.get("/v1/tenants/acme/feeds")
    assert r.status_code == 200
    body = r.json()
    assert "health" in body and "feeds" in body
    assert len(body["feeds"]) == 13
    assert body["health"]["connected"] == 4
    assert body["health"]["partial"] == 3
    assert body["health"]["not_connected"] == 6


def test_feeds_route_unknown_tenant_404():
    client, _ = _client()
    assert client.get("/v1/tenants/ghost/feeds").status_code == 404


def test_feeds_summary_matches_route_payload():
    client, store = _client()
    direct = store.feeds_summary()
    via_route = client.get("/v1/tenants/acme/feeds").json()
    assert via_route == direct.model_dump(mode="json")


def test_feeds_route_reflects_canonical_order():
    """Feeds render in the canonical FeedId order (DATA-MODEL.md §2 table order),
    not alphabetical or by status — matters for a stable table UI."""
    client, _ = _client()
    body = client.get("/v1/tenants/acme/feeds").json()
    assert [row["feed_id"] for row in body["feeds"]] == [f.value for f in FeedId]
