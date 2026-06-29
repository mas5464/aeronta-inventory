# ADR-0013: #5 slice C — empirical-Bayes compound-Poisson projector for `ULTRA_RARE`

**Date:** 2026-06-28
**Status:** Accepted
**Context project:** #5 Forecasting & Policy Engine (slice C)

## Context

Slices A ([ADR-0006](2026-06-27-0006-statistical-projector-behind-demandprojector.md)) and B ([ADR-0009](2026-06-27-0009-gradient-boosted-projector.md)) shipped intermittent-regime and moderate/high-volume forecasting behind the `DemandProjectorProtocol`. The remaining `ULTRA_RARE` regime — fewer than 6 removals in 24 months, or a new PN with under 90 days of history — accounts for **60–75% of the catalog by count** (design §5.1). Per-part history is too sparse for a per-series fit: a part with 0–5 lifetime removals yields an unstable or zero rate, and a brand-new PN has no history at all.

The design's champion for this regime (§5.2) is a **Gamma-Poisson empirical-Bayes (EB)** model: a conjugate prior is estimated from the peer group (same ATA chapter, canonical criticality tier, part class), and each part's sparse count is shrunk toward the peer mean. The prior-fit pass reads the tenant's demand corpus and is separate from the per-part `project(...)` call — a design tension since `project` sees only one part. This is resolved by an **injected, pre-fit `PeerPriorProvider`** (§4.2 of [the slice C spec](../superpowers/specs/2026-06-28-forecasting-empirical-bayes-ultra-rare-design.md)), leaving the `DemandProjectorProtocol` seam unchanged.

The spec and implementation plan are at:
- [docs/superpowers/specs/2026-06-28-forecasting-empirical-bayes-ultra-rare-design.md](../superpowers/specs/2026-06-28-forecasting-empirical-bayes-ultra-rare-design.md)
- [docs/superpowers/plans/2026-06-28-forecasting-empirical-bayes-ultra-rare.md](../superpowers/plans/2026-06-28-forecasting-empirical-bayes-ultra-rare.md) (implementation plan, 7 TDD tasks)

## Decision

Build slice C as an `EmpiricalBayesProjector` (`services/forecasting/`, `trax_io_forecasting`) implementing `DemandProjectorProtocol`, backed by a `PeerPriorProvider` holding pre-fit `GammaPrior` objects per coarsening level.

**EB math (`eb.py`):** pure `numpy`/`scipy` (both pre-existing dependencies). `GammaPrior(alpha, beta)` is a frozen dataclass. `fit_prior(rates, exposures)` uses **method-of-moments (MoM)** closed-form:
- if overdispersion `s² > m/t̄`: `β = m / (s² − m/t̄)`, `α = m · β`;
- else (near-Poisson): `α = m · β₀`, `β = β₀` (large configurable `β₀`).
Guards: `m = 0` → minimal floor prior; non-finite inputs rejected at construction. MoM is deterministic and dependency-light (no optimizer), consistent with ADR-0009's determinism bar.

Posterior shrinkage: `λ̂ = (α + count) / (β + exposure)`. Posterior-predictive variance uses the negative-binomial form `rate × (1 + 1/(β + exposure))` — always ≥ Poisson variance, widening `std_per_day` to reflect EB estimation uncertainty.

**Peer-group provider (`peer_priors.py`):** `PeerPriorProvider.fit(records)` builds `group_key → GammaPrior` at four coarsening levels:
- L0 `(ata_chapter, canonical_tier, part_class)`
- L1 `(ata_chapter, canonical_tier)`
- L2 `(canonical_tier,)`
- L3 global (tenant-wide; always present)

`get_prior(*, ata_chapter, canonical_tier, part_class)` returns the finest level with `≥ min_peers` (default 5), falling back to the next coarser level. `PeerPriorProvider.fit` excludes **zero-exposure records** from prior fitting (they carry no rate information and would create 0/0; see design decision below).

**Zero-exposure design decision (new-PN vs established-rare PN):** `peer_record_from_context` sets `exposure = 0.0` for a part whose `demand_history.observations` list is empty — i.e., a brand-new PN with no demand-history records at all. Its posterior `λ̂ = (α + 0) / (β + 0) = α/β` collapses exactly to the **peer-group mean**, which aligns precisely with design §5.1: *"new PN … inherit the peer-group rate."* An established-but-rare part (non-empty observations, even if all demand was zero) retains its full basis exposure (`basis_window_days`, default 730) and is genuinely shrunk *below* the peer mean — it has evidence that its demand rate is lower than the group average. The asymmetry is intentional: empty observations list = no history at all vs. an observed stretch of quietude. Consequence: `PeerPriorProvider.fit` must exclude zero-exposure records (they represent new PNs whose rates are not yet observable) so the prior is estimated only from parts with real exposure.

