# ADR-0003: fake_emro contract-testing strategy for the eMRO Writeback REST API

**Status:** Accepted
**Date:** 2026-04-14
**Deciders:** Miguel Sosa, AI Platform team, eMRO product team
**Related:** Sub-plan #4 (Agent Spine), Sub-plan #6 (eMRO Writeback REST API)

---

## Context

Sub-plan #4 (Agent Spine, Python on AgentCore) writes to `PN_INVENTORY_LEVEL` via a new REST API that lives inside eMRO (sub-plan #6, Java). The Spine team and the eMRO product team are organizationally distinct, on different release cadences, with different test conventions. The Spine cannot ship until the Writeback REST exists; the Writeback REST has no functional integration partner until the Spine ships.

This is the same independent-development problem ADR-0002 solves for the Feature Store boundary, but at a tougher boundary: the eMRO endpoint runs Java in a customer's Oracle-backed environment, not a swappable Python implementation. We cannot stand up "real eMRO" in the Spine's CI.

The classical solution — mock the HTTP client — is rejected because mock drift at the writeback boundary would silently corrupt customer data in production. Writeback is the highest-blast-radius integration surface in the system. We need stronger guarantees.

---

## Decision

Adopt **bidirectional contract testing**:

1. **OpenAPI 3.1 specification** for the eMRO Writeback REST API is authored jointly by the Spine and eMRO teams in `docs/contracts/2026-04-14-emro-writeback-rest-contract.yaml` and is the single source of truth.
2. **`fake_emro`** — a FastAPI mock of the contract — ships in the Spine repo (`tests/fixtures/fake_emro/`) and is the reference implementation that the Spine tests against. The Spine test suite exercises real HTTP against `fake_emro` in-process, not mocks.
3. **Contract test suite** — a shared `pytest` package — exercises both `fake_emro` and the real eMRO endpoint with the same scenarios. Every CI run, in both repos, runs the contract suite. If either implementation diverges from the other, CI fails.
4. **Schemathesis property-based tests** generate adversarial requests against the OpenAPI spec to find behavioral inconsistencies. Run nightly against both `fake_emro` and a deployed real-eMRO sandbox.

---

## Considered options

### Option A — Bidirectional contract testing with fake_emro + Schemathesis (chosen)

As above.

**Pros**
- Spine tests run against real HTTP semantics (status codes, idempotency keys, headers, error bodies) — far higher fidelity than `httpx` mocks.
- The contract is a versioned document both teams own; changes are reviewable, deployable, and auditable.
- Schemathesis catches edge cases neither team would think of (Unicode, integer overflow, malformed UUIDs).
- `fake_emro` becomes a self-documenting, machine-checked "what does the eMRO endpoint do?" reference for new engineers on either side.
- Pilot tenants can run the Spine against `fake_emro` for shadow-mode validation before sub-plan #6 ships.

**Cons**
- Two implementations (FastAPI mock + Java production) — risk of behavioral drift.
- Contract test suite is a third codebase that needs maintenance.
- The OpenAPI spec must stay in lockstep with both implementations; outdated specs cause silent failures.

### Option B — Mock the WritebackClient directly with `unittest.mock`

Spine tests stub `WritebackClient.upsert()` to return canned `WritebackResult` instances.

**Pros**
- Trivially fast tests.
- No HTTP infrastructure in tests.

**Cons**
- Mocks pass even when the real eMRO endpoint changes its contract.
- Status code handling, header propagation, retry behavior, idempotency-key semantics all untested.
- Production failures the day after sub-plan #6 ships its first breaking change.
- Categorically the wrong choice for a high-blast-radius integration boundary.

### Option C — Stand up real eMRO in CI

Run the actual eMRO Java app + Oracle in a Docker Compose stack for every Spine CI run.

**Pros**
- Tests against the real thing.
- No drift possible.

**Cons**
- 5–10 minute CI startup just for eMRO. Kills TDD velocity.
- Spine team takes on eMRO operational burden — wrong responsibility split.
- Customer-specific eMRO configurations are not represented; we get one slice of behavior, not the diversity production sees.
- License and image distribution complexity.

