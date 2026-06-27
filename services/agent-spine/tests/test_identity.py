import pytest
from trax_io_feature_store import TenantContext

from trax_io_spine.identity import MissingTenantScopeError, current_tenant, tenant_scope


def test_current_tenant_raises_outside_scope() -> None:
    with pytest.raises(MissingTenantScopeError):
        current_tenant()


def test_tenant_scope_sets_and_clears() -> None:
    ctx = TenantContext(tenant_id="acme")
    with tenant_scope(ctx):
        assert current_tenant() == ctx
    with pytest.raises(MissingTenantScopeError):
        current_tenant()


def test_nested_scopes_restore_outer() -> None:
    outer = TenantContext(tenant_id="acme")
    inner = TenantContext(tenant_id="other")
    with tenant_scope(outer):
        with tenant_scope(inner):
            assert current_tenant().tenant_id == "other"
        assert current_tenant().tenant_id == "acme"
