"""Human-readable text for guardrail-pipeline reason codes (GuardrailOutcome.reasons).

These codes are internal plumbing — see guardrail/enforce.py (non_policy_recommendation),
guardrail/hard.py (delta_exceeds_100pct), and the recommendation engine's guardrail_flags
producers (delta_gt_100pct, active_aog, shelf_life_clamped, hazmat_tool_capped,
open_order_deferral) — never meant for display verbatim.
"""

from __future__ import annotations

_DROPPED = frozenset({"non_policy_recommendation"})

_DELTA_CODES = frozenset({"delta_exceeds_100pct", "delta_gt_100pct"})
_DELTA_MESSAGE = "Exceeds the 100% single-write cap — requires manual review."

_MESSAGES: dict[str, str] = {
    "active_aog": "An aircraft is currently AOG for this part — routed for immediate review.",
    "shelf_life_clamped": "Quantity capped to respect this part's shelf life.",
    "hazmat_tool_capped": "Increase capped — hazmat/tool-control items can only double per cycle.",
    "open_order_deferral": (
        "Deferred — on-hand stock plus incoming orders already cover the proposed level."
    ),
}


def _fallback(code: str) -> str:
    return code.replace("_", " ").title()


def humanize_guardrail_codes(codes: tuple[str, ...]) -> tuple[str, ...]:
    """Map raw guardrail reason codes to deduplicated, human-readable messages.

    `non_policy_recommendation` is dropped (already conveyed by the advisory state
    elsewhere in the UI). Both 100%-delta codes collapse to a single message. Any
    code outside the known set falls back to a title-cased rendering of the raw code.
    """
    seen: set[str] = set()
    messages: list[str] = []
    delta_emitted = False
    for code in codes:
        if code in _DROPPED:
            continue
        if code in _DELTA_CODES:
            if not delta_emitted:
                messages.append(_DELTA_MESSAGE)
                delta_emitted = True
            continue
        if code in seen:
            continue
        seen.add(code)
        messages.append(_MESSAGES.get(code, _fallback(code)))
    return tuple(messages)
