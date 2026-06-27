"""Tenant context propagation via contextvars (task-local, async-safe).

The spine binds the canonical feature-store ``TenantContext`` for the duration of an
orchestration so every step runs under one tenant. Reading outside a scope raises.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from trax_io_feature_store import TenantContext


class MissingTenantScopeError(RuntimeError):
    """Raised when tenant context is read outside any ``tenant_scope``."""


_current: ContextVar[TenantContext | None] = ContextVar("trax_io_spine_tenant", default=None)


def current_tenant() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise MissingTenantScopeError(
            "no tenant bound; wrap the call site in `with tenant_scope(...)`"
        )
    return ctx


@contextmanager
def tenant_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
