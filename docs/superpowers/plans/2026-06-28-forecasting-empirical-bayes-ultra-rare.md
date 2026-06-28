# Forecasting #5 slice C — Empirical-Bayes `ULTRA_RARE` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an empirical-Bayes (Gamma-Poisson) compound-Poisson `DemandProjector` for the `ULTRA_RARE` regime that shrinks each part's sparse removal count toward a peer-group prior.

**Architecture:** Three new pure modules in `services/forecasting/` behind the existing single-part `DemandProjectorProtocol` seam. `eb.py` holds the Gamma-Poisson math; `peer_priors.py` fits per-group priors with coarsening backoff; `eb_projector.py` is the projector (handles `ULTRA_RARE`, delegates the rest to a fallback). A peer-prior provider is fit in a batch pre-pass and injected. Mirrors slices A/B field-for-field — only λ's source changes (EB-shrunken) plus a widened std.

**Tech Stack:** Python ≥3.12, `numpy` (already a dep), `pytest`, `ruff`. No new dependencies. `scipy` is available but not required (closed-form math).

**Spec:** [docs/superpowers/specs/2026-06-28-forecasting-empirical-bayes-ultra-rare-design.md](../specs/2026-06-28-forecasting-empirical-bayes-ultra-rare-design.md)

## Global Constraints

- Python ≥3.12; `line-length = 100`; ruff lint select `["E","F","I","B","UP","N","SIM"]`.
- No new runtime dependencies (numpy only; scipy optional).
- **Determinism:** closed-form method-of-moments only — no optimizer, no RNG. Identical float output across processes.
- All public functions guard against non-finite values and never raise on valid-but-sparse input (`count = 0` is valid: a new PN). `project` never raises — fail safe to the fallback.
- Run tests with `cd services/forecasting && uv run --extra dev pytest`; lint with `uv run --extra dev ruff check .`.
- Commit messages prefixed `#5`; end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- Create `src/trax_io_forecasting/eb.py` — `GammaPrior` dataclass + `fit_prior` + `posterior_rate` + `posterior_predictive_var` (pure math).
- Create `src/trax_io_forecasting/peer_priors.py` — `PeerRecord`, `PeerPriorProvider` (fit + backoff `get_prior`), `peer_record_from_context`.
- Create `src/trax_io_forecasting/eb_projector.py` — `EmpiricalBayesProjector` + `build_eb_projector`.
- Modify `src/trax_io_forecasting/backtest.py` — add `eb_rate_fn`.
- Tests: `tests/test_eb.py`, `tests/test_peer_priors.py`, `tests/test_eb_projector.py`, `tests/test_eb_backtest.py`, `tests/test_eb_integration.py`.

---

### Task 1: Gamma-Poisson posterior math (`eb.py`)

**Files:**
- Create: `src/trax_io_forecasting/eb.py`
- Test: `tests/test_eb.py`

**Interfaces:**
- Produces:
  - `GammaPrior` — frozen dataclass `(alpha: float, beta: float)`; property `mean -> float` (`alpha/beta`).
  - `posterior_rate(prior: GammaPrior, count: float, exposure: float) -> float` = `(alpha + count) / (beta + exposure)`.
  - `posterior_predictive_var(prior: GammaPrior, count: float, exposure: float) -> float` = `λ̂ * (1 + 1/(beta + exposure))` where `λ̂ = posterior_rate(...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eb.py
import math
from trax_io_forecasting.eb import GammaPrior, posterior_predictive_var, posterior_rate


def test_posterior_rate_shrinks_between_prior_mean_and_own_rate():
    prior = GammaPrior(alpha=2.0, beta=100.0)  # prior mean 0.02/day
    # sparse part: 1 event over 730 days (own rate ~0.00137)
    lam = posterior_rate(prior, count=1.0, exposure=730.0)
    assert 0.00137 < lam < 0.02  # pulled up toward the peer mean

def test_posterior_rate_zero_count_returns_prior_mean():
    prior = GammaPrior(alpha=4.0, beta=200.0)  # mean 0.02
    lam = posterior_rate(prior, count=0.0, exposure=0.0)
    assert lam == prior.mean == 0.02

def test_posterior_rate_ample_count_approaches_own_rate():
    prior = GammaPrior(alpha=2.0, beta=100.0)
    lam = posterior_rate(prior, count=1000.0, exposure=1000.0)
    assert abs(lam - 1.0) < 0.01  # own rate 1.0 dominates the weak prior

def test_posterior_predictive_var_exceeds_poisson_for_sparse_data():
    prior = GammaPrior(alpha=2.0, beta=10.0)
    lam = posterior_rate(prior, count=0.0, exposure=0.0)
    var = posterior_predictive_var(prior, count=0.0, exposure=0.0)
    assert var > lam  # wider than Poisson (var == mean) due to estimation uncertainty
    assert math.isfinite(var)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb.py -v`
