"""CedarAuthorizer — real cedarpy (skips without the `cedar` extra)."""

from __future__ import annotations

import pytest

pytest.importorskip("cedarpy")

from trax_io_spine.guardrail.cedar import CedarAuthorizer, CedarPolicyError  # noqa: E402

_POLICY = (
    'permit(principal, action == Action::"autonomous_write", resource is PartLocation)\n'
    "when { resource.criticality_tier >= 4 && resource.delta_bps <= 4000 };"
)


def test_permit_match_allows() -> None:
    a = CedarAuthorizer(_POLICY)
    assert a.is_allowed(
        action="autonomous_write", resource_attrs={"criticality_tier": 4, "delta_bps": 2000}
    ) is True


def test_non_match_denies() -> None:
    a = CedarAuthorizer(_POLICY)
    # criticality 3 fails the >= 4 floor -> no permit matches -> default deny
    assert a.is_allowed(
        action="autonomous_write", resource_attrs={"criticality_tier": 3, "delta_bps": 2000}
    ) is False


def test_unknown_action_denies() -> None:
    a = CedarAuthorizer(_POLICY)
    assert a.is_allowed(
        action="bounded_write", resource_attrs={"criticality_tier": 4, "delta_bps": 100}
    ) is False


def test_parse_error_raises_not_allows() -> None:
    # A float literal is a Cedar parse error -> is_authorized returns NoDecision -> must raise.
    bad = (
        'permit(principal, action, resource is PartLocation)\n'
        "when { resource.delta_pct <= 0.40 };"
    )
    a = CedarAuthorizer(bad)
    with pytest.raises(CedarPolicyError):
        a.is_allowed(
            action="autonomous_write", resource_attrs={"criticality_tier": 4, "delta_bps": 0}
        )
