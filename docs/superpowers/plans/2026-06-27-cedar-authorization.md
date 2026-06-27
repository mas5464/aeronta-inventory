# Cedar Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `CedarAutonomyPolicy` that implements the existing `AutonomyPolicy` Protocol via declarative Cedar policy (`cedarpy`, in-process, no AWS), so the spine's auto-write-vs-queue decision is governed by an auditable `.cedar` file (the design §6.1 tier bands) and swaps into `GuardrailEnforcer` by DI.

**Architecture:** A new `guardrail/cedar.py` adds a thin `CedarAuthorizer` over `cedarpy.is_authorized` plus `CedarAutonomyPolicy`, governed by `guardrail/policies/autonomy_bands.cedar`. cedarpy is lazy-imported behind a `cedar` optional extra so the core install stays dependency-free; the enforcer/supervisor are unchanged (the policy is injected). The code-level §6.2 hard floors in `hard.py` stay as-is (defense-in-depth).

**Tech Stack:** Python 3.12, `cedarpy>=4.0` (Rust-backed Cedar bindings, optional `cedar` extra), `uv` + `pytest` + `ruff`.

Spec: [docs/superpowers/specs/2026-06-27-cedar-authorization-design.md](../specs/2026-06-27-cedar-authorization-design.md).

## Global Constraints

- Work in `services/agent-spine/`; branch off `main`. ruff: `line-length = 100`, `select = ["E","F","I","B","UP","N","SIM"]`; no mypy; Python `>=3.12`; `pythonpath = ["src"]`.
- `cedarpy` is **lazy-imported** (inside functions/methods), never at module top level, so `trax_io_spine.guardrail.cedar` imports without the `cedar` extra. cedarpy tests are gated `pytest.importorskip("cedarpy")` and run with `--extra cedar`.
- **Cedar has no float type.** `delta_pct` (a float ratio) crosses the Cedar boundary as an integer: `delta_bps = round(delta_pct * 10000)` (40% → 4000; the 100% cap → 10000).
- The Cedar `entities` list MUST declare three entities: principal `Agent::"spine"`, action `Action::"<action>"`, and resource `PartLocation::"k"` with attrs `{"criticality_tier": int, "delta_bps": int}`.
- `cedarpy.is_authorized(request: dict, policies: str, entities: list[dict]) -> AuthzResult`; `AuthzResult.decision ∈ {cedarpy.Decision.Allow, cedarpy.Decision.Deny, cedarpy.Decision.NoDecision}`. `NoDecision` (a policy parse/eval error) MUST raise `CedarPolicyError` — never silently allow.
- Tier → Cedar action: `ADVISOR` → always queue (no Cedar call); `BOUNDED` → `bounded_write`; `AUTONOMOUS` → `autonomous_write`.
- §6.1 bands (verbatim in `autonomy_bands.cedar`): `autonomous_write` permit when `criticality_tier >= 4 && delta_bps <= 4000`; `bounded_write` permit when `criticality_tier >= 2 && delta_bps <= 1500`; `forbid … when delta_bps > 10000`.
- `CedarAutonomyPolicy` implements the existing Protocol from `trax_io_spine.guardrail.policy`:
  `AutonomyPolicy.authorize(self, *, tier: AutonomyTier, delta_pct: float, criticality_tier: int) -> GuardrailStatus`.
- `AutonomyTier` from `trax_io_reco.contracts.enums`; `GuardrailStatus` from `trax_io_spine.contracts`.
- Commit after every green task.

---

## File Structure

```
services/agent-spine/
├── pyproject.toml                                  # + [project.optional-dependencies] cedar
├── README.md                                       # + Cedar note
├── src/trax_io_spine/guardrail/
│   ├── cedar.py                                    # CedarPolicyError, CedarAuthorizer, CedarAutonomyPolicy
│   └── policies/
│       └── autonomy_bands.cedar                    # §6.1 bands (data file)
└── tests/guardrail/
    ├── test_cedar_authorizer.py                    # CedarAuthorizer (real cedarpy)
    ├── test_cedar_policy.py                         # CedarAutonomyPolicy band matrix
    └── test_cedar_enforcer_swap.py                 # GuardrailEnforcer(policy=CedarAutonomyPolicy()) end-to-end
```

