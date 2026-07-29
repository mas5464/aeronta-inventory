"""Pure demand-history contract helpers shared by local and Glue ingestion."""

from __future__ import annotations

from datetime import date
from typing import Any

_DEMAND_DOMAINS = frozenset(
    {"demand_history_rotables", "demand_history_expendables"}
)


def demand_observation_window(
    manifest: dict[str, Any],
) -> tuple[date, date] | None:
    """Return the configured closed demand interval from successful artifact binds.

    Legacy manifests may omit ``bind_vars`` entirely; those return ``None`` so callers
    preserve an explicit unavailable window. Successful demand artifacts must agree.
    Mixing configured and unconfigured artifacts, omitting one boundary, reversing the
    interval, or supplying an invalid date fails loudly rather than inventing exposure.
    """

    windows: list[tuple[date, date]] = []
    unconfigured = 0
    for artifact in manifest.get("artifacts") or []:
        if (
            not isinstance(artifact, dict)
            or artifact.get("domain") not in _DEMAND_DOMAINS
            or artifact.get("status") != "succeeded"
        ):
            continue
        binds = artifact.get("bind_vars")
        if not isinstance(binds, dict) or not binds:
            unconfigured += 1
            continue
        raw_start = binds.get("from_date")
        raw_end = binds.get("to_date")
        if raw_start in (None, "") and raw_end in (None, ""):
            unconfigured += 1
            continue
        if raw_start in (None, "") or raw_end in (None, ""):
            raise ValueError(
                f"demand artifact {artifact.get('domain')!r} must bind both "
                "from_date and to_date"
            )
        try:
            start = date.fromisoformat(str(raw_start)[:10])
            end = date.fromisoformat(str(raw_end)[:10])
        except ValueError as exc:
            raise ValueError(
                f"demand artifact {artifact.get('domain')!r} has invalid date binds"
            ) from exc
        if end < start:
            raise ValueError(
                f"demand artifact {artifact.get('domain')!r} has observation_end "
                "before observation_start"
            )
        windows.append((start, end))

    if not windows:
        return None
    if unconfigured:
        raise ValueError(
            "successful demand artifacts disagree: configured and missing observation windows"
        )
    if len(set(windows)) != 1:
        raise ValueError("successful demand artifacts disagree on observation window")
    return windows[0]


__all__ = ["demand_observation_window"]
