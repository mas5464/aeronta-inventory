# Forecasting #5 slice C — Empirical-Bayes compound-Poisson for `ULTRA_RARE`

**Date:** 2026-06-28
**Sub-project:** #5 Forecasting & Policy Engine — slice C
**Status:** Design approved
**Builds on:** slice A ([classical intermittent](2026-06-27-forecasting-classical-intermittent-design.md), [ADR-0006](../../adr/2026-06-27-0006-statistical-projector-behind-demandprojector.md)) · slice B ([gradient-boosted](2026-06-27-forecasting-gradient-boosted-design.md), [ADR-0009](../../adr/2026-06-27-0009-gradient-boosted-projector.md))

> Cross-refs: design doc [§5.1 regime router](../../design/2026-04-14-trax-io-inventory-optimizer-design.md), §5.2 (per-regime models), §5.4 (policy engine).

---

## 1. Problem & goal

The `ULTRA_RARE` regime — fewer than 6 removals in 24 months, or a new PN with under 90 days of history — is **60–75% of the catalog by count** (design §5.1). Per-part history is too sparse for a per-series fit (slices A/B): a part with 0–5 lifetime removals yields an unstable or zero rate, and a brand-new PN has none at all.

The design's champion for this regime (§5.2) is a **compound-Poisson model with empirical-Bayes (EB) priors from peer PNs** (same ATA chapter, criticality, fleet). EB borrows strength across similar parts: a per-group prior is estimated from the pool, and each part's sparse count is **shrunk toward the peer mean**. New parts inherit the peer-group rate until they accumulate their own history.

**Goal:** ship the EB compound-Poisson champion as a `DemandProjector` for `ULTRA_RARE`, pure `numpy`/`scipy` (both already dependencies), end-to-end verifiable now. The Chronos/Moirai zero-shot challenger and cross-tenant federated priors are explicitly **deferred** (see §7).

---

## 2. Background — the existing seam (verified)