### Option D — Pact / consumer-driven contract testing

Spine team writes Pact consumer tests that generate "pacts" (machine-readable contract documents). eMRO team verifies their implementation against those pacts in their CI.

**Pros**
- Industry standard for this exact problem.
- Pact Broker provides a central verification dashboard.
- Good cross-language support (Python consumer ↔ Java provider).

**Cons**
- Adds a third infrastructure component (Pact Broker) that needs to be operated.
- The eMRO team's existing Java test infrastructure does not currently use Pact; adoption cost is high.
- Pact handles the "consumer expects X" direction well, but is weaker at "producer behavior matches OpenAPI" — for which Schemathesis is purpose-built.

---

## Decision rationale

Three reasons.

**Real HTTP, not mocks, at high-blast-radius boundaries.** The cost of a writeback bug — wrong inventory levels in a customer's eMRO — is enormous. The cost of a mocked writeback test passing while the real endpoint changes shape is exactly that bug. `fake_emro` is the cheapest way to exercise real HTTP semantics in fast tests.

**Symmetric contract enforcement.** A contract that only one side checks is a contract that drifts. Running the same `pytest` suite against `fake_emro` and the real eMRO endpoint forces both teams to converge on observable behavior, not on documentation.

**The OpenAPI spec is the artifact two teams can collaborate on without sharing a stack.** YAML is universal. Both Python and Java tooling generate clients and validators from it. Both teams can review changes in PRs. The OpenAPI spec is the social contract, the FastAPI mock is one concrete refinement of it, and the eMRO Java endpoint is the other.

The downside — drift between `fake_emro` and the real endpoint — is mitigated by the shared contract test suite running against both, in both repos, on every CI run. Drift is impossible to ignore.

---

## Consequences

### Positive

- Spine team unblocked from day one; can build, test, and ship without waiting for sub-plan #6.
- Lighthouse pilot can run Spine against `fake_emro` for shadow-mode validation before sub-plan #6 ships.
- Production breakages caused by silent contract drift are eliminated.
- `fake_emro` doubles as a developer-experience tool: customer engineering teams can run it locally to understand the Trax IO write surface.
- Schemathesis nightly runs catch entire categories of bugs neither team would write tests for.

### Negative

- Three artifacts to keep in sync: OpenAPI spec, FastAPI mock, Java endpoint. Mitigated by CI gates and a quarterly contract review.
- Initial setup cost: ~1 week for the OpenAPI spec, ~3 days for `fake_emro`, ~1 week for the contract test package, ~1 week for Schemathesis integration. ~3 person-weeks total. Recouped in the first month of avoided integration debugging.
- The eMRO Java team must add the contract test suite to their CI, which requires Python tooling on their build agents. One-time cost.

### Neutral

- We standardize on this pattern for every other Trax IO ↔ external boundary. Sub-plan #3 (Event Publisher) will adopt the same pattern (OpenAPI for the event endpoint, AsyncAPI for the event schema, fake event publisher in the Spine repo).

---

## Verification

- Agent Spine plan Task 26 ships `fake_emro` as a FastAPI app with smoke tests verifying the contract.
- Sub-plan #6 Phase N (TBD in that plan) runs the contract test suite against the deployed Java endpoint in the eMRO CI pipeline.
- The OpenAPI spec lives at `docs/contracts/2026-04-14-emro-writeback-rest-contract.yaml` and is reviewed in PRs by both teams.
- Schemathesis runs nightly against `fake_emro` and against a deployed sandbox eMRO endpoint; failures page the on-call from whichever team broke the contract.

---

## Open questions deferred

- **Should the Outbound Event Publisher (sub-plan #3) use the same pattern?** Yes — see the eMRO Event Publisher integration contract. Same shape: AsyncAPI spec + FastAPI mock + shared contract tests.
- **Should we publish the OpenAPI spec to customer environments?** Yes, for tier-1 carriers who want to write their own integrations against the writeback surface. v2 packaging decision.