---

## Task 1: `cedar` extra + `CedarAuthorizer`

**Files:**
- Modify: `services/agent-spine/pyproject.toml` (add the `cedar` optional extra)
- Create: `services/agent-spine/src/trax_io_spine/guardrail/cedar.py`
- Test: `services/agent-spine/tests/guardrail/test_cedar_authorizer.py`

**Interfaces:**
- Produces:
  - `CedarPolicyError(RuntimeError)`.
  - `CedarAuthorizer(policies: str)` with `is_allowed(self, *, action: str, resource_attrs: dict[str, int]) -> bool` — builds the request + the three declared entities, calls `cedarpy.is_authorized`, returns `True` on `Allow`, `False` on `Deny`, raises `CedarPolicyError` on `NoDecision`.

- [ ] **Step 1: Add the `cedar` extra to `pyproject.toml`**

In `services/agent-spine/pyproject.toml`, under `[project.optional-dependencies]`, add a `cedar` group alongside the existing `dev`/`emro`:
```toml
cedar = ["cedarpy>=4.0"]
```

- [ ] **Step 2: Write the failing test**

Create `services/agent-spine/tests/guardrail/test_cedar_authorizer.py`:
```python
"""CedarAuthorizer — real cedarpy (skips without the `cedar` extra)."""

from __future__ import annotations

import pytest

pytest.importorskip("cedarpy")

from trax_io_spine.guardrail.cedar import CedarAuthorizer, CedarPolicyError  # noqa: E402

_POLICY = (
    'permit(principal, action == Action::"autonomous_write", resource is PartLocation)\n'
    "when { resource.criticality_tier >= 4 && resource.delta_bps <= 4000 };"
)


def test_permit_match_allows() -> None:
    a = CedarAuthorizer(_POLICY)
    assert a.is_allowed(
        action="autonomous_write", resource_attrs={"criticality_tier": 4, "delta_bps": 2000}
    ) is True


def test_non_match_denies() -> None:
    a = CedarAuthorizer(_POLICY)
    # criticality 3 fails the >= 4 floor -> no permit matches -> default deny
    assert a.is_allowed(
        action="autonomous_write", resource_attrs={"criticality_tier": 3, "delta_bps": 2000}
    ) is False


def test_unknown_action_denies() -> None:
    a = CedarAuthorizer(_POLICY)
    assert a.is_allowed(
        action="bounded_write", resource_attrs={"criticality_tier": 4, "delta_bps": 100}
    ) is False


def test_parse_error_raises_not_allows() -> None:
    # A float literal is a Cedar parse error -> is_authorized returns NoDecision -> must raise.
    bad = (
        'permit(principal, action, resource is PartLocation)\n'
        "when { resource.delta_pct <= 0.40 };"
    )
    a = CedarAuthorizer(bad)
    with pytest.raises(CedarPolicyError):
        a.is_allowed(action="autonomous_write", resource_attrs={"criticality_tier": 4, "delta_bps": 0})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/agent-spine && uv sync --extra dev --extra emro --extra cedar && uv run --extra dev --extra cedar pytest tests/guardrail/test_cedar_authorizer.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'trax_io_spine.guardrail.cedar'`.

- [ ] **Step 4: Implement `cedar.py` (authorizer only)**

