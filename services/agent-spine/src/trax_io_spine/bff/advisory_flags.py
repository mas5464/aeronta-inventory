"""Shared fail-closed tenant feature flag for planning and replay surfaces."""

from __future__ import annotations

from fastapi import Request


def advisory_enabled(request: Request, tenant_id: str) -> bool:
    configured = getattr(request.app.state, "planning_enabled_for", None)
    try:
        if callable(configured):
            return bool(configured(tenant_id))
        if isinstance(configured, dict):
            return configured.get(tenant_id) is True
        if isinstance(configured, (set, frozenset, list, tuple)):
            return tenant_id in configured
    except Exception:  # fail closed without exposing flag-provider internals
        return False
    return False


__all__ = ["advisory_enabled"]