Expected: FAIL — `ModuleNotFoundError: trax_io_forecasting.eb`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/trax_io_forecasting/eb.py
"""Gamma-Poisson empirical-Bayes primitives (closed form, deterministic)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GammaPrior:
    """Conjugate prior for a Poisson rate: shape alpha, rate beta (mean = alpha/beta)."""

    alpha: float
    beta: float

    @property
    def mean(self) -> float:
        return self.alpha / self.beta


def posterior_rate(prior: GammaPrior, count: float, exposure: float) -> float:
    """Posterior mean daily rate after observing `count` events over `exposure` time."""
    return (prior.alpha + count) / (prior.beta + exposure)


def posterior_predictive_var(prior: GammaPrior, count: float, exposure: float) -> float:
    """Per-unit posterior-predictive variance (negative-binomial): >= Poisson var (= mean)."""
    lam = posterior_rate(prior, count, exposure)
    return lam * (1.0 + 1.0 / (prior.beta + exposure))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add services/forecasting/src/trax_io_forecasting/eb.py services/forecasting/tests/test_eb.py
git commit -m "#5 forecasting slice C: Gamma-Poisson posterior math (eb.py)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Method-of-moments prior fitting (`eb.py`)

**Files:**
- Modify: `src/trax_io_forecasting/eb.py`
- Test: `tests/test_eb.py`

**Interfaces:**
- Consumes: `GammaPrior` (Task 1).
- Produces: `fit_prior(rates: Sequence[float], exposures: Sequence[float]) -> GammaPrior`.
  - `m = mean(rates)`, `t_bar = mean(exposures)`.
  - If `len(rates) >= 2`: `excess = var(rates, ddof=1) - m / t_bar`. If `excess > 0 and m > 0`: `beta = m / excess`, `alpha = m * beta`.
  - Otherwise (too few peers, or no overdispersion): **near-Poisson fallback** — `beta = t_bar`, `alpha = max(m, EPS) * t_bar` (prior as informative as one average peer's exposure; unit-correct, no arbitrary constant).
  - Guards: `t_bar <= 0` or empty → `GammaPrior(EPS, 1.0)`; clamp `alpha, beta` to small positive finite. `EPS = 1e-9`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eb.py  (append)
from trax_io_forecasting.eb import fit_prior


def test_fit_prior_recovers_mean_under_overdispersion():
    # rates spread well beyond Poisson sampling noise -> a real Gamma prior
    rates = [0.01, 0.05, 0.0, 0.08, 0.03, 0.12]
    exposures = [730.0] * len(rates)
    prior = fit_prior(rates, exposures)
    assert abs(prior.mean - (sum(rates) / len(rates))) < 1e-9
    assert prior.alpha > 0.0 and prior.beta > 0.0

def test_fit_prior_near_poisson_fallback_when_no_overdispersion():
    # identical rates -> zero sample variance -> excess <= 0 -> fallback beta = t_bar
    rates = [0.02, 0.02, 0.02, 0.02]
    exposures = [730.0] * 4
    prior = fit_prior(rates, exposures)
    assert prior.beta == 730.0
    assert abs(prior.mean - 0.02) < 1e-9

def test_fit_prior_single_peer_uses_fallback():
    prior = fit_prior([0.05], [365.0])
    assert prior.beta == 365.0
    assert abs(prior.mean - 0.05) < 1e-9

def test_fit_prior_empty_returns_floor():
    prior = fit_prior([], [])
    assert prior.alpha > 0.0 and prior.beta > 0.0

def test_fit_prior_deterministic():
    rates, exp = [0.01, 0.05, 0.0, 0.08], [730.0] * 4
    assert fit_prior(rates, exp) == fit_prior(rates, exp)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb.py -k fit_prior -v`
Expected: FAIL — `ImportError: cannot import name 'fit_prior'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/trax_io_forecasting/eb.py  — add import + function
from collections.abc import Sequence

_EPS = 1e-9


def fit_prior(rates: Sequence[float], exposures: Sequence[float]) -> GammaPrior:
    """Method-of-moments Gamma prior from peer per-unit rates + exposures (closed form)."""
    rs = [float(r) for r in rates]
    ts = [float(t) for t in exposures if float(t) > 0.0]
    if not rs or not ts:
        return GammaPrior(alpha=_EPS, beta=1.0)
    m = sum(rs) / len(rs)
    t_bar = sum(ts) / len(ts)
    if len(rs) >= 2 and m > 0.0:
        mean_r = m
        var_r = sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1)
        excess = var_r - m / t_bar
        if excess > 0.0:
            beta = m / excess
            alpha = m * beta
            return GammaPrior(alpha=max(alpha, _EPS), beta=max(beta, _EPS))
    # near-Poisson fallback: prior as informative as one average peer's exposure
    return GammaPrior(alpha=max(m, _EPS) * t_bar, beta=t_bar)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb.py -v`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add services/forecasting/src/trax_io_forecasting/eb.py services/forecasting/tests/test_eb.py
git commit -m "#5 forecasting slice C: method-of-moments Gamma prior fit

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Peer-prior provider with backoff (`peer_priors.py`)

**Files:**
- Create: `src/trax_io_forecasting/peer_priors.py`
- Test: `tests/test_peer_priors.py`

**Interfaces:**
- Consumes: `GammaPrior`, `fit_prior` (Tasks 1–2); `PartLocationContext` (read-only).
- Produces:
  - `PeerRecord` — frozen dataclass `(ata_chapter: str | None, canonical_tier: int, part_class: str | None, count: float, exposure: float)`.
  - `peer_record_from_context(context, *, basis_window_days: int = 730) -> PeerRecord` — `count = sum(removals + issues)` over `context.demand_history.observations`; `exposure = basis_window_days`.
  - `PeerPriorProvider` with `@classmethod fit(records: Iterable[PeerRecord], *, min_peers: int = 5) -> PeerPriorProvider` and `get_prior(*, ata_chapter, canonical_tier, part_class) -> GammaPrior`.
  - Backoff order L0 `(ata, tier, class)` → L1 `(ata, tier)` → L2 `(tier,)` → L3 global. `get_prior` returns the finest level whose group has `>= min_peers`, else coarser; global always present (floor prior if no records).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_peer_priors.py
from trax_io_forecasting.peer_priors import PeerPriorProvider, PeerRecord


def _rec(ata, tier, cls, count, exp=730.0):
    return PeerRecord(ata_chapter=ata, canonical_tier=tier, part_class=cls,
                      count=count, exposure=exp)


def test_get_prior_uses_finest_group_when_enough_peers():
    recs = [_rec("32", 1, "rotable", c) for c in (0, 1, 2, 0, 3)]  # 5 peers at L0
    p = PeerPriorProvider.fit(recs, min_peers=5)
    prior = p.get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    # group mean rate = mean(count)/730
    assert abs(prior.mean - (sum([0, 1, 2, 0, 3]) / 5 / 730.0)) < 1e-9

def test_get_prior_backs_off_when_group_too_small():
    # only 2 peers at L0 (32,1,rotable) but 6 share tier 1 -> back off to L2 (tier)
    recs = [_rec("32", 1, "rotable", 1), _rec("32", 1, "rotable", 2)]
    recs += [_rec("79", 1, "repairable", c) for c in (0, 1, 0, 2)]
    p = PeerPriorProvider.fit(recs, min_peers=5)
    prior = p.get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    tier_counts = [1, 2, 0, 1, 0, 2]
    assert abs(prior.mean - (sum(tier_counts) / len(tier_counts) / 730.0)) < 1e-9

def test_get_prior_falls_back_to_global_then_floor():
    recs = [_rec("32", 1, "rotable", 1), _rec("79", 2, "repairable", 3)]
    p = PeerPriorProvider.fit(recs, min_peers=5)
    # no group meets min_peers -> global prior over all records
    prior = p.get_prior(ata_chapter="11", canonical_tier=4, part_class="expendable")
    assert prior.alpha > 0.0 and prior.beta > 0.0

def test_fit_empty_returns_floor_global():
    p = PeerPriorProvider.fit([], min_peers=5)
    prior = p.get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    assert prior.alpha > 0.0 and prior.beta > 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_peer_priors.py -v`
Expected: FAIL — `ModuleNotFoundError: trax_io_forecasting.peer_priors`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/trax_io_forecasting/peer_priors.py
"""Peer-group empirical-Bayes priors with coarsening backoff (within-tenant, v1)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from trax_io_forecasting.eb import GammaPrior, fit_prior

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

