# ADR-0002: In-Memory FeatureStoreClient stub for Agent Spine

**Status:** Accepted
**Date:** 2026-04-14
**Deciders:** Miguel Sosa, AI Platform team, Data Platform team
**Related:** Sub-plan #2 (Feature Store), Sub-plan #4 (Agent Spine)

---

## Context

Sub-plan #4 (Agent Spine) and sub-plan #2 (Feature Store & Data Lake) are owned by different teams, on different stacks, with different release cadences. The Agent Spine is Python on AgentCore; the Feature Store is Glue/Iceberg/DynamoDB. They depend on each other: the Spine reads from the Feature Store on every recommendation; the Feature Store has nobody to read from it without the Spine.

Sequencing them serially (build Feature Store first, then Spine) burns 6 weeks. Building them in parallel needs a shared interface that both teams agree on and can develop against independently.

The same problem exists between the Spine and sub-plan #6 (eMRO Writeback REST), which is addressed separately in ADR-0003.

---

## Decision

The Agent Spine ships in v1 with a **`FeatureStoreClient` Protocol** (typed Python interface) and an **`InMemoryFeatureStore` reference implementation** that satisfies the Protocol. The Protocol is the contract; sub-plan #2 implements the production backend (Iceberg + DynamoDB) against the same Protocol; the Spine swaps backends at startup via dependency injection.

The Protocol lives in the Agent Spine repository (`src/trax_io/specialists/data_retrieval/feature_store.py`). Sub-plan #2 imports it. Both teams own the Protocol jointly; changes require sign-off from both.

---

## Considered options

### Option A — Shared Protocol + InMemory reference implementation (chosen)

The Spine defines `FeatureStoreClient` as a Python `Protocol`. The Spine ships an `InMemoryFeatureStore` for tests, dev, and shadow-mode pilots. Sub-plan #2 ships a `GlueIcebergFeatureStore` that conforms to the Protocol. Production wires the real implementation via DI; tests and integration runs use the in-memory fake.

**Pros**
- Spine team unblocked from day one; can build, test, ship without waiting for sub-plan #2.
- Sub-plan #2 has a precise, machine-checkable contract to implement against.
- Tests run hermetically — no LocalStack, no Glue catalog, no Iceberg.
- The contract is in code (a `Protocol`), so drift is caught by `mypy` rather than by integration failures.
- Lighthouse customer can pilot in shadow mode against the in-memory fake seeded with their extracted data, independently of whether sub-plan #2 has shipped.

**Cons**
- Two implementations to maintain (the InMemory fake and the production backend).
- Subtle behavioral differences between the fake and real backend can hide bugs (e.g., Iceberg time-travel vs. dict mutation semantics).
- The Spine team owns a piece of code (the InMemory fake) that the Data team will think of as "test infrastructure" — risk of neglect.

### Option B — Shared library, single canonical implementation

Both sub-plans depend on a separate `trax-io-feature-store` library that ships the only implementation (Iceberg + DynamoDB). Spine tests run against LocalStack + Iceberg.

**Pros**
- One implementation, one source of truth.
- Tests exercise production code paths.

**Cons**
- Spine work is blocked on Feature Store work landing.
- Test runs are slow (LocalStack startup, Iceberg writes, DynamoDB Local) — destroys TDD velocity.
- The library becomes a third codebase with its own release train.
- Lighthouse pilot cannot run before sub-plan #2 ships.

### Option C — Mocked feature store in tests, real backend in dev/prod

Spine tests use `unittest.mock`. Local dev and shadow pilots run against a deployed Feature Store in a sandbox AWS account.

**Pros**
- Smallest amount of test-only code.
- Fewest abstractions.

**Cons**
- Mocks drift from reality silently. Test passes; production breaks.
- Local dev requires AWS credentials and a sandbox Feature Store — kills new-engineer onboarding velocity.
- Shadow pilot blocked by sub-plan #2.
- "Mock the boundary" is a known antipattern that we have explicitly chosen to avoid for the eMRO Writeback boundary (see ADR-0003).

### Option D — Use the real Iceberg + DynamoDB inside the Spine repo

Vendor in the Iceberg + DynamoDB code into the Spine repo and let the Spine team own both. Sub-plan #2 becomes a deployment exercise.

**Pros**
- One team, one repo, one accountability surface.
- No protocol-drift risk.

**Cons**
- Violates the team-boundary principle. Data Platform owns data infrastructure; AI Platform owns agents.
- The Spine repo balloons in scope. Code review surface area triples.
- Deployment becomes coupled — can't ship a Spine fix without re-deploying the Feature Store.

---

## Decision rationale

Option A wins on three grounds.

**Independence of release trains.** Spine and Feature Store can ship at their own cadence. The Spine team can iterate on agent topology daily; the Data team can iterate on Iceberg partition strategy weekly. They synchronize at the Protocol boundary, not at every commit.

**Test velocity.** TDD discipline (which the Agent Spine plan enforces with the 85% coverage floor and per-task red-green-refactor cycle) requires sub-second test runs. LocalStack + Iceberg writes are 10–30 seconds per test. A fake `dict`-backed implementation runs in microseconds. This compounds — the Spine plan has 41 tasks × ~5 tests each = 200+ test files. The difference between sub-second and 10-second tests is the difference between a thriving codebase and a dying one.

**Lighthouse pilot decoupling.** The lighthouse customer can run the Spine against the InMemory fake seeded with their own nightly extract data while sub-plan #2 is still shipping. We learn product fit and model behavior weeks before the production data plane is ready.

The downside — fake/real drift — is mitigated by an integration test suite in sub-plan #2 that runs the *same* test scenarios against both the InMemory fake (executed in the Spine repo) and the production `GlueIcebergFeatureStore` (executed in the Feature Store repo), with the test suite shared as a pip-installable contract test package. If both pass, the implementations agree on observable behavior.

---

## Consequences

### Positive

- Agent Spine plan executes in parallel with Feature Store plan, saving ~6 weeks on the critical path.
- Spine tests stay fast; TDD velocity preserved.
- The Protocol becomes the integration contract — every change needs both teams to sign off, which forces explicit conversation about changes that would otherwise be invisible.
- Lighthouse pilot can begin before the production Feature Store ships.

### Negative

- The InMemory fake must be kept faithful to production semantics. Documented invariants:
 - All reads are tenant-scoped via `current_tenant()` — production enforces this via Cedar; the fake enforces it via `KeyError`.
 - Read-after-write within a tenant scope is consistent.
 - Cross-tenant reads raise `FeatureStoreLookupError`.
- The shared contract test suite (Sub-plan #2 Phase 6) is non-negotiable. Without it, the two implementations drift.

### Neutral

- The Spine repo carries ~150 lines of in-memory fake code permanently. It's also the cleanest documentation of the Feature Store's read surface — worth the cost.

---

## Verification

- Agent Spine plan Task 19 implements `FeatureStoreClient` Protocol + `InMemoryFeatureStore` with four tests covering tenant isolation and lookup semantics.
- Sub-plan #2 Phase 6 implements the shared contract test suite that runs the same scenarios against `InMemoryFeatureStore` and `GlueIcebergFeatureStore`. CI fails if either implementation drifts.
- The Protocol surface (method signatures, exceptions raised) is reviewed at the start of every quarter to identify deprecation candidates.
