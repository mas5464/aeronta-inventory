# Real eMRO Pipeline — Wave 1 Plan (prove the real path)

> Execute via superpowers:subagent-driven-development. Spec:
> docs/superpowers/specs/2026-07-01-real-emro-full-network-pipeline-design.md

**Goal:** fix the extract's blocking SQL bug, add a station+cap scope filter, then
run a REAL scoped extract from the local eMRO Oracle (`localhost:1521/LOCAL`, `ODB`)
through the existing engine and Docker deploy so **real YYZ parts render in the
web frontend**. Default projector (forecaster is Wave 2). No scale plumbing yet
(Wave 3).

## Global constraints
- Oracle container `oracle` (`localhost:1521/LOCAL`) is **read-only**: SELECTs
  only, **no DDL** (no temp tables), never stop/restart/remove it. Connection via
  `TRAX_ORACLE_HOST/PORT/SERVICE/USER/PASSWORD` env — **never commit secrets**.
  Local creds live in `~/OracleDataUpdate/config.py` (`ODB`), used only at runtime.
- Docker: project `trax-io-planner` only; single sequential build; never touch
  `oracle`/MySQL; no prune.
- Python ≥3.12, `uv` + `pytest` + `ruff` for `tools/nightly-extract`.
- Real-Oracle tests must **skip** when `TRAX_ORACLE_*` env is absent (so the
  normal suite/CI still passes offline).
- Extract output must stay the `<domain>.json` per-domain format the reco loader
  (`services/recommendation-engine/.../data/extract_loader.py`) already consumes.

## Task W1-1 — Fix the trailing-`;` bug + real-Oracle smoke test
**Files:** `tools/nightly-extract/src/trax_io_extract/oracle.py`,
`tools/nightly-extract/tests/test_oracle_execute.py` (new),
`tools/nightly-extract/tests/test_oracle_smoke.py` (new, env-gated).

- In `execute_domain` (oracle.py), strip a **single** trailing `;` (and trailing
  whitespace) from `sql_text` before `cursor.execute()`. `oracledb` rejects a lone
  statement terminated by `;` (`ORA-00933`). Do not strip internal semicolons or
  PL/SQL blocks — only one trailing `;` after `rstrip()`.
- Unit test (`test_oracle_execute.py`) with a `FakeCursor` capturing the executed
  text: assert the SQL passed to `execute()` has **no** trailing `;` for input
  `"SELECT 1 FROM DUAL ;"` and is otherwise unchanged; assert a query with no
  trailing `;` is passed through unchanged.
- Smoke test (`test_oracle_smoke.py`), `@pytest.mark.skipif` when
  `TRAX_ORACLE_HOST`/`_SERVICE`/`_USER`/`_PASSWORD` are not all set: build
  `OracleConnectionConfig.from_env()`, open a real connection, run
  `execute_domain` on `sql/05_location_master.sql` (a small, non-windowed domain),
  assert `row_count > 0` and each row has a `hostlocid` key. Read-only.
- Verify offline: `uv run --extra dev pytest` green (smoke skips). Then with
  `TRAX_ORACLE_*` exported to `localhost/1521/LOCAL/ODB`, the smoke test passes
  and returns real rows.

## Task W1-2 — Station + part-cap scope filter on the extract
**Files:** `tools/nightly-extract/src/trax_io_extract/domains.py` (add a
`scope_key` per domain), `tools/nightly-extract/src/trax_io_extract/scope.py`
(new — scope resolution + SQL wrapping), `oracle.py`/`runner.py` (apply the wrap
at execute), `tools/nightly-extract/src/trax_io_extract/cli.py` (new CLI flags),
tests alongside.

**Mechanism (generic, avoids editing 21 SQLs individually):** the reco loader
consumes lowercased output columns `hostpartid` and `hostlocid`. Wrap each
domain's SQL as `SELECT * FROM ( <original, ; stripped> ) traxscope WHERE …`
using a per-domain `scope_key`:
- `scope_key="part_location"` → `WHERE hostpartid IN (:parts…) AND hostlocid = :loc`
- `scope_key="part"` → `WHERE hostpartid IN (:parts…)`
- `scope_key="location"` → `WHERE hostlocid = :loc`
- `scope_key=None` → no wrap (small reference domains: `trans_code`,
  `location_type`, `part_criticality`, `location_master`, `vendor`).

Assign `scope_key` by inspecting each domain SQL's output aliases (the sample
JSON in `services/recommendation-engine/examples/extract_sample/` shows them:
e.g. `stock_amount`/`stock_level_upload`/`demand_history_*`/`part_location` expose
`hostpartid`+`hostlocid` → `part_location`; `part_master`/`pn_vendor_price` expose
`hostpartid` → `part`). Oracle IN-lists cap at 1000 — pass the scoped PN set via a
**bound array / `SELECT column_value FROM TABLE(:parts)`** or chunked OR-of-INs;
do NOT create a temp table (no DDL). For Wave 1 the cap keeps the set ≤ ~1000, so
a single bound IN-list is sufficient — enforce the cap and error if the resolved
scope exceeds the IN-list limit without chunking.

**Scope resolution (`scope.py`):** given a target location and a max-parts cap,
query `PN_INVENTORY_LEVEL` read-only for the planning-active PNs at that location
(`(NVL(REORDER_LEVEL,0)>0 OR NVL(MAXIMUM_STOCK,0)>0) AND LOCATION=:loc`), ordered
so the cap is deterministic (e.g. `ORDER BY PN`), `FETCH FIRST :cap ROWS ONLY`.
Return `(location, tuple(parts))`.

**CLI:** add `--scope-location YYZ` and `--scope-max-parts 500` to the `extract`
command; when set, resolve the scope and thread it into the domain execution so
every scoped domain is filtered. Absent → current unscoped behavior (unchanged).

**Tests:** unit-test the SQL wrapping (given `scope_key` + a fake scope, the
wrapped SQL contains the expected `WHERE` and the inner SQL has no trailing `;`);
unit-test `scope_key` is assigned for all 21 domains; env-gated real run is
covered in W1-3 (manual/ops), not a committed test.

## Task W1-3 — Run the real YYZ extract, seed, redeploy, verify (OPS — controller-run)
Not a code task; the controller runs it against the live DB + Docker.
1. Export `TRAX_ORACLE_*` for `localhost/1521/LOCAL/ODB` (from `~/OracleDataUpdate/config.py`).
2. `trax-io-extract extract --scope-location YYZ --scope-max-parts 500
   --output-dir <scratch>/emro_yyz …` → a real `<domain>.json` extract dir.
   Confirm `stock_amount.json`/`part_master.json` are non-empty and consistent
   (same PNs across domains).
3. `trax-io-reco run --extract-dir <that dir> --tenant acme` → confirm a
   `RecommendationBatch` with real YYZ parts + real policies.
4. Point the Docker BFF at the real extract dir (`EXTRACT_DIR`), `docker compose
   build bff ui && docker compose up -d` (single sequential build), verify
   `curl localhost:8088/v1/tenants/acme/dashboard` shows real part counts and the
   UI at :8088 renders real YYZ parts. Cap keeps it viewable without pagination.

## Done when
Real YYZ eMRO parts (with real stock/policy/recommendations) render in the Planner
UI at :8088, produced by the real extract→reco→BFF chain. `tools/nightly-extract`
suite green offline; smoke + real run verified against `LOCAL`. Then re-plan Wave 2.
