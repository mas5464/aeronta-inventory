# ADR-0009: #5 slice B — gradient-boosted projector (sklearn HGB) behind the DemandProjector seam

**Date:** 2026-06-27
**Status:** Accepted
**Context project:** #5 Forecasting & Policy Engine (slice B)

## Context

Slice A ([ADR-0006](2026-06-27-0006-statistical-projector-behind-demandprojector.md)) shipped intermittent-regime forecasting behind #11's `DemandProjectorProtocol`, delegating every other regime to the deterministic `HistoricalScheduledProjector`. Slice B fills the **`MODERATE`** (25–200 events/24mo) and **`HIGH_VOLUME`** (201+) regimes, where single-series signal is strongest and most demand volume lives.

The design (§5.2) names **LightGBM with causal covariates** for these regimes. Two grounded facts reshaped that for a v1, locally-verifiable slice:

1. **LightGBM is not self-contained on this platform.** Its py3.14 wheel installs, but the native lib fails to load without a system `libomp.dylib` (`brew install libomp`) — every dev/CI box would need it. scikit-learn's `HistGradientBoostingRegressor` (the same histogram-gradient-boosted-tree family) installs and fits cleanly on py3.14 with **zero system dependency** (verified, sklearn 1.8.0).
2. **The design's causal covariates are stubbed in v1.** `causal_utilization` (flight hours / cycles) is always `None`, and static covariates are constant within a single key — useless to a per-key model. The honest, trainable contribution is a **per-key autoregressive** model on the key's own demand series.

## Decision

Build slice B as a `GradientBoostedProjector` (`services/forecasting/`, `trax_io_forecasting`) implementing `DemandProjectorProtocol`, with **sklearn `HistGradientBoostingRegressor`** as the backend.

- **Per-key autoregressive.** `features.py` builds lag (most-recent-first) + rolling (mean/std/max) features from the existing gap-filled `PeriodSeries`; `gradient_boosted.gb_forecast` fits HGB and returns `(next-period mean, residual std)`, or `None` when history is too short.
- **Reuses the deterministic NORMAL contract field-for-field.** For `MODERATE`/`HIGH_VOLUME` the projector returns `dist_kind="NORMAL"`, `dist_params={"mean", "var"}`, `var_per_day = max(gb_per_day, residual_var_per_day)` (the same Poisson-ish floor the deterministic projector uses), with `scheduled_component`/`by_aircraft`/`by_task` computed identically — **only the historical mean's source changes** (GB next-period prediction vs the raw average). So the projection is structurally indistinguishable to the policy engine; it just gets a better mean. Same philosophy as slice A.
- **Delegation + cold-start.** Non-target regimes, and any history too short to train, delegate to the injected deterministic fallback.
- **Backtest hook.** `gb_next_rate` plugs into the existing `backtest_key`/MASE harness as a champion vs the historical-average challenger — no change to slice A's `backtest.py`.
- **Determinism.** Every model is constructed with `random_state=0`; HGB is deterministic given the seed (verified across separate interpreter processes — identical float bits). sklearn is **lazy-imported inside `gb_forecast`** so package import stays light (mirroring slice A's lazy statsforecast import).

## Consequences

**Positive**
- Moderate/high-volume keys get a gradient-boosted mean (and residual-based variance) with **zero policy-engine change** — the NORMAL projection is field-for-field identical to the deterministic one.
- Fully locally verifiable on py3.14, no system dependency, no AWS (34 forecasting tests; #11's path unchanged via the existing `projector=` kwarg).
- The seam is unchanged, so LightGBM, causal covariates, the global/federated model, and SageMaker are pure additions later.

**Negative / deferred**
- **Per-key autoregressive only** — no causal covariates (flight hours/cycles/wash rate) until `causal_utilization` is wired; no global cross-sectional / federated cross-tenant model.
- **LightGBM backend deferred** — recorded as an optional swap once `libomp` is available; the sklearn HGB is the default, verifiable-now backend.
- Trees can't extrapolate beyond seen targets (they track the recent **level**, not an unbounded trend); residual std uses population `np.std` (ddof=0), a mild variance under-estimate on small training sets, floored by `max(gb_per_day, …)`.
- SageMaker hosting, the foundation-model (Chronos) ensemble for high_volume, and the nightly champion/challenger 45-day auto-promotion gate remain in slices C/D.

## Alternatives considered

1. **LightGBM per the design + require `brew install libomp`.** Rejected for v1: adds a system dependency to every dev/CI machine and isn't verifiable here without it. sklearn HGB is the same model family, self-contained; LightGBM is a documented deferred backend.
2. **A global cross-sectional model with the design's causal covariates now.** Rejected: `causal_utilization` is stubbed and there's no real multi-key corpus locally — it would be covariate theater. Deferred until the inputs exist.
3. **Pure-numpy gradient boosting.** Rejected: re-implements a well-tested reference method for no gain; sklearn HGB is self-contained and reference-correct.
