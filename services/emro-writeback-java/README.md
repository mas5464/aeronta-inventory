# emro-writeback-java

Quarkus (Java 21) service for eMRO writeback. Group/artifact: `trax.io:emro-writeback-java`.

Part of the Trax IO monorepo. See the top-level [CLAUDE.md](../../CLAUDE.md) for
overall architecture; this module is the Java-side eMRO writeback facade referenced
there under "Target tech stack" / "Writeback".

## Build & test

```bash
cd services/emro-writeback-java
mvn test
```

The `%test` profile boots Quarkus with an **Oracle Dev Services** container
(`gvenzl/oracle-free:23-slim-faststart`, via Testcontainers) and a Dev Services
Kafka broker (Redpanda) for the configured `smallrye-kafka` channels — both are
disposable, automatically started/stopped by the test run, and require Docker
to be running locally. **First run pulls the Oracle image and can take several
minutes** — allow long timeouts (10+ min) the first time.

`quarkus.hibernate-orm.database.generation` is `drop-and-create` in `%test` and
`none` otherwise; Flyway (`quarkus.flyway.migrate-at-start`) is disabled in
`%test` and enabled elsewhere, and manages **only** the service-owned
`WRITEBACK_LEDGER` schema objects — never eMRO's own schema.

### Docker Desktop API-version note

`src/test/resources/docker-java.properties` is a **local, git-ignored
override** — it is not committed (see `.gitignore`), because pinning a Docker
API version is machine-specific and could break `mvn test` on another
developer's machine or CI runner with an older Docker Engine.

Create it yourself, only if needed, with exactly this content:

```properties
api.version=1.44
```

When you need it: Testcontainers/docker-java (as used by Quarkus Dev
Services) fails to negotiate an API version against **Docker Engine 29+**,
surfacing as a `400` from the daemon before any application code loads. This
happens because the docker-java version bundled with this Quarkus release
(Testcontainers 1.20.6) probes with an older default API version that
Engine 29+ rejects. Adding this file to `src/test/resources/` pins the
negotiated version and unblocks Dev Services.

Remove it once this module's Testcontainers version negotiates Engine 29+
correctly on its own (expected once the Quarkus platform BOM picks up
Testcontainers 2.x) — at that point the file becomes a no-op and can be
deleted.

### Test JWT public key placeholder

`src/test/resources/publicKey.pem` is an inert, throwaway RSA public key with
no corresponding private key anywhere. It exists only to satisfy SmallRye
Config's requirement that `mp.jwt.verify.publickey.location` (used by
`%test.mp.jwt.verify.publickey.location` in `application.properties`) be
non-empty — Quarkus validates this at CDI startup even though `HealthCheckTest`
hits an unauthenticated endpoint and never actually verifies a token against
it. Real JWT test tokens (signed against a matching key) will be introduced
via `quarkus-test-security-jwt` in later tasks once endpoints require
authentication.

## Dev mode

```bash
mvn quarkus:dev
```

## Smoke tests (real eMRO Oracle, opt-in only)