Create `services/agent-spine/src/trax_io_spine/guardrail/cedar.py`:
```python
"""Cedar-backed authorization for the spine's autonomy decision.

`cedarpy` is imported lazily so this module loads without the `cedar` extra. The autonomy
band policy lives in `policies/autonomy_bands.cedar`; this module turns a (tier, delta, criticality)
question into a Cedar `is_authorized` call and maps the decision to a GuardrailStatus.
"""

from __future__ import annotations

_PRINCIPAL_TYPE = "Agent"
_PRINCIPAL_ID = "spine"
_RESOURCE_TYPE = "PartLocation"
_RESOURCE_ID = "k"  # decision is attribute-based; the id is a fixed placeholder


class CedarPolicyError(RuntimeError):
    """Cedar returned no clear decision (policy parse/eval error). Never treated as allow."""


class CedarAuthorizer:
    """Thin typed boundary over ``cedarpy.is_authorized`` for one principal/resource shape."""

    def __init__(self, policies: str) -> None:
        self._policies = policies

    def is_allowed(self, *, action: str, resource_attrs: dict[str, int]) -> bool:
        import cedarpy

        request = {
            "principal": f'{_PRINCIPAL_TYPE}::"{_PRINCIPAL_ID}"',
            "action": f'Action::"{action}"',
            "resource": f'{_RESOURCE_TYPE}::"{_RESOURCE_ID}"',
            "context": {},
        }
        entities = [
            {"uid": {"type": _PRINCIPAL_TYPE, "id": _PRINCIPAL_ID}, "attrs": {}, "parents": []},
            {"uid": {"type": "Action", "id": action}, "attrs": {}, "parents": []},
            {
                "uid": {"type": _RESOURCE_TYPE, "id": _RESOURCE_ID},
                "attrs": dict(resource_attrs),
                "parents": [],
            },
        ]
        decision = cedarpy.is_authorized(request, self._policies, entities).decision
        if decision == cedarpy.Decision.Allow:
            return True
        if decision == cedarpy.Decision.Deny:
            return False
        raise CedarPolicyError(f"cedar returned {decision!r} (policy parse/eval error)")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev --extra cedar pytest tests/guardrail/test_cedar_authorizer.py -q`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add services/agent-spine/pyproject.toml services/agent-spine/uv.lock \
  services/agent-spine/src/trax_io_spine/guardrail/cedar.py \
  services/agent-spine/tests/guardrail/test_cedar_authorizer.py
git commit -m "#4 agent-spine: cedar extra + CedarAuthorizer (cedarpy wrapper, NoDecision->raise)"
```

---

## Task 2: `autonomy_bands.cedar` + `CedarAutonomyPolicy`

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/guardrail/policies/autonomy_bands.cedar`
- Modify: `services/agent-spine/src/trax_io_spine/guardrail/cedar.py` (add `CedarAutonomyPolicy` + default-policy loader)
- Test: `services/agent-spine/tests/guardrail/test_cedar_policy.py`

**Interfaces:**
- Consumes: `CedarAuthorizer` (Task 1); `AutonomyTier` (`trax_io_reco.contracts.enums`); `GuardrailStatus` (`trax_io_spine.contracts`).
- Produces: `CedarAutonomyPolicy(policies: str | None = None)` implementing `authorize(self, *, tier: AutonomyTier, delta_pct: float, criticality_tier: int) -> GuardrailStatus`. `None` loads the packaged `autonomy_bands.cedar`.

- [ ] **Step 1: Create the policy file**

Create `services/agent-spine/src/trax_io_spine/guardrail/policies/autonomy_bands.cedar`:
```cedar
// Trax IO autonomy bands (design §6.1). delta_bps = round(delta_pct * 10000); Cedar has no float type.

// Tier C — autonomous: routine/consumable parts (essentiality 4-5), single-write delta within +/-40%.
permit(principal, action == Action::"autonomous_write", resource is PartLocation)
when { resource.criticality_tier >= 4 && resource.delta_bps <= 4000 };

// Tier B — bounded: non-flight-safety parts (essentiality 2-3 and below), delta within +/-15%.
permit(principal, action == Action::"bounded_write", resource is PartLocation)
when { resource.criticality_tier >= 2 && resource.delta_bps <= 1500 };

// §6.2 hard floor (declarative mirror of the code-level cap): never auto-write a delta over 100%.
forbid(principal, action, resource is PartLocation)
when { resource.delta_bps > 10000 };
```

- [ ] **Step 2: Write the failing test**