_DEFAULT_BASIS_DAYS = 730


@dataclass(frozen=True)
class PeerRecord:
    ata_chapter: str | None
    canonical_tier: int
    part_class: str | None
    count: float
    exposure: float


def peer_record_from_context(
    context: PartLocationContext, *, basis_window_days: int = _DEFAULT_BASIS_DAYS
) -> PeerRecord:
    count = float(sum(o.removals + o.issues for o in context.demand_history.observations))
    return PeerRecord(
        ata_chapter=context.part_attributes.ata_chapter,
        canonical_tier=context.criticality.canonical_tier,
        part_class=context.part_attributes.part_class,
        count=count,
        exposure=float(basis_window_days),
    )


def _keys(ata: str | None, tier: int, cls: str | None) -> list[tuple]:
    # finest -> coarsest; global is the empty tuple
    return [(ata, tier, cls), (ata, tier), (tier,), ()]


@dataclass(frozen=True)
class PeerPriorProvider:
    _priors: dict[tuple, GammaPrior]
    _global: GammaPrior

    @classmethod
    def fit(cls, records: Iterable[PeerRecord], *, min_peers: int = 5) -> PeerPriorProvider:
        recs = list(records)
        groups: dict[tuple, list[PeerRecord]] = {}
        for r in recs:
            for key in _keys(r.ata_chapter, r.canonical_tier, r.part_class)[:-1]:
                groups.setdefault(key, []).append(r)
        priors = {
            key: fit_prior([m.count / m.exposure for m in members],
                           [m.exposure for m in members])
            for key, members in groups.items()
            if len(members) >= min_peers
        }
        global_prior = fit_prior(
            [r.count / r.exposure for r in recs], [r.exposure for r in recs]
        )
        return cls(_priors=priors, _global=global_prior)

    def get_prior(
        self, *, ata_chapter: str | None, canonical_tier: int, part_class: str | None
    ) -> GammaPrior:
        for key in _keys(ata_chapter, canonical_tier, part_class)[:-1]:
            prior = self._priors.get(key)
            if prior is not None:
                return prior
        return self._global
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_peer_priors.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add services/forecasting/src/trax_io_forecasting/peer_priors.py services/forecasting/tests/test_peer_priors.py
git commit -m "#5 forecasting slice C: peer-prior provider with coarsening backoff

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `EmpiricalBayesProjector` (`eb_projector.py`)

