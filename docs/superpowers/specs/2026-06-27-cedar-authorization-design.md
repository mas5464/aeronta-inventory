# Trax IO — Cedar Authorization for the Agent Spine (#4) — Design

**Date:** 2026-06-27
**Sub-project:** #4 Agent Spine (Cedar authorization slice)
**Status:** Design — approved in brainstorm, pending spec review → writing-plans
**Builds on:** [Agent Spine v1 design](2026-06-27-agent-spine-v1-design.md) · [ADR-0005](../../adr/2026-06-27-0005-deterministic-agent-spine-core.md). The deterministic `BandAutonomyPolicy` already implements the `AutonomyPolicy` Protocol; this slice adds the **Cedar-backed** implementation the design (§6.1) mandates, behind the same seam.

---

## 1. Context & goal

Agent Spine v1 ships a deterministic `BandAutonomyPolicy` behind an `AutonomyPolicy` Protocol (`guardrail/policy.py`), deliberately built so the design's Cedar authorization swaps in by DI. Design §6.1 specifies three Cedar-policy-enforced autonomy tiers; §6.2 specifies non-bypassable hard guardrails.

**Goal:** add a `CedarAutonomyPolicy` that implements `AutonomyPolicy.authorize(...)` via **declarative Cedar policy** (`cedarpy`, in-process, no AWS), so the auto-write-vs-queue decision is governed by auditable, tenant-customizable `.cedar` rules — injected into `GuardrailEnforcer` exactly where `BandAutonomyPolicy` plugs in today, with the enforcer and supervisor unchanged.

### Grounded `cedarpy` facts (verified, not assumed)
- `cedarpy` installs cleanly (Rust-backed, ~3.8 MiB) and runs in-process. API: `cedarpy.is_authorized(request: dict, policies: str, entities: list[dict]) -> AuthzResult`; `AuthzResult.decision ∈ {Decision.Allow, Decision.Deny, Decision.NoDecision}`.
- The `request` references entities by string id (`principal`/`action`/`resource`/`context`); the `entities` list must **declare the principal, action, and resource** entities (`{"uid": {"type","id"}, "attrs": {...}, "parents": []}`). A missing entity or a parse error yields `Decision.NoDecision`.
- **Cedar has no float/decimal type.** `delta_pct <= 0.40` is a parse error (`unexpected token 40`). Therefore `delta_pct` crosses the boundary as an **integer in basis points** (`delta_bps = round(delta_pct * 10000)`; 40% → 4000, the 100% cap → 10000) and the `.cedar` policies compare integer thresholds. Verified working: `autonomous_write` crit≥4 ∧ delta_bps≤4000 → `Allow`; tier-3, >40%, or a tighter bounded band → `Deny`.

---

## 2. Scope

### In scope
1. `cedar` optional extra (`cedarpy>=4.0`); core install stays dep-free (cedarpy lazy-imported).
2. `guardrail/cedar.py`: `CedarAuthorizer` (cedarpy wrapper) + `CedarAutonomyPolicy(AutonomyPolicy)`.
3. `guardrail/policies/autonomy_bands.cedar`: the §6.1 bands as Cedar `permit`/`forbid` policies (packaged default; the policy text is injectable for tenant customization).
4. Real-cedarpy unit tests (behind the `cedar` extra, `importorskip`) covering the band matrix + the wrapper.

### Out of scope (designed-for, deferred)
- Cedar **schema** validation (the `schema=` arg) — the policies are unit-tested directly.
- Per-tenant policy **loading** from S3/AgentCore config — v1 loads the packaged `.cedar` file or accepts injected policy text.
- The user/principal **identity** model (Cedar principals for human users, Cognito, etc.) — only the single agent principal matters for the autonomy decision here.
- Changing the default: `GuardrailEnforcer`/`Supervisor` keep `BandAutonomyPolicy` as the no-dep default; `CedarAutonomyPolicy` is opt-in via DI.

---

## 3. Components

### 3.1 `guardrail/cedar.py` — `CedarAuthorizer`
- **Purpose:** a thin, typed boundary over `cedarpy` so call sites never touch raw Cedar request/entity dicts.
- **Interface:** `CedarAuthorizer(policies: str)` with `is_allowed(self, *, action: str, resource_attrs: dict[str, int]) -> bool`. It builds the request (`principal = Agent::"spine"`, `action = Action::"<action>"`, `resource = PartLocation::"k"`), the three declared entities (agent, action, resource-with-attrs), calls `cedarpy.is_authorized`, and returns `decision == Decision.Allow`. cedarpy is imported lazily inside `__init__`/the call so the module imports without the `cedar` extra. `Decision.NoDecision` (a policy parse/eval error) maps to a raised `CedarPolicyError` — a misconfigured policy must fail loud, never silently authorize.

