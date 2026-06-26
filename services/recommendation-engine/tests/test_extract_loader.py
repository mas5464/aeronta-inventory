"""Golden test: real-extract-shaped data -> a judge-able recommendation batch.

Proves the bridge (extract_loader) turns a nightly-extract output dir into the same kind of
batch the synthetic demo produces, with no AWS/Oracle/Spark — the shadow-mode dry run.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from trax_io_feature_store import TenantContext

from tests.fixtures.extract_fixture import write_sample_extract
from trax_io_reco.contracts.enums import RecommendationType
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.service import RecommendationService

NOW = datetime(2026, 4, 17, 9, 0, 0)


def _run(extract_dir):
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)
    batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
        tenant=TenantContext(tenant_id=tenant_id), keys=keys, now=NOW
    )
    return tenant_id, keys, batch


def test_extract_loader_produces_judgeable_batch(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    tenant_id, keys, batch = _run(extract_dir)

    assert tenant_id == "acme"
    assert set(keys) == {
        ("HYD-PUMP-001", "YYZ"), ("HYD-PUMP-001", "YOW"),
        ("FILTER-EXP-042", "YYZ"), ("VALVE-MOD-117", "YYZ"),
    }
    assert batch.skipped == ()  # every seeded key has complete required inputs
    assert batch.recommendations  # a non-empty, judge-able batch
    for r in batch.recommendations:
        assert r.description and r.reason and r.supporting_evidence
        assert 0.0 <= r.confidence_score <= 1.0


def test_extract_loader_transforms_are_correct(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _, _, batch = _run(extract_dir)
    by_part: dict[str, set] = {}
    for r in batch.recommendations:
        by_part.setdefault(r.part_number, set()).add(r.type)

    # Short rotable with an excess sibling at YOW -> transfer beats purchase.
    yyz_pump = {r.type for r in batch.recommendations
                if r.part_number == "HYD-PUMP-001" and r.current_location == "YYZ"}
    assert RecommendationType.TRANSFER in yyz_pump
    assert RecommendationType.PURCHASE not in yyz_pump

    # High-value (>$5k), zero-usage, deep-overstock expendable -> sell.
    assert RecommendationType.SELL in by_part.get("FILTER-EXP-042", set())

    # Busy part with a far-too-low current Max -> adjust min/max.
    assert RecommendationType.ADJUST_MIN_MAX in by_part.get("VALVE-MOD-117", set())


def test_extract_loader_is_deterministic(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _, _, b1 = _run(extract_dir)
    _, _, b2 = _run(extract_dir)
    strip = lambda b: [  # noqa: E731
        r.model_copy(update={"recommendation_id": "X"}).model_dump_json()
        for r in b.recommendations
    ]
    assert strip(b1) == strip(b2)


# --- review-fix regressions: edge-case robustness on messy real extract data --- #
def _write(extract_dir, domain, rows):
    (extract_dir / f"{domain}.json").write_text(json.dumps(rows))


def test_survives_nonfinite_values(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    # A single 'Infinity' qty and a 'NaN' price must NOT crash the load.
    _write(extract_dir, "stock_amount", [
        {"hostpartid": "BAD-1", "hostlocid": "YYZ", "onhandnew": "Infinity", "onhandbad": "0",
         "inrepair": "0", "allocated": "0", "rentalqty": "0", "loanqty": "0"},
    ])
    _write(extract_dir, "pn_vendor_price", [
        {"hostvendorlocid": "V", "hostpartid": "BAD-1", "price": "NaN", "processinglength": "21",
         "condition": "NEW", "preferred": "Y", "minoq": "1"},
    ])
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)  # no exception
    assert ("BAD-1", "YYZ") in keys


def test_missing_required_domain_raises(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    (extract_dir / "stock_amount.json").unlink()  # a failed/partial extract
    with pytest.raises(FileNotFoundError):
        build_stores_from_extract(extract_dir)


def test_tolerates_corrupt_optional_domain(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    (extract_dir / "location_master.json").write_text("{ this is not valid json ]")
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)  # no exception
    assert keys  # the run still produces a population


def test_parses_oracle_mmddyyyy_dates(tmp_path) -> None:
    # Oracle-native date format must NOT silently drop demand (the subtle one).
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(extract_dir, "demand_history_expendables", [
        {"hostpartid": "VALVE-MOD-117", "hostlocid": "YYZ",
         "historybegdate": f"0{m}/15/2025 08:30", "historyamount": "30",
         "transactiontype": "ISSUED"}
        for m in range(1, 10)
    ])
    fs, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    dh = fs.get_demand_history(tenant=TenantContext(tenant_id=tenant_id),
                               pn="VALVE-MOD-117", location="YYZ")
    assert dh.observations  # demand was parsed, not dropped
    assert sum(o.issues for o in dh.observations) == 270
