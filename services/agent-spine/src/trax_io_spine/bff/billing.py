"""C4 billing status + usage read.

`billing_summary` is a plain synchronous read over two RLS-protected tables
(`tenants`, `part_keys`) — see `tenants_select`/`part_keys_select` (C1
migrations 20260720000001/20260720000004). It takes any connection with the
tenant's `request.jwt.claims` GUC already set (a `tenant_conn(...)` checkout,
or a superuser/BYPASSRLS test connection) — callers on a bare pooled
connection with no claims set will see zero rows, not this tenant's row. See
bff/asgi.py's `_billing_reader` for the production wiring.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BillingSummary(BaseModel):
    plan_tier: str
    subscription_status: str | None
    key_quota: int
    keys_used: int
    current_period_end: datetime | None
    trial_ends_at: datetime | None


def billing_summary(conn, tenant_uuid: str) -> BillingSummary:
    row = conn.execute(
        "select plan_tier, subscription_status::text, key_quota, "
        "current_period_end, trial_ends_at from tenants where id = %s::uuid",
        (tenant_uuid,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown tenant {tenant_uuid}")
    used = conn.execute(
        "select count(*) from part_keys where tenant_id = %s::uuid", (tenant_uuid,)
    ).fetchone()[0]
    return BillingSummary(
        plan_tier=row[0],
        subscription_status=row[1],
        key_quota=row[2],
        keys_used=used,
        current_period_end=row[3],
        trial_ends_at=row[4],
    )
