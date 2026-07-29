"""Compatibility and provenance contracts for supply-cycle distributions."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from trax_io_feature_store.schemas import LeadTimeDistribution


def _legacy_payload() -> dict:
    return {
        "tenant_id": "acme",
        "pn": "P-1",
        "vendor": "DEFAULT",
        "condition": "NEW",
        "promised_lead_days": 21,
        "realized_mean_days": 25,
        "realized_p50_days": 24,
        "realized_p90_days": 35,
        "realized_p99_days": 40,
        "promised_vs_actual_delta_mean": 4,
        "n_observations": 8,
        "extract_date": "2026-04-01",
    }


def test_pre_phase3_snapshot_loads_with_explicit_unknown_provenance() -> None:
    distribution = LeadTimeDistribution.model_validate(_legacy_payload())

    assert distribution.evidence_status == "legacy_unknown"
    assert distribution.source == "legacy_unknown"
    assert distribution.grouping_level == "legacy_unknown"
    assert distribution.confidence == "unknown"
    assert distribution.data_cutoff is None
    assert distribution.model_version == "legacy-v0"
    assert distribution.classification_source == "legacy_unknown"
    assert distribution.proxy_definition is None


def test_closed_only_repair_observation_accepts_no_promised_value() -> None:
    distribution = LeadTimeDistribution(
        tenant_id="acme",
        pn="P-1",
        vendor="SHOP",
        condition="REP",
        realized_mean_days=30,
        realized_p50_days=30,
        realized_p90_days=40,
        realized_p99_days=45,
        n_observations=3,
        extract_date=date(2026, 4, 1),
        evidence_status="observed",
        source="order_plan_closed_orders",
        grouping_level="part_vendor_condition",
        confidence="low",
        data_cutoff=date(2026, 4, 1),
        model_version="supply-cycle-v1",
        proxy_definition="order_creation_to_last_receipt",
        classification_source="explicit_order_type",
    )

    assert distribution.promised_lead_days is None
    assert distribution.promised_vs_actual_delta_mean is None


def test_v2_observed_durations_are_sorted_and_reconcile_to_coverage() -> None:
    distribution = LeadTimeDistribution(
        tenant_id="acme",
        pn="P-1",
        vendor="SHOP",
        condition="REP",
        realized_mean_days=20,
        realized_p50_days=20,
        realized_p90_days=30,
        realized_p99_days=30,
        n_observations=3,
        observed_cycle_days=(10, 20, 30),
        extract_date=date(2026, 4, 1),
        evidence_status="observed",
        source="order_plan_closed_orders",
        grouping_level="part_vendor_condition",
        confidence="low",
        data_cutoff=date(2026, 4, 1),
        model_version="supply-cycle-v2",
        proxy_definition="order_creation_to_last_receipt",
        classification_source="explicit_order_type",
    )

    assert distribution.observed_cycle_days == (10, 20, 30)

    for durations in ((20, 10, 30), (10, 20)):
        with pytest.raises(
            ValidationError,
            match="durations must|requires every raw duration",
        ):
            LeadTimeDistribution.model_validate(
                distribution.model_dump()
                | {"observed_cycle_days": durations}
            )


def test_new_provenance_rejects_incoherent_observed_or_configured_rows() -> None:
    observed = {
        **_legacy_payload(),
        "evidence_status": "observed",
        "source": "pn_vendor_price",
        "grouping_level": "part_condition",
        "confidence": "low",
        "data_cutoff": "2026-04-01",
        "model_version": "supply-cycle-v1",
        "classification_source": "configured_condition",
    }
    with pytest.raises(ValidationError, match="closed orders"):
        LeadTimeDistribution.model_validate(observed)

    configured = {
        **_legacy_payload(),
        "condition": "REP",
        "evidence_status": "configured_fallback",
        "source": "pn_vendor_price",
        "grouping_level": "part_condition",
        "confidence": "low",
        "data_cutoff": "2026-04-01",
        "model_version": "supply-cycle-v1",
        "classification_source": "configured_condition",
        "n_observations": 0,
        "proxy_definition": None,
    }
    with pytest.raises(ValidationError, match="configured_repair_promise"):
        LeadTimeDistribution.model_validate(configured)


@pytest.mark.parametrize("evidence_status", ["observed", "configured_fallback"])
def test_new_evidence_rejects_implicit_legacy_model_version(
    evidence_status: str,
) -> None:
    payload = {
        **_legacy_payload(),
        "evidence_status": evidence_status,
        "source": (
            "order_plan_closed_orders"
            if evidence_status == "observed"
            else "pn_vendor_price"
        ),
        "grouping_level": "part_condition",
        "confidence": "low",
        "data_cutoff": "2026-04-01",
        "classification_source": (
            "explicit_order_type"
            if evidence_status == "observed"
            else "configured_condition"
        ),
        "n_observations": 1 if evidence_status == "observed" else 0,
    }

    with pytest.raises(ValidationError, match="non-legacy model"):
        LeadTimeDistribution.model_validate(payload)


def test_new_evidence_rejects_nonmonotonic_quantiles() -> None:
    payload = {
        **_legacy_payload(),
        "realized_p50_days": 30,
        "realized_p90_days": 20,
        "evidence_status": "observed",
        "source": "order_plan_closed_orders",
        "grouping_level": "part_condition",
        "confidence": "low",
        "data_cutoff": "2026-04-01",
        "model_version": "supply-cycle-v1",
        "classification_source": "explicit_order_type",
    }

    with pytest.raises(ValidationError, match="quantiles must be monotonic"):
        LeadTimeDistribution.model_validate(payload)
