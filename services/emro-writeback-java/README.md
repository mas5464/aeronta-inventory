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

`src/test/resources/docker-java.properties` pins `api.version=1.44` for the
Testcontainers/docker-java client used by Quarkus Dev Services. Docker Engine
29+ requires API ≥ 1.44; the docker-java version bundled with this Quarkus
release (Testcontainers 1.20.6) probes with an older default and gets a
`400` from the daemon without this override. Safe to remove once the Quarkus
platform BOM picks up Testcontainers 2.x.

## Dev mode

```bash
mvn quarkus:dev
```

## Smoke tests (real eMRO Oracle, opt-in only)

Smoke tests are tagged `emro-smoke` and excluded from the default `mvn test`
run (`excludedGroups` in the Surefire config). To run them against a real
target:

```bash
EMRO_SMOKE_DB_URL=<jdbc-url> mvn test -Dgroups=emro-smoke -DexcludedGroups=
```

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
