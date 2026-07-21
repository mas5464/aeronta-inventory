"""jobs.result column exists and round-trips JSON."""
import json


def test_jobs_result_roundtrip(admin_pool):
    tenant_id = "dddddddd-9999-9999-9999-dddddddd0c31"
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme-c3t1', 'A') "
            "on conflict (id) do nothing",
            (tenant_id,),
        )
        jid = conn.execute(
            "insert into jobs (tenant_id, kind, payload, result) "
            "values (%s, 'ingest', '{}', %s) returning id",
            (tenant_id, json.dumps({"keys": 42, "recommendations": 6})),
        ).fetchone()[0]
        conn.commit()
        got = conn.execute("select result from jobs where id = %s", (jid,)).fetchone()[0]
        assert got == {"keys": 42, "recommendations": 6}
