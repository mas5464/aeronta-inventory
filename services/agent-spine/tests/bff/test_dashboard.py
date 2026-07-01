from datetime import UTC, datetime
from pathlib import Path

from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def _dashboard_reference(store: PlannerStore):
    """Re-implementation of the pre-optimization O(keys * entries) dashboard() body,
    kept only to prove the indexed version is byte-identical on the same store."""
    t = store.tenant
    from trax_io_spine.bff.models import Breakdown, DashboardSummary, PartShortfall
    from trax_io_spine.bff.store import _safe

    rows = []
    for pn, loc in store.keys:
        sp = _safe(
            lambda pn=pn, loc=loc: store.fs.get_stock_position(tenant=t, pn=pn, location=loc)
        )
        attrs = _safe(lambda pn=pn: store.fs.get_part_attributes(tenant=t, pn=pn))
        crit = _safe(lambda pn=pn: store.fs.get_criticality(tenant=t, pn=pn))
        ve = _safe(lambda pn=pn: store.fs.get_vendor_economics(tenant=t, pn=pn, vendor="DEFAULT"))
        e = next(
            (
                x
                for x in store._entries.values()
                if x.rec.part_number == pn and x.rec.current_location == loc
            ),
            None,
        )
        rec = e.rec if e else None
        rows.append(
            dict(
                pn=pn,
                loc=loc,
                on_hand=sp.on_hand if sp else 0,
                unit_cost=float(ve.unit_cost) if ve else 0.0,
                shortage=rec.shortage_quantity if rec else 0.0,
                demand=rec.projected_demand if rec else 0.0,
                aog=rec.aog_risk_level if rec else 0,
                cost=float(rec.estimated_cost_impact) if rec else 0.0,
                crit=crit.canonical_tier if crit else None,
                ata=attrs.ata_chapter if attrs else None,
                pclass=attrs.part_class if attrs else None,
                tier=e.outcome.tier if e else None,
                has_rec=rec is not None,
            )
        )

    def breakdown(field: str):
        groups: dict = {}
        for r in rows:
            k = r[field]
            if k is None:
                continue
            g = groups.setdefault(str(k), dict(count=0, on_hand=0, shortage=0.0))
            g["count"] += 1
            g["on_hand"] += r["on_hand"]
            g["shortage"] += r["shortage"]
        return tuple(
            Breakdown(key=k, count=g["count"], on_hand=g["on_hand"], shortage=g["shortage"])
            for k, g in sorted(groups.items())
        )

    shortfalls = [r for r in rows if r["shortage"] > 0]
    top = sorted(shortfalls, key=lambda r: r["shortage"], reverse=True)[:10]
    return DashboardSummary(
        parts=len(rows),
        total_on_hand=sum(r["on_hand"] for r in rows),
        total_on_hand_value=sum(r["on_hand"] * r["unit_cost"] for r in rows),
        total_shortage=sum(r["shortage"] for r in rows),
        total_projected_demand=sum(r["demand"] for r in rows),
        aog_exposure=sum(1 for r in rows if r["aog"] >= 3),
        open_recommendations=sum(1 for r in rows if r["has_rec"]),
        net_cost_impact=sum(r["cost"] for r in rows),
        by_criticality=breakdown("crit"),
        by_ata=breakdown("ata"),
        by_part_class=breakdown("pclass"),
        by_tier=breakdown("tier"),
        top_shortages=tuple(
            PartShortfall(
                pn=r["pn"],
                location=r["loc"],
                shortage=r["shortage"],
                on_hand=r["on_hand"],
                projected_demand=r["demand"],
            )
            for r in top
        ),
    )


def test_dashboard_aggregates_portfolio():
    store = _store()
    d = store.dashboard()
    assert d.parts == len(store.keys)  # portfolio-wide (all keys), not just recommendations
    assert d.total_on_hand >= 0
    assert d.total_on_hand_value >= 0
    assert d.total_shortage >= 0
    assert d.total_projected_demand >= 0
    assert d.aog_exposure >= 0
    assert d.open_recommendations >= 0
    assert isinstance(d.by_criticality, tuple)
    assert isinstance(d.by_ata, tuple)
    assert isinstance(d.by_part_class, tuple)
    assert isinstance(d.by_tier, tuple)
    # top_shortages sorted desc by shortage
    shorts = [s.shortage for s in d.top_shortages]
    assert shorts == sorted(shorts, reverse=True)


def test_dashboard_output_unchanged_by_indexed_lookup():
    """The dict-index optimization in dashboard() must be a pure complexity fix:
    byte-identical output vs. the original O(keys * entries) linear-scan
    implementation, on the same store."""
    store = _store()
    fast = store.dashboard()
    reference = _dashboard_reference(store)
    assert fast == reference
    # spot-check the fields called out in the task explicitly
    assert fast.parts == reference.parts
    assert fast.total_on_hand == reference.total_on_hand
    assert fast.total_on_hand_value == reference.total_on_hand_value
    assert fast.total_shortage == reference.total_shortage
    assert fast.total_projected_demand == reference.total_projected_demand
    assert fast.aog_exposure == reference.aog_exposure
    assert fast.open_recommendations == reference.open_recommendations
    assert fast.net_cost_impact == reference.net_cost_impact
    assert len(fast.by_criticality) == len(reference.by_criticality)
    assert len(fast.by_ata) == len(reference.by_ata)
    assert len(fast.by_part_class) == len(reference.by_part_class)
    assert len(fast.by_tier) == len(reference.by_tier)
    assert len(fast.top_shortages) == len(reference.top_shortages)


def test_dashboard_indexed_lookup_correct_with_duplicated_keys():
    """Sanity check that the by_key index is actually keyed on (pn, location) and
    stays correct when the store has more keys than entries (i.e. the index must
    be built from self._entries, not assumed 1:1 with self.keys)."""
    store = _store()
    # Duplicate the key list (as if the portfolio had more (pn, location) pairs
    # without matching recommendations) to exercise the has_rec=False path through
    # the index for keys absent from _entries.
    extra_keys = [(f"{pn}-DUP", loc) for pn, loc in store.keys]
    store.keys = list(store.keys) + extra_keys
    d = store.dashboard()
    assert d.parts == len(store.keys)
    # duplicated keys have no matching recommendation and no stock position, so they
    # contribute zero on_hand/shortage/demand/cost and should not appear in top_shortages
    dup_pns = {pn for pn, _ in extra_keys}
    assert not any(s.pn in dup_pns for s in d.top_shortages)