**Files:**
- Create: `src/trax_io_forecasting/eb_projector.py`
- Test: `tests/test_eb_projector.py`

**Interfaces:**
- Consumes: `PeerPriorProvider`, `peer_record_from_context` (Task 3); `posterior_rate`, `posterior_predictive_var` (Task 1); `DemandProjection`, `PartLocationContext`, `Regime`, `DemandProjectorProtocol`, `HistoricalScheduledProjector` (from `trax_io_reco`).
- Produces: `EmpiricalBayesProjector(provider, fallback=None, *, basis_window_days=730)` implementing `project(*, context, regime) -> DemandProjection`.
  - `regime is not Regime.ULTRA_RARE` → `fallback.project(...)`.
  - Else: `count`/`exposure` from `peer_record_from_context`; `prior = provider.get_prior(...)`; `lam_per_day = posterior_rate(prior, count, exposure)`; `var_per_day = posterior_predictive_var(prior, count, exposure)`; add scheduled-demand per-day component + `by_aircraft`/`by_task` exactly as `HistoricalScheduledProjector`; emit `dist_kind="COMPOUND_POISSON"`, `dist_params={"lambda": lam_per_day, "clump_p": 1.0}`, `std_per_day=sqrt(var_per_day)`, `historical_component=lam_per_day`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eb_projector.py
