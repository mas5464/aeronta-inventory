"""Cedar-backed authorization for the spine's autonomy decision.

`cedarpy` is imported lazily so this module loads without the `cedar` extra. The autonomy
band policy lives in `policies/autonomy_bands.cedar`; this module turns a (tier, delta, criticality)
question into a Cedar `is_authorized` call and maps the decision to a GuardrailStatus.
"""

from __future__ import annotations

from importlib.resources import files

from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import GuardrailStatus

_PRINCIPAL_TYPE = "Agent"
_PRINCIPAL_ID = "spine"
_RESOURCE_TYPE = "PartLocation"
_RESOURCE_ID = "k"  # decision is attribute-based; the id is a fixed placeholder


class CedarPolicyError(RuntimeError):
    """Cedar returned no clear decision (policy parse/eval error). Never treated as allow."""


class CedarAuthorizer:
    """Thin typed boundary over ``cedarpy.is_authorized`` for one principal/resource shape."""

    def __init__(self, policies: str) -> None:
        self._policies = policies

    def is_allowed(self, *, action: str, resource_attrs: dict[str, int]) -> bool:
        import cedarpy

        request = {
            "principal": f'{_PRINCIPAL_TYPE}::"{_PRINCIPAL_ID}"',
            "action": f'Action::"{action}"',
            "resource": f'{_RESOURCE_TYPE}::"{_RESOURCE_ID}"',
            "context": {},
        }
        entities = [
            {"uid": {"type": _PRINCIPAL_TYPE, "id": _PRINCIPAL_ID}, "attrs": {}, "parents": []},
            {"uid": {"type": "Action", "id": action}, "attrs": {}, "parents": []},
            {
                "uid": {"type": _RESOURCE_TYPE, "id": _RESOURCE_ID},
                "attrs": dict(resource_attrs),
                "parents": [],
            },
        ]
        decision = cedarpy.is_authorized(request, self._policies, entities).decision
        if decision == cedarpy.Decision.Allow:
            return True
        if decision == cedarpy.Decision.Deny:
            return False
        raise CedarPolicyError(f"cedar returned {decision!r} (policy parse/eval error)")


_BPS_PER_UNIT = 10000
_TIER_ACTION = {
    AutonomyTier.BOUNDED: "bounded_write",
    AutonomyTier.AUTONOMOUS: "autonomous_write",
}


def _default_policy_text() -> str:
    return (
        files("trax_io_spine.guardrail")
        .joinpath("policies", "autonomy_bands.cedar")
        .read_text(encoding="utf-8")
    )


class CedarAutonomyPolicy:
    """`AutonomyPolicy` backed by the declarative `autonomy_bands.cedar` (design §6.1)."""

    def __init__(self, policies: str | None = None) -> None:
        policy_text = policies if policies is not None else _default_policy_text()
        self._authorizer = CedarAuthorizer(policy_text)

    def authorize(
        self, *, tier: AutonomyTier, delta_pct: float, criticality_tier: int
    ) -> GuardrailStatus:
        action = _TIER_ACTION.get(tier)
        if action is None:  # ADVISOR (Tier A) is always human approval
            return GuardrailStatus.QUEUED_FOR_APPROVAL
        delta_bps = round(delta_pct * _BPS_PER_UNIT)
        allowed = self._authorizer.is_allowed(
            action=action,
            resource_attrs={"criticality_tier": criticality_tier, "delta_bps": delta_bps},
        )
        return GuardrailStatus.APPROVED_FOR_WRITE if allowed else GuardrailStatus.QUEUED_FOR_APPROVAL  # noqa: E501
