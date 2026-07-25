"""preserve= keeps the audit ledger and the kill switch across a re-seed.

`seed_store` runs on a BYPASSRLS pool by contract (see its module docstring —
`trax_seed` in production; every existing seed test, e.g. `test_seed.py`,
calls it on `admin_pool`). `pg_pool` (role `trax_app`, NOBYPASSRLS) has only
SELECT on `tenants` and no DELETE on `writeback_ledger`/`part_keys`/
`part_contexts`/`tenant_snapshots`/`kill_switches` (see the RLS/grants in
`supabase/migrations/20260720000001_tenants_memberships.sql` and
`.../20260720000003_planner_lifecycle.sql`) — deliberately, so the request-time
role can never do what a seed/recompute pass does. Calling `seed_store` on
`pg_pool` fails immediately with `InsufficientPrivilege: permission denied for
table tenants` (confirmed by actually running it), so this file seeds on
`admin_pool` — matching every other seed test in this suite — and uses
`pg_admin_conn` only for out-of-band setup/assertions that are not the surface
under test.
"""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.store import PlannerStore
from trax_io_spine.pg.seed import seed_store

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="c5-preserve", extract_dir=str(_SAMPLE),
        now=datetime(2026, 4, 1, tzinfo=UTC))


def _counts(conn, tid):
    return {
        t: conn.execute(f"select count(*) from {t} where tenant_id=%s", (tid,)).fetchone()[0]
        for t in ("writeback_ledger", "kill_switches", "recommendations")
    }


def test_preserve_keeps_ledger_and_killswitch_but_replaces_queue(admin_pool, pg_admin_conn):
    report = seed_store(admin_pool, store=_store(), slug="c5-preserve", name="P")
    tid = report.tenant_uuid
    pg_admin_conn.execute(
        "insert into writeback_ledger (tenant_id,pn,location,version,entry,changed_at) "
        "values (%s,'P1','JFK',1,'{}'::jsonb,now())", (tid,))
    pg_admin_conn.execute(
        "insert into kill_switches (tenant_id,engaged) values (%s,true) "
        "on conflict (tenant_id) do update set engaged=true", (tid,))
    before = _counts(pg_admin_conn, tid)
    assert before["writeback_ledger"] == 1 and before["kill_switches"] == 1

    seed_store(admin_pool, store=_store(), slug="c5-preserve", name="P",
               preserve=frozenset({"writeback_ledger", "kill_switches"}))

    after = _counts(pg_admin_conn, tid)
    assert after["writeback_ledger"] == 1, "audit ledger must survive a recompute"
    assert after["kill_switches"] == 1, "kill switch must never be silently reset"
    assert after["recommendations"] == before["recommendations"], "queue is replaced, not doubled"

    # Row *count* surviving isn't enough on its own: a preserved kill switch
    # must keep its operator-set VALUE too. seed_store's final step used to be
    # an unconditional (non-upsert) INSERT — if that ran anyway for a
    # preserved-but-not-deleted kill_switches row, it would either raise a
    # duplicate-key error (tenant_id is the primary key) or, if changed to an
    # upsert, silently flip `engaged` back to the fresh store's default
    # (False) — exactly the "silent reset" this feature exists to prevent.
    engaged = pg_admin_conn.execute(
        "select engaged from kill_switches where tenant_id=%s", (tid,)
    ).fetchone()[0]
    assert engaged is True, "preserved kill switch must keep its True value, not just its row"


def test_default_still_full_replaces(admin_pool, pg_admin_conn):
    """Upload-ingest behavior is unchanged: no preserve => everything cleared."""
    report = seed_store(admin_pool, store=_store(), slug="c5-replace", name="R")
    tid = report.tenant_uuid
    pg_admin_conn.execute(
        "insert into writeback_ledger (tenant_id,pn,location,version,entry,changed_at) "
        "values (%s,'P1','JFK',1,'{}'::jsonb,now())", (tid,))
    seed_store(admin_pool, store=_store(), slug="c5-replace", name="R")
    assert _counts(pg_admin_conn, tid)["writeback_ledger"] == 0


def test_preserve_rejects_unknown_table_name(admin_pool):
    """A typo'd or never-seeded name in `preserve` must fail loudly, not no-op.

    `decisions` is a real table but is deliberately never in `_SEEDED_TABLES`
    (it's already preserved unconditionally) — asking to preserve it is a
    caller mistake worth surfacing, exactly like a plain typo would be.
    """
    with pytest.raises(ValueError, match="decisions"):
        seed_store(admin_pool, store=_store(), slug="c5-preserve-bad", name="B",
                    preserve=frozenset({"decisions"}))