import math
from trax_io_reco.contracts.enums import Regime
from trax_io_forecasting.eb_projector import EmpiricalBayesProjector
from trax_io_forecasting.peer_priors import PeerPriorProvider, PeerRecord
# Reuse the context builder the existing projector tests use.
from tests.conftest import make_context  # see note in Step 3 if helper name differs


def _provider():
    recs = [PeerRecord("32", 1, "rotable", c, 730.0) for c in (0, 1, 2, 0, 3)]
    return PeerPriorProvider.fit(recs, min_peers=5)


def test_non_ultra_rare_delegates_to_fallback():
    class Sentinel:
        def project(self, *, context, regime):
            raise AssertionError("should not be called for ULTRA_RARE")
        # delegate marker
    calls = {}
    class FB:
        def project(self, *, context, regime):
            calls["hit"] = regime
            return "DELEGATED"
    proj = EmpiricalBayesProjector(_provider(), fallback=FB())
    ctx = make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[1])
    assert proj.project(context=ctx, regime=Regime.MODERATE) == "DELEGATED"
    assert calls["hit"] == Regime.MODERATE


def test_ultra_rare_emits_compound_poisson_with_shrunken_lambda():
    proj = EmpiricalBayesProjector(_provider())
    ctx = make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[1])
    dp = proj.project(context=ctx, regime=Regime.ULTRA_RARE)
    assert dp.dist_kind == "COMPOUND_POISSON"
    assert dp.dist_params["clump_p"] == 1.0
    own_rate = 1.0 / 730.0
    assert dp.dist_params["lambda"] > own_rate  # shrunk up toward the peer mean
    assert dp.std_per_day >= math.sqrt(dp.dist_params["lambda"])  # widened beyond Poisson


def test_new_pn_zero_history_shrinks_to_peer_mean():
    proj = EmpiricalBayesProjector(_provider())
    ctx = make_context(ata_chapter="32", canonical_tier=1, part_class="rotable", removals=[])
    dp = proj.project(context=ctx, regime=Regime.ULTRA_RARE)
    prior = _provider().get_prior(ata_chapter="32", canonical_tier=1, part_class="rotable")
    assert abs(dp.historical_component - prior.mean) < 1e-9