Create `services/agent-spine/tests/guardrail/test_cedar_policy.py`:
```python
"""CedarAutonomyPolicy band matrix — real cedarpy (skips without the `cedar` extra)."""

from __future__ import annotations

import pytest

pytest.importorskip("cedarpy")

from trax_io_reco.contracts.enums import AutonomyTier  # noqa: E402

from trax_io_spine.contracts import GuardrailStatus  # noqa: E402
from trax_io_spine.guardrail.cedar import CedarAutonomyPolicy  # noqa: E402

APPROVED = GuardrailStatus.APPROVED_FOR_WRITE
QUEUED = GuardrailStatus.QUEUED_FOR_APPROVAL


@pytest.fixture
def policy() -> CedarAutonomyPolicy:
    return CedarAutonomyPolicy()  # loads the packaged autonomy_bands.cedar


def test_advisor_always_queues(policy: CedarAutonomyPolicy) -> None:
    assert policy.authorize(tier=AutonomyTier.ADVISOR, delta_pct=0.0, criticality_tier=5) == QUEUED


def test_autonomous_in_band_low_criticality_approves(policy: CedarAutonomyPolicy) -> None:
    # crit 4 (>=4) and 20% (<=40%) -> approved
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.20, criticality_tier=4
    ) == APPROVED


def test_autonomous_critical_part_queues(policy: CedarAutonomyPolicy) -> None:
    # crit 3 fails the >=4 floor -> queued
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.10, criticality_tier=3
    ) == QUEUED


def test_autonomous_out_of_band_queues(policy: CedarAutonomyPolicy) -> None:
    # 60% exceeds the 40% band -> queued
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.60, criticality_tier=5
    ) == QUEUED


def test_autonomous_exact_band_edge_approves(policy: CedarAutonomyPolicy) -> None:
    # 40% -> 4000 bps == ceiling -> approved (<=)
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.40, criticality_tier=4
    ) == APPROVED


def test_bounded_band_is_tighter(policy: CedarAutonomyPolicy) -> None:
    # crit 3, 10% (<=15%) -> approved under bounded; 20% (>15%) -> queued
    assert policy.authorize(
        tier=AutonomyTier.BOUNDED, delta_pct=0.10, criticality_tier=3
    ) == APPROVED
    assert policy.authorize(
        tier=AutonomyTier.BOUNDED, delta_pct=0.20, criticality_tier=3
    ) == QUEUED


def test_tier1_flight_safety_never_autowrites(policy: CedarAutonomyPolicy) -> None:
    # criticality_tier 1 matches no permit on either action -> queued
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.0, criticality_tier=1
    ) == QUEUED
    assert policy.authorize(
        tier=AutonomyTier.BOUNDED, delta_pct=0.0, criticality_tier=1
    ) == QUEUED


def test_over_100pct_is_forbidden(policy: CedarAutonomyPolicy) -> None:
    # 150% -> 15000 bps > 10000 -> forbid overrides any permit -> queued
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=1.50, criticality_tier=5
    ) == QUEUED
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra cedar pytest tests/guardrail/test_cedar_policy.py -q`
Expected: FAIL with `ImportError: cannot import name 'CedarAutonomyPolicy'`.

- [ ] **Step 4: Add `CedarAutonomyPolicy` to `cedar.py`**

Append to `services/agent-spine/src/trax_io_spine/guardrail/cedar.py` (add the imports at the top of the file alongside the existing `from __future__` line):
```python
from importlib.resources import files

from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import GuardrailStatus

_BPS_PER_UNIT = 10000
_TIER_ACTION = {
    AutonomyTier.BOUNDED: "bounded_write",
    AutonomyTier.AUTONOMOUS: "autonomous_write",
}


def _default_policy_text() -> str:
    return (
        files("trax_io_spine.guardrail")
        .joinpath("policies", "autonomy_bands.cedar")
        .read_text(encoding="utf-8")
    )


class CedarAutonomyPolicy:
    """`AutonomyPolicy` backed by the declarative `autonomy_bands.cedar` (design §6.1)."""

    def __init__(self, policies: str | None = None) -> None:
        self._authorizer = CedarAuthorizer(policies if policies is not None else _default_policy_text())

    def authorize(
        self, *, tier: AutonomyTier, delta_pct: float, criticality_tier: int
    ) -> GuardrailStatus:
        action = _TIER_ACTION.get(tier)
        if action is None:  # ADVISOR (Tier A) is always human approval
            return GuardrailStatus.QUEUED_FOR_APPROVAL
        delta_bps = round(delta_pct * _BPS_PER_UNIT)
        allowed = self._authorizer.is_allowed(
            action=action,
            resource_attrs={"criticality_tier": criticality_tier, "delta_bps": delta_bps},
        )
        return GuardrailStatus.APPROVED_FOR_WRITE if allowed else GuardrailStatus.QUEUED_FOR_APPROVAL
```