- `DemandProjectorProtocol.project(*, context, regime) -> DemandProjection` is the single-part seam ([recommendation-engine `demand/projection.py`](../../../services/recommendation-engine/src/trax_io_reco/demand/projection.py)). The deterministic `HistoricalScheduledProjector` emits a `COMPOUND_POISSON` projection for `ULTRA_RARE`/`INTERMITTENT` (`lambda = historical_per_day`, `clump_p = 1.0`, `std = sqrt(lambda)`).
- Slices A (`StatisticalProjector`) and B (`GradientBoostedProjector`) each handle one target regime and **delegate every other regime to an injected fallback**, changing only the *source* of the rate while keeping the projection's shape field-for-field. Both are wired into #11 via the existing `RecommendationService(projector=…)` kwarg.
- Peer-grouping attributes exist on the context: `PartAttributes.ata_chapter`, `PartAttributes.part_class`, `Criticality.canonical_tier`, `PartAttributes.fleet_effectivity_tail_count` ([feature-store `schemas/features.py`](../../../services/feature-store/src/trax_io_feature_store/schemas/features.py)).
- **Tension:** EB priors need the *peer pool* (other PNs' demand), but `project(context, regime)` sees only one part. Resolved by an injected, pre-fit `PeerPriorProvider` (§4.2) — the seam is unchanged.

---

## 3. The empirical-Bayes model (Gamma–Poisson)

Removals are modeled as Poisson with a part-specific daily rate `λᵢ`. The conjugate prior is `Gamma(α, β)` (shape, rate). For a peer group:

- **Per-part observation:** `kᵢ` = removals over the basis window; `tᵢ` = exposure in basis days (default 730).
- **Prior fit (method-of-moments over peer per-day rates `rᵢ = kᵢ / tᵢ`):** with peer-rate sample mean `m` and variance `s²`,
  - if `s² > m / t̄` (overdispersion present, `t̄` = mean exposure): `β = m / (s² − m/t̄)`, `α = m · β`;
  - else (no excess dispersion → effectively Poisson): fall back to a high-confidence near-Poisson prior `α = m · β₀`, `β = β₀` with a large default `β₀` (configurable). Guards: `m = 0` → a minimal floor prior; never produce `α ≤ 0` or `β ≤ 0`.
  - MoM is deterministic and dependency-light (no optimizer); chosen over marginal MLE for reproducibility (the repo's determinism bar — see ADR-0009).
- **Posterior rate (shrinkage):** `λ̂ᵢ = (α + kᵢ) / (β + tᵢ)`. Sparse `kᵢ` ⇒ `λ̂ᵢ ≈ α/β` (peer mean); ample `kᵢ` ⇒ `λ̂ᵢ ≈ kᵢ/tᵢ` (own rate). New PN (`kᵢ = 0`, small `tᵢ`) ⇒ `λ̂ᵢ ≈ α/β`.
- **Posterior-predictive variance** (negative-binomial form) for next-period demand over a horizon: used to widen `std_per_day` beyond the Poisson `sqrt(λ̂)`, honoring the extra estimation uncertainty from sparse data. Reported per day consistent with the other projectors.

`eb.py` holds this math behind small pure functions: `fit_prior(peer_rates, exposures) -> GammaPrior`, `posterior_rate(prior, count, exposure) -> float`, `posterior_predictive_var_per_day(prior, count, exposure) -> float`.

---

## 4. Components

### 4.1 `eb.py` — Gamma-Poisson primitives
Pure `numpy`/`scipy`. A frozen `GammaPrior(alpha, beta)` dataclass + the three functions above. No I/O, no context types — trivially unit-testable and deterministic.

### 4.2 `peer_priors.py` — `PeerPriorProvider`
- `PeerPriorProvider.fit(corpus) -> PeerPriorProvider` where `corpus` is an iterable of `(PartAttributes, Criticality, DemandHistory)` (or a lean peer record) for the tenant's parts. Builds, for each grouping level, `group_key -> GammaPrior`:
  - **L0** `(ata_chapter, canonical_tier, part_class)`
  - **L1** `(ata_chapter, canonical_tier)`
  - **L2** `(canonical_tier,)`
  - **L3** global (tenant-wide)
- `get_prior(*, ata_chapter, canonical_tier, part_class) -> GammaPrior`: returns the **finest** level whose group has `≥ min_peers` (default 5); else the next coarser; L3 always exists. The chosen level is recorded for provenance/evidence.
- Within-tenant only. Cross-tenant federated peer medians (design §5.3) are deferred.

### 4.3 `eb_projector.py` — `EmpiricalBayesProjector`
- `__init__(self, provider, fallback=None, *, basis_window_days=730, min_peers=5)`. Default fallback = `HistoricalScheduledProjector`.
- `project(*, context, regime)`:
  - `regime is not Regime.ULTRA_RARE` → `fallback.project(...)`.
  - Look up the prior via `provider.get_prior(...)` from `context.part_attributes` + `context.criticality`. If attributes are missing or the provider was never fit → `fallback` (fail safe to deterministic).
  - `count` = `sum(removals + issues)` over `context.demand_history.observations`; `exposure` = `basis_window_days`.
  - `λ̂` = `posterior_rate(prior, count, exposure)` → per-day; add the scheduled-demand per-day component exactly as the other projectors (`by_aircraft`/`by_task` itemization preserved).
  - Emit `DemandProjection(dist_kind="COMPOUND_POISSON", dist_params={"lambda": λ̂_per_day, "clump_p": 1.0}, std_per_day=sqrt(posterior_predictive_var_per_day), historical_component=λ̂_per_day, scheduled_component=…, …)` — **identical in shape to the deterministic ultra_rare branch; only λ's source (EB-shrunken vs raw historical) and the widened std change.**

### 4.4 Backtest hook — `eb_next_rate`
A `eb_next_rate(values, prior)` that applies `posterior_rate` against a fixed prior, plugged into the existing MASE harness ([`backtest.py`](../../../services/forecasting/src/trax_io_forecasting/backtest.py)) like `gb_next_rate`. Documented caveat: EB's gain is cross-sectional shrinkage, so single-series backtesting holds the peer prior fixed and chiefly guards against regressions/NaNs rather than proving the shrinkage benefit (which is a portfolio-level property).

---

## 5. Data flow

```
batch of tenant parts ──► PeerPriorProvider.fit(corpus)   (pre-pass, once per batch)
                                   │  group→GammaPrior + backoff chain
                                   ▼
RecommendationService(projector=EmpiricalBayesProjector(provider, fallback))
   per (pn, location):
     regime == ULTRA_RARE ─► get_prior(attrs) ─► λ̂ = posterior_rate ─► COMPOUND_POISSON DemandProjection
     else ────────────────► fallback.project(...)
                                   ▼
                          #11 policy engine (unchanged)
```

The pre-pass is the caller's responsibility (the forecasting package exposes `fit` + the projector; a thin builder helper ties them). Supervisor wiring of the two-pass is a tracked follow-up, consistent with slices A/B which shipped the projector without changing the spine.

---

## 6. Error handling & determinism
- Provider never fit / unknown group → L3 global; L3 empty (no ultra-rare peers at all) → minimal floor prior so `project` never raises.
- All math guarded for `α,β > 0`, non-finite, and zero-exposure; `count = 0` is valid (new PN). No randomness — MoM closed-form ⇒ identical float output across processes (the repo's determinism bar; verify cross-process like ADR-0009).
- Missing `ata_chapter`/`part_class` → treated as a coarser group automatically by backoff.

---

## 7. Scope & deferrals
**In:** `eb.py`, `peer_priors.py`, `eb_projector.py`, `eb_next_rate`, unit + integration tests, an ADR.
**Deferred (documented in the ADR):**
- Chronos/Moirai zero-shot challenger (needs `torch` + foundation-model weights / SageMaker — the libomp-class heavy dep this repo defers until hostable).
- Cross-tenant **federated** peer priors (design §5.3) — within-tenant only in v1.
- Compound-clump (`clump_p`) estimation — stays `1.0` (single-unit), already a tracked #5 follow-up.
- Regime hysteresis re-classification (§5.1) — owned by the regime router, not the projector.
- Supervisor two-pass wiring (provider fit → inject) — follow-up, as with A/B.

## 8. Testing
- **`eb.py`:** MoM prior recovers a known Gamma under overdispersion; near-Poisson fallback when `s² ≤ m/t̄`; shrinkage monotonicity (`λ̂` between `α/β` and `kᵢ/tᵢ`); zero-count → peer mean; guards (`m=0`, non-finite) ; cross-process determinism (identical float bits).
- **`peer_priors.py`:** grouping at each level; backoff picks the finest group meeting `min_peers`, else coarsens; global always present; chosen-level recorded.
- **`eb_projector.py`:** non-ultra_rare and missing-attrs delegate to fallback; ultra_rare emits `COMPOUND_POISSON` with the shrunken λ; new-PN (0 history) → peer-mean λ; projection mirrors the deterministic ultra_rare shape field-for-field except λ/std (assert against `HistoricalScheduledProjector` output).
- **`eb_next_rate`:** slots into the MASE backtest without NaNs; deterministic.
- **integration:** `RecommendationService(projector=EmpiricalBayesProjector(...))` runs a batch end-to-end; ultra_rare keys get EB-shrunken policies, other regimes unchanged vs the deterministic projector.

## 9. Acceptance
All tests green; `ruff` clean; cross-process determinism shown; the ultra_rare projection proven to mirror the deterministic branch field-for-field except the (EB) λ and widened std; #11's existing suite unchanged.
