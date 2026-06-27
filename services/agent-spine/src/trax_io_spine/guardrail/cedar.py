"""Cedar-backed authorization for the spine's autonomy decision.

`cedarpy` is imported lazily so this module loads without the `cedar` extra. The autonomy
band policy lives in `policies/autonomy_bands.cedar`; this module turns a (tier, delta, criticality)
question into a Cedar `is_authorized` call and maps the decision to a GuardrailStatus.
"""

from __future__ import annotations

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