```

> **Note:** `make_context` is the helper the existing slice-A/B tests use to assemble a `PartLocationContext`. If `tests/conftest.py` exposes a differently-named builder (check `tests/test_projector.py` / `tests/test_gb_projector.py` imports), use that one and pass `ata_chapter`, `canonical_tier`, `part_class`, and a list of monthly `removals`. If no parameterized builder exists, add a thin `make_context(**overrides)` to `tests/conftest.py` in this step that wraps the existing fixture so these three knobs are settable.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb_projector.py -v`
Expected: FAIL — `ModuleNotFoundError: trax_io_forecasting.eb_projector`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/trax_io_forecasting/eb_projector.py
"""EmpiricalBayesProjector — a DemandProjector for the ULTRA_RARE regime.

Shrinks each part's sparse removal count toward a peer-group Gamma-Poisson prior and emits
the deterministic ULTRA_RARE COMPOUND_POISSON projection with the EB-shrunken lambda + a
widened (posterior-predictive) std. Every other regime delegates to the fallback projector.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector

from trax_io_forecasting.eb import posterior_predictive_var, posterior_rate
from trax_io_forecasting.peer_priors import PeerPriorProvider, peer_record_from_context

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

_DEFAULT_BASIS_DAYS = 730


class EmpiricalBayesProjector:
    def __init__(
        self,
        provider: PeerPriorProvider,
        fallback: DemandProjectorProtocol | None = None,
        *,
        basis_window_days: int = _DEFAULT_BASIS_DAYS,
    ) -> None:
        self._provider = provider
        self._fallback = fallback or HistoricalScheduledProjector(
            basis_window_days=basis_window_days
        )
        self._basis = basis_window_days

    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection:
        if regime is not Regime.ULTRA_RARE:
            return self._fallback.project(context=context, regime=regime)

        rec = peer_record_from_context(context, basis_window_days=self._basis)
        prior = self._provider.get_prior(
            ata_chapter=rec.ata_chapter,
            canonical_tier=rec.canonical_tier,
            part_class=rec.part_class,
        )
        lam_per_day = posterior_rate(prior, rec.count, rec.exposure)
        var_per_day = posterior_predictive_var(prior, rec.count, rec.exposure)

        sched_total = float(sum(s.qty for s in context.scheduled_demand))
        scheduled_per_day = sched_total / self._basis
        by_aircraft: dict[str, float] = {}
        by_task: dict[str, float] = {}
        for s in context.scheduled_demand:
            if s.ac_type:
                by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
            by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty

        mean_per_day = lam_per_day + scheduled_per_day
        return DemandProjection(
            mean_per_day=mean_per_day,
            std_per_day=math.sqrt(var_per_day),
            dist_kind="COMPOUND_POISSON",
            dist_params={"lambda": lam_per_day, "clump_p": 1.0},
            historical_component=lam_per_day,
            scheduled_component=scheduled_per_day,
            by_aircraft=by_aircraft,
            by_task=by_task,
            basis_window_days=self._basis,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb_projector.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add services/forecasting/src/trax_io_forecasting/eb_projector.py services/forecasting/tests/test_eb_projector.py services/forecasting/tests/conftest.py
git commit -m "#5 forecasting slice C: EmpiricalBayesProjector (ultra_rare)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Backtest hook (`eb_rate_fn`)

**Files:**
- Modify: `src/trax_io_forecasting/backtest.py`
- Test: `tests/test_eb_backtest.py`

**Interfaces:**
- Consumes: `GammaPrior`, `posterior_rate` (Task 1); `backtest_key`, `naive_scale` (existing in `backtest.py`).
- Produces: `eb_rate_fn(prior: GammaPrior) -> Callable[[Sequence[float]], float]` — returns a closure that treats the series as per-period counts: `count = sum(values)`, `exposure = len(values)` periods, returning `posterior_rate(prior, count, exposure)` (a per-period rate). Slots directly into `backtest_key(values, eb_rate_fn(prior), holdout=...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eb_backtest.py
import math
from trax_io_forecasting.backtest import backtest_key, eb_rate_fn
from trax_io_forecasting.eb import GammaPrior


def test_eb_rate_fn_returns_posterior_per_period_rate():
    prior = GammaPrior(alpha=1.0, beta=5.0)  # prior mean 0.2/period
    fn = eb_rate_fn(prior)
    # 2 events over 4 periods -> (1+2)/(5+4) = 0.3333...
    assert abs(fn([1.0, 0.0, 1.0, 0.0]) - (3.0 / 9.0)) < 1e-9

def test_eb_rate_fn_slots_into_backtest_without_nan():
    prior = GammaPrior(alpha=1.0, beta=10.0)
    score = backtest_key([0, 1, 0, 0, 2, 0, 1, 0], eb_rate_fn(prior), holdout=3)
    assert math.isfinite(score) or score == math.inf  # well-defined, never NaN
    assert not (isinstance(score, float) and math.isnan(score))

def test_eb_rate_fn_deterministic():
    prior = GammaPrior(alpha=2.0, beta=7.0)
    fn = eb_rate_fn(prior)
    assert fn([1.0, 0.0, 3.0]) == fn([1.0, 0.0, 3.0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb_backtest.py -v`
Expected: FAIL — `ImportError: cannot import name 'eb_rate_fn'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/trax_io_forecasting/backtest.py  — add near the other rate fns
from trax_io_forecasting.eb import GammaPrior, posterior_rate


def eb_rate_fn(prior: GammaPrior) -> Callable[[Sequence[float]], float]:
    """Empirical-Bayes per-period rate for the MASE harness.

    EB's gain is cross-sectional (shrinkage across peers), so single-series backtesting
    holds the peer prior fixed — this guards against regressions/NaNs rather than proving
    the portfolio-level shrinkage benefit.
    """

    def rate(values: Sequence[float]) -> float:
        vals = [float(v) for v in values]
        return posterior_rate(prior, sum(vals), float(len(vals)))

    return rate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb_backtest.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add services/forecasting/src/trax_io_forecasting/backtest.py services/forecasting/tests/test_eb_backtest.py
git commit -m "#5 forecasting slice C: eb_rate_fn backtest hook

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Builder + end-to-end integration

**Files:**
- Modify: `src/trax_io_forecasting/eb_projector.py`
- Test: `tests/test_eb_integration.py`

**Interfaces:**
- Consumes: everything above; `RecommendationService` (from `trax_io_reco.service`); the existing integration-test context builders (model on `tests/test_gb_integration.py`).
- Produces: `build_eb_projector(contexts, fallback=None, *, basis_window_days=730, min_peers=5) -> EmpiricalBayesProjector` — runs the pre-pass: `PeerPriorProvider.fit(peer_record_from_context(c) for c in contexts)` then constructs the projector.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eb_integration.py
# Model the batch + context assembly on tests/test_gb_integration.py (same fixtures/imports).
import math
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import HistoricalScheduledProjector
from trax_io_forecasting.eb_projector import EmpiricalBayesProjector, build_eb_projector
from trax_io_forecasting.peer_priors import peer_record_from_context


def test_build_eb_projector_fits_provider_from_batch(ultra_rare_contexts):
    # ultra_rare_contexts: a list[PartLocationContext] of >= min_peers similar ultra-rare parts.
    proj = build_eb_projector(ultra_rare_contexts, min_peers=2)
    assert isinstance(proj, EmpiricalBayesProjector)
    dp = proj.project(context=ultra_rare_contexts[0], regime=Regime.ULTRA_RARE)
    assert dp.dist_kind == "COMPOUND_POISSON"


def test_projection_mirrors_deterministic_ultra_rare_shape_except_lambda(ultra_rare_contexts):
    ctx = ultra_rare_contexts[0]
    eb = build_eb_projector(ultra_rare_contexts, min_peers=2).project(
        context=ctx, regime=Regime.ULTRA_RARE
    )
    det = HistoricalScheduledProjector().project(context=ctx, regime=Regime.ULTRA_RARE)
    # Same shape, fields, and scheduled component; only the rate/dist params differ.
    assert eb.dist_kind == det.dist_kind == "COMPOUND_POISSON"
    assert eb.scheduled_component == det.scheduled_component
    assert eb.by_aircraft == det.by_aircraft and eb.by_task == det.by_task
    assert eb.basis_window_days == det.basis_window_days
    assert set(eb.dist_params) == set(det.dist_params)  # {"lambda", "clump_p"}
    assert eb.dist_params["lambda"] != det.dist_params["lambda"]  # EB-shrunken


def test_recommendation_service_runs_with_eb_projector(reco_batch_inputs, ultra_rare_contexts):
    # Reuse the slice-B integration pattern: build the service with projector=, run a batch,
    # assert it produces recommendations and other regimes are unaffected.
    from trax_io_reco.service import RecommendationService  # noqa: PLC0415
    proj = build_eb_projector(ultra_rare_contexts, min_peers=2)
    # ... assemble service exactly as tests/test_gb_integration.py does, passing projector=proj
    # assert the run completes and ultra_rare keys carry COMPOUND_POISSON projections.
```

> **Note:** Fixtures `ultra_rare_contexts` and `reco_batch_inputs` (or whatever the slice-B integration test calls them) should be added to `tests/conftest.py` or the test module, mirroring `tests/test_gb_integration.py`. Read that file first and copy its service-construction and context-assembly approach verbatim, changing only the `projector=` to the EB projector and the regime to `ULTRA_RARE`. Do not invent new contract fields — use the same builders.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb_integration.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_eb_projector'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/trax_io_forecasting/eb_projector.py  — add at module bottom
from collections.abc import Iterable  # add to imports


def build_eb_projector(
    contexts: Iterable[PartLocationContext],
    fallback: DemandProjectorProtocol | None = None,
    *,
    basis_window_days: int = _DEFAULT_BASIS_DAYS,
    min_peers: int = 5,
) -> EmpiricalBayesProjector:
    """Pre-pass: fit the peer-prior provider from a batch, then build the projector."""
    records = [
        peer_record_from_context(c, basis_window_days=basis_window_days) for c in contexts
    ]
    provider = PeerPriorProvider.fit(records, min_peers=min_peers)
    return EmpiricalBayesProjector(
        provider, fallback=fallback, basis_window_days=basis_window_days
    )
```

> Move the `PartLocationContext` import out of the `TYPE_CHECKING` block (it is now used at runtime in the signature only as an annotation — keep it under `TYPE_CHECKING` since `from __future__ import annotations` defers evaluation; `Iterable` is the only new runtime import).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_eb_integration.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + lint + commit**

```bash
cd services/forecasting && uv run --extra dev pytest && uv run --extra dev ruff check .
git add services/forecasting/src/trax_io_forecasting/eb_projector.py services/forecasting/tests/
git commit -m "#5 forecasting slice C: build_eb_projector + end-to-end integration

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: ADR + tracker updates

**Files:**
- Create: `docs/adr/2026-06-28-0013-empirical-bayes-ultra-rare-projector.md`
- Modify: `ROADMAP.md`, `TASKS.md`, `CLAUDE.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Write ADR-0013**

Record: context (ultra_rare sparsity, single-part seam tension), decision (Gamma-Poisson EB via injected `PeerPriorProvider`, MoM closed-form, `COMPOUND_POISSON` mirror with widened std, backoff L0→L3), consequences, and deferrals (Chronos/Moirai challenger; cross-tenant federated §5.3; compound-clump; Supervisor two-pass wiring). Follow the structure of [ADR-0009](../../adr/2026-06-27-0009-gradient-boosted-projector.md).

- [ ] **Step 2: Update trackers**

- `ROADMAP.md` — mark the slice C line `[x]` with date and a one-line summary; keep slice D + the deferred challenger as remaining.
- `TASKS.md` — add a "Completed 2026-06-28 — Forecasting #5 slice C" entry mirroring the slice B entry's style; update the In-Progress line.
- `CLAUDE.md` — extend the `services/forecasting` row/description to mention slice C (EB ultra_rare).

- [ ] **Step 3: Commit**

```bash
git add docs/adr/2026-06-28-0013-empirical-bayes-ultra-rare-projector.md ROADMAP.md TASKS.md CLAUDE.md
git commit -m "#5 forecasting slice C: ADR-0013 + tracker updates

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** §3 EB math → Tasks 1–2; §4.2 provider+backoff → Task 3; §4.3 projector → Task 4; §4.4 backtest → Task 5; §5 data-flow/pre-pass builder → Task 6; §7 deferrals + §8 tests → covered across tasks; ADR + trackers → Task 7. No gaps.

**Placeholder scan:** all code steps contain full code. The two `> Note` blocks point to real, committed pattern files (`tests/test_gb_integration.py`, `tests/conftest.py`) the implementer must read — these are concrete instructions, not deferred work, because the exact fixture names are owned by existing code that must be matched rather than re-invented.

**Type consistency:** `GammaPrior(alpha, beta)`, `posterior_rate(prior, count, exposure)`, `posterior_predictive_var(prior, count, exposure)`, `fit_prior(rates, exposures)`, `PeerRecord(ata_chapter, canonical_tier, part_class, count, exposure)`, `PeerPriorProvider.fit(records, *, min_peers)` / `.get_prior(*, ata_chapter, canonical_tier, part_class)`, `EmpiricalBayesProjector(provider, fallback, *, basis_window_days)`, `eb_rate_fn(prior)`, `build_eb_projector(contexts, fallback, *, basis_window_days, min_peers)` — names and signatures consistent across tasks.