**Projector (`eb_projector.py`):** `EmpiricalBayesProjector(provider, fallback, *, basis_window_days=730)`. Non-`ULTRA_RARE` regimes, missing attributes, or an unfit provider all delegate to the fallback (default `HistoricalScheduledProjector`). The emitted `DemandProjection` is `COMPOUND_POISSON` with `clump_p=1.0` — **identical in shape to the deterministic `ULTRA_RARE` branch; only `lambda`'s source (EB-shrunken vs raw historical) and `std_per_day` (widened) change.** Policy engine sees no difference. All paths are wrapped in try/except — the projector never raises.

`build_eb_projector(contexts, fallback, *, basis_window_days, min_peers)` is a thin batch-builder helper that pre-passes the corpus, fits the provider, and returns a ready `EmpiricalBayesProjector`.

**Backtest hook (`backtest.py`):** `eb_rate_fn(prior)` wraps `posterior_rate` for the existing MASE harness (same pattern as `gb_next_rate` in slice B). Single-series backtesting holds the prior fixed — it validates against NaN/regression rather than proving the shrinkage benefit, which is a portfolio-level property.

The projector wires into `RecommendationService(projector=EmpiricalBayesProjector(...))` via the existing `projector=` kwarg — no change to `#11`.

## Consequences

**Positive**
- `ULTRA_RARE` keys (60–75% of catalog) now receive an EB-shrunken rate drawn from peer-group evidence rather than a raw historical mean that is often zero or meaningless on sparse data.
- Brand-new PNs **inherit the peer-group rate** via exposure=0 → posterior collapses to `α/β`; no manual "new part" special-case needed in the projector.
- Fully locally verifiable (pure `numpy`/`scipy`, no torch, no AWS, no SageMaker): **61 forecasting tests**, ruff clean.
- The `DemandProjectorProtocol` seam is unchanged; #11's 142 tests are untouched; the policy engine sees a structurally identical `COMPOUND_POISSON` projection.
- MoM closed-form ⇒ identical float bits across processes (determinism bar maintained; cross-process verified).
- Coarsening backoff L0→L3 degrades gracefully on thin catalogs — L3 global always resolves.

**Negative / deferred**
- **Within-tenant peer groups only.** Cross-tenant federated peer medians (design §5.3) would require cross-tenant data access; deferred to a future slice behind the provider seam.
- **`clump_p` stays 1.0** (single-unit demand); compound-clump estimation deferred as a tracked #5 follow-up (pre-dates this slice; unchanged from slices A/B).
- **Supervisor two-pass wiring** (fit → inject in the batch run pipeline) is a follow-up, consistent with slices A/B which shipped the projector without changing the spine.
- **Chronos/Moirai zero-shot challenger** (design §5.2 ensemble) needs `torch` + foundation-model weights / SageMaker hosting — the same heavy-dep category as LightGBM in slice B; deferred to slice D.
- **Regime hysteresis re-classification** (§5.1) is owned by the regime router, not this projector; deferred.
- The backtest MASE score for the EB model is a single-series regression guard only; portfolio-level shrinkage benefit is not quantified here.

## Alternatives considered

1. **Marginal MLE prior fit (numeric optimization).** Rejected: introduces a scipy optimizer dependency and non-determinism risk. MoM is closed-form, deterministic, and sufficient for v1 (consistent with the repo's determinism bar per ADR-0009).
2. **MAP point estimate without prior (raw historical rate).** This is exactly what `HistoricalScheduledProjector` already does. Rejected: produces zero or near-zero rates for most of the catalog; no peer-group borrowing; new PNs get zero rate.
3. **Chronos/Moirai zero-shot foundation model.** Explicitly in scope per the design but requires `torch` + foundation-model hosting (SageMaker); deferred. Slotting it in later is a pure addition behind the provider seam.
4. **Cross-tenant federated priors.** Design §5.3 calls for this as a premium feature; within-tenant only in v1. The `PeerPriorProvider` interface does not constrain the source of records, so a cross-tenant provider is a future drop-in.