### 3.2 `guardrail/cedar.py` — `CedarAutonomyPolicy(AutonomyPolicy)`
- **Purpose:** the Cedar-backed `AutonomyPolicy`.
- **Interface:** `CedarAutonomyPolicy(policies: str | None = None)` (defaults to the packaged `autonomy_bands.cedar`). `authorize(self, *, tier, delta_pct, criticality_tier) -> GuardrailStatus`:
  - `ADVISOR` → `QUEUED_FOR_APPROVAL` (Tier A is always human; no Cedar call).
  - `BOUNDED` → action `bounded_write`; `AUTONOMOUS` → action `autonomous_write`.
  - `delta_bps = round(delta_pct * 10000)` (non-negative; delta_pct is already ≥0).
  - `CedarAuthorizer.is_allowed(action, {"criticality_tier": criticality_tier, "delta_bps": delta_bps})` → `True` → `APPROVED_FOR_WRITE`, else `QUEUED_FOR_APPROVAL`.
- **Depends on:** `AutonomyTier`, `GuardrailStatus`, `CedarAuthorizer`.

### 3.3 `guardrail/policies/autonomy_bands.cedar` — the §6.1 bands (chosen: faithful to design §6.1)
```cedar
// Tier C — autonomous: routine/consumable parts (essentiality 4–5), delta within ±40%.
permit(principal, action == Action::"autonomous_write", resource is PartLocation)
when { resource.criticality_tier >= 4 && resource.delta_bps <= 4000 };

// Tier B — bounded: non-flight-safety parts (essentiality 2–3 and below-cost), delta within ±15%.
permit(principal, action == Action::"bounded_write", resource is PartLocation)
when { resource.criticality_tier >= 2 && resource.delta_bps <= 1500 };

// §6.2 hard floor (declarative mirror of the code-level cap): never auto-write a >100% delta.
forbid(principal, action, resource is PartLocation)
when { resource.delta_bps > 10000 };
```
- Tier-1 (criticality_tier = 1, flight-safety) matches no `permit` → `Deny` → queued, on every action.
- `forbid` overrides any `permit` (Cedar semantics), so the §6.2 100% cap holds even if a band permit would otherwise match.

> **Note (intentional difference):** these §6.1-faithful bands differ from the deterministic `BandAutonomyPolicy` default (single criticality floor 4; ceilings 25%/100%). The two are valid, separately-tested implementations of the same `AutonomyPolicy` Protocol; `CedarAutonomyPolicy` is the production-faithful one. Aligning `BandAutonomyPolicy` to §6.1 is a possible later cleanup, out of scope here.

### 3.4 Hard floors stay in code (defense-in-depth)
`hard.hard_guardrail_violations` (delta > 100% → `REJECTED_HARD_GUARDRAIL`) is unchanged. The enforcer still runs it **before** `AutonomyPolicy.authorize`, so a §6.2 breach is *rejected* (not merely queued) regardless of which policy is wired. The Cedar `forbid` is the declarative mirror; the code floor owns the reject-vs-queue distinction Cedar cannot express here.

---

## 4. Data flow

```
GuardrailEnforcer.enforce(rec)   [unchanged]
  → hard_guardrail_violations  → if breach: REJECTED_HARD_GUARDRAIL
  → effective tier (AOG→ADVISOR)
  → AutonomyPolicy.authorize(tier, delta_pct, criticality_tier)
        └─ CedarAutonomyPolicy:  delta_bps = round(delta_pct*10000)
             action = {ADVISOR: ⟶queue, BOUNDED: bounded_write, AUTONOMOUS: autonomous_write}
             CedarAuthorizer.is_allowed(action, {criticality_tier, delta_bps})
               └─ cedarpy.is_authorized(request, autonomy_bands.cedar, [agent, action, resource])
                  Allow → APPROVED_FOR_WRITE ; Deny/no-permit → QUEUED_FOR_APPROVAL
```

Wiring: `GuardrailEnforcer(policy=CedarAutonomyPolicy())`. The CLI/supervisor gain no required dependency; Cedar is opt-in.

---

## 5. Testing

- **`CedarAuthorizer`**: a permit matches → `is_allowed` True; a non-matching request → False; a deliberately malformed policy → `CedarPolicyError` (not a silent allow).
- **`CedarAutonomyPolicy`** (the band matrix, real cedarpy): `ADVISOR` always queues; `AUTONOMOUS` crit-4 @ 20% → approved, crit-3 @ 20% → queued (criticality floor), crit-5 @ 60% → queued (band); `BOUNDED` crit-3 @ 10% → approved, crit-5 @ 20% → queued (tighter band); any tier @ >100% → queued (forbid). Assert the integer-bps boundary (e.g. exactly 40% / 4000 bps → approved).
- Gated with `pytest.importorskip("cedarpy")` and run with `--extra cedar`; core tests unaffected.
- Conventions mirror the package: `uv` + `pytest` + `ruff` (line-length 100, select E/F/I/B/UP/N/SIM), pydantic frozen where applicable, `pythonpath=["src"]`.
- Adversarial review of the policy + the float→bps boundary after build.

---

## 6. Risks

- **Float→bps rounding at band edges.** `round(delta_pct*10000)` could nudge a value across a threshold (e.g. 40.004% → 4000). Mitigation: document that the Cedar band is evaluated at bps granularity; tests pin the exact-boundary cases.
- **Policy parse errors silently authorizing.** Mitigation: `Decision.NoDecision` → raise `CedarPolicyError`; a malformed-policy test proves it raises rather than allows.
- **Drift between the Cedar bands and the deterministic default.** Accepted and documented (§3.3 note); they are independent Protocol implementations, each tested.