> The `from __future__ import annotations` line must remain the first statement in the file; place the three new `import`/`from` lines after it (ruff `I` will order them). `cedarpy` stays lazy-imported inside `CedarAuthorizer.is_allowed` only.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev --extra cedar pytest tests/guardrail/test_cedar_policy.py -q`
Expected: 9 passed. (If `files(...).joinpath(...).read_text()` raises `FileNotFoundError`, confirm the `.cedar` file is at `src/trax_io_spine/guardrail/policies/autonomy_bands.cedar` — it is read from the source tree under `pythonpath=["src"]`.)

- [ ] **Step 6: Lint + commit**

Run: `cd services/agent-spine && uv run --extra dev ruff check .` (fix any findings).
```bash
git add services/agent-spine/src/trax_io_spine/guardrail/cedar.py \
  services/agent-spine/src/trax_io_spine/guardrail/policies/autonomy_bands.cedar \
  services/agent-spine/tests/guardrail/test_cedar_policy.py
git commit -m "#4 agent-spine: CedarAutonomyPolicy + autonomy_bands.cedar (§6.1 bands, integer bps)"
```

---

## Task 3: enforcer swap (end-to-end) + docs

**Files:**
- Modify: `services/agent-spine/pyproject.toml` (ensure the `.cedar` data file ships in the wheel)
- Modify: `services/agent-spine/README.md` (Cedar note)
- Test: `services/agent-spine/tests/guardrail/test_cedar_enforcer_swap.py`

**Interfaces:**
- Consumes: `GuardrailEnforcer` (`trax_io_spine.guardrail.enforce`), `CedarAutonomyPolicy` (Task 2), the `make_rec` fixture + `make_policy`/`make_current` helpers (`tests/conftest.py`), `GuardrailStatus`.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/guardrail/test_cedar_enforcer_swap.py`:
```python
"""GuardrailEnforcer with CedarAutonomyPolicy injected — proves the Protocol swap (real cedarpy)."""

from __future__ import annotations

import pytest

pytest.importorskip("cedarpy")

from trax_io_reco.contracts.enums import AutonomyTier  # noqa: E402

from trax_io_spine.contracts import GuardrailStatus  # noqa: E402
from trax_io_spine.guardrail.cedar import CedarAutonomyPolicy  # noqa: E402
from trax_io_spine.guardrail.enforce import GuardrailEnforcer  # noqa: E402

from tests.conftest import make_current, make_policy  # noqa: E402


def _enforcer() -> GuardrailEnforcer:
    return GuardrailEnforcer(policy=CedarAutonomyPolicy())


def test_cedar_enforcer_approves_autonomous_in_band(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=4,
        policy=make_policy(max_stock=23), current_policy=make_current(max_stock=20),  # +15%
    )
    out = _enforcer().enforce(rec)
    assert out.status is GuardrailStatus.APPROVED_FOR_WRITE


def test_cedar_enforcer_queues_critical_part(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=2,  # < 4 floor
        policy=make_policy(max_stock=22), current_policy=make_current(max_stock=20),  # +10%
    )
    out = _enforcer().enforce(rec)
    assert out.status is GuardrailStatus.QUEUED_FOR_APPROVAL
    assert out.approval_task is not None


def test_cedar_enforcer_rejects_hard_floor_breach(make_rec) -> None:
    # delta > 100% is rejected by the code-level hard guardrail BEFORE Cedar is consulted.
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=5,
        policy=make_policy(rop=10, eoq=5, safety_stock=4, max_stock=60),  # 20 -> 60 = +200%
        current_policy=make_current(max_stock=20),
    )
    out = _enforcer().enforce(rec)
    assert out.status is GuardrailStatus.REJECTED_HARD_GUARDRAIL
```

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `cd services/agent-spine && uv run --extra dev --extra cedar pytest tests/guardrail/test_cedar_enforcer_swap.py -q`
Expected: the three tests PASS as written — `GuardrailEnforcer` already accepts an injected `policy` (built in the spine's Task 6), and `CedarAutonomyPolicy` satisfies the `AutonomyPolicy` Protocol. If any fail, the failure localizes the integration defect (e.g. an attr/name mismatch) — fix it before proceeding. (This task has no new production logic; it is the integration gate for Tasks 1–2.)

- [ ] **Step 3: Ensure the `.cedar` ships in the wheel**

In `services/agent-spine/pyproject.toml`, under the existing `[tool.hatch.build.targets.wheel]`, add (so the data file is packaged for a real install, not only the source tree):
```toml
[tool.hatch.build.targets.wheel.force-include]
"src/trax_io_spine/guardrail/policies/autonomy_bands.cedar" = "trax_io_spine/guardrail/policies/autonomy_bands.cedar"
```

- [ ] **Step 4: README note**

In `services/agent-spine/README.md`, add a short subsection after the dev-setup block:
```markdown
## Cedar authorization (optional)

`CedarAutonomyPolicy` (in `guardrail/cedar.py`) implements the `AutonomyPolicy` Protocol via
declarative Cedar policy (`guardrail/policies/autonomy_bands.cedar`, the design §6.1 tier bands),
evaluated in-process by `cedarpy` — no AWS. It is opt-in; the default stays the deterministic
`BandAutonomyPolicy`. Wire it with `GuardrailEnforcer(policy=CedarAutonomyPolicy())`. Cedar has no
float type, so deltas cross as integer basis points (`delta_bps = round(delta_pct * 10000)`).

Install/test the Cedar path with the `cedar` extra:
```bash
uv run --extra dev --extra cedar pytest
```
```

- [ ] **Step 5: Full suite + lint + commit**

Run:
```bash
cd services/agent-spine && uv run --extra dev --extra emro --extra cedar pytest -q && uv run --extra dev ruff check .
```
Expected: all green (the prior 34 + the new cedar tests), ruff clean.
```bash
git add services/agent-spine/pyproject.toml services/agent-spine/README.md \
  services/agent-spine/tests/guardrail/test_cedar_enforcer_swap.py
git commit -m "#4 agent-spine: CedarAutonomyPolicy enforcer swap test + packaging + README"
```

---

## Post-implementation

- [ ] Update `CLAUDE.md` agent-spine run note (mention `--extra cedar`), `ROADMAP.md` (#4: Cedar autonomy policy done), and `TASKS.md`.
- [ ] Adversarial review of `cedar.py` + the float→bps boundary + the `.cedar` bands before declaring done.

---

## Self-Review notes (author)

- **Spec coverage:** `cedar` extra + lazy import → Task 1 (Step 1, Global Constraints) + Task 3 (packaging); `CedarAuthorizer` §3.1 → Task 1; `CedarAutonomyPolicy` §3.2 → Task 2; `autonomy_bands.cedar` §3.3 (§6.1-faithful bands) → Task 2 Step 1; code-level hard floors retained §3.4 → unchanged + verified in Task 3 (`test_cedar_enforcer_rejects_hard_floor_breach`); no enforcer/supervisor changes → confirmed by Task 3; testing §5 (band matrix, malformed→raise, exact-bps edge) → Tasks 1–3.
- **Placeholder scan:** none — every code/policy/test block is complete.
- **Type consistency:** `CedarAuthorizer.is_allowed(action, resource_attrs)`, `CedarPolicyError`, `CedarAutonomyPolicy.authorize(tier, delta_pct, criticality_tier) -> GuardrailStatus`, `_TIER_ACTION`, `delta_bps = round(delta_pct*10000)`, `Decision.{Allow,Deny}` — used identically across tasks; the Protocol signature matches `trax_io_spine.guardrail.policy.AutonomyPolicy`.