`EmroSchemaSmokeTest` validates this module's entity/SQL assumptions against a
REAL eMRO Oracle schema (e.g. the user's local `oracle19c`). It is plain JDBC
(`DriverManager` — no Quarkus boot, no JPA), tagged `emro-smoke`, and
belt-and-braces gated two ways: Surefire's `excludedGroups` (pom property `smoke.excludedGroups=emro-smoke` — a hardcoded config value would ignore CLI overrides; in this
module's `pom.xml`) excludes it from the default `mvn test` run, **and** it
carries `@EnabledIfEnvironmentVariable(named = "EMRO_SMOKE_DB_URL", matches =
".+")` so it self-skips even if group exclusion is overridden without setting
the env vars.

It never issues DDL. It reads `PN_MASTER`, `PROFILE_MASTER`,
`PN_INVENTORY_LEVEL_AUDIT`, `REQUISITION_HEADER`, `REQUISITION_DETAIL`,
`ORDER_HEADER`, `ORDER_DETAIL`, and `ALL_OBJECTS` (an existence-only check
for `PKG_APPLICATION_FUNCTION`, deliberately never invoking
`config_number()` — see the test's Javadoc for why) read-only, and does one
DML round-trip on the single designated `(EMRO_SMOKE_PN, EMRO_SMOKE_LOCATION)`
key in `PN_INVENTORY_LEVEL` — bump `REORDER_LEVEL` by 1, verify, then restore
the original value and verify the restore, all on one connection with
explicit commit points. If that key has no `PN_INVENTORY_LEVEL` row, the
test aborts (not fails) with an informative message instead of running DML.

Required environment variables:

- `EMRO_SMOKE_DB_URL` — JDBC URL, e.g. `jdbc:oracle:thin:@localhost:1521/XEPDB1`
- `EMRO_SMOKE_DB_USER` — DB user
- `EMRO_SMOKE_DB_PASSWORD` — DB password
- `EMRO_SMOKE_PN` — a real `PN` to probe in `PN_INVENTORY_LEVEL`
- `EMRO_SMOKE_LOCATION` — the matching `LOCATION`

To run it against a real target:

```bash
EMRO_SMOKE_DB_URL=jdbc:oracle:thin:@localhost:1521/<service> \
EMRO_SMOKE_DB_USER=<user> \
EMRO_SMOKE_DB_PASSWORD=<password> \
EMRO_SMOKE_PN=<test-pn> \
EMRO_SMOKE_LOCATION=<test-location> \
mvn test -Dgroups=emro-smoke -Dsmoke.excludedGroups= -Dnet.bytebuddy.experimental=true
```

A failure running this against a real target is a FINDING about the schema
assumptions, not a broken build — it never runs as part of the default
`mvn test`.

## Replay & run results

`GET /api/v1/runs/{runId}/results` (`writeback:read`) returns a top-level
JSON array of every `WRITEBACK_LEDGER` row for `runId`, scoped to the
caller's tenant (the `tenant_id` JWT claim, defaulting to `default` — same
rule as the PRD batch facades' write side), ordered oldest-first by
`createdAt` then `rowId`:

```json
[
  {
    "rowId": 1,
    "domain": "STOCK_LEVEL",
    "pn": "PN-1",
    "location": "LOC-1",
    "status": "WRITTEN",
    "createdRef": null,
    "version": 1,
    "parentVersion": null,
    "message": null,
    "createdAt": "2026-07-07T12:00:00Z"
  }
]
```

An unknown `runId` (or one with no rows for the caller's tenant) returns
`[]` with HTTP 200 — there is no distinct "run not found" signal.

**This is a thin, ledger-backed replay, deliberately not a full request
replay.** The ledger only records rows that were actually APPLIED — a real
write (`status: WRITTEN`) or a shadow write under an onboarding tenant
(`status: SHADOWED`). Rows a processor REJECTED (unknown PN/location, a
validation failure, etc.) or that errored before a ledger row could be
written are never ledgered, so this endpoint shows what happened for a run,
not the full original request. If a caller needs the complete original
request including rejected rows, it must keep its own copy — this service
does not retain one.

**Full re-drive of a run** (as opposed to reading back what happened) is a
Kafka-level operation, not something this endpoint does: replay the
`writeback-in` topic (`optimizer.writeback.v1`) for the relevant offsets,
bounded by topic retention, and reset the `emro-writeback-java` consumer
group's offset to before them. This is safe to do more than once, including
for rows that already succeeded — `WritebackConsumer` routes every message
through the same idempotency-keyed, effectively-once ledger write path (see
`WritebackLedger`'s Javadoc: unique on `(TENANT_ID, IDEMPOTENCY_KEY)`), so
re-processing an already-applied row resolves to `SKIPPED_DUPLICATE` rather
than a double-write.

## Known latency note — audit-PK collision retry (D17)

On real Oracle, `PN_INVENTORY_LEVEL_AUDIT`'s PK includes a second-precision `CREATED_DATE`, so two
writes to the same key by the same principal within one second collide. The stock-level writer
self-heals with a bounded retry (up to 2 retries, ≥1.1s backoff each so the timestamp advances) —
which means a rare synchronous REST apply can take up to ~2.2s extra before succeeding. Kafka-path
items absorb this invisibly. See ADR-0016 (D17).

## Hard rules

- **Never issue DDL against the eMRO schema.** This service's Flyway migrations
  own only its own writeback ledger tables — eMRO's schema is managed
  exclusively by the eMRO release train (see the root `CLAUDE.md`: "Writeback
  is the ONLY agent with eMRO write permission," and even writeback never runs
  DDL against eMRO).
- **Never manage the shared `oracle19c` Docker container** from this module —
  not via `docker rm`, `stop`, `restart`, `kill`, or volume/network operations,
  automated or manual. Dev Services container(s) started by `mvn test` are
  disposable and scoped to this module only; they are never `oracle19c`.
