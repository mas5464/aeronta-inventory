# #6 Writeback Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the writeback seam (the only agent with eMRO write permission) with provenance history, rollback (90-day window), and shadow-mode — locally, against `fake_emro` — behind a backward-compatible `AuditedWritebackTarget` Protocol.

**Architecture:** Extend `WritebackTarget` (unchanged base `write()`) with an `AuditedWritebackTarget` Protocol adding `get_history` + `rollback`. `InMemoryWritebackTarget` becomes the single behavior definition (a per-key `HistoryEntry` ledger, shadow-mode, rollback); `fake_emro` is **backed by an `InMemoryWritebackTarget`** instance (no mock drift); `RestWritebackClient` mirrors it over HTTP. The Supervisor passes `tier`/`shadow` through and gains a `shadow` run mode surfaced as `trax-io-spine run --shadow`.

**Tech Stack:** Python 3.14, pydantic v2, FastAPI + httpx (the `emro` extra), typer, uv + pytest + ruff.

## Global Constraints

- **Python ≥3.12, runs on 3.14.** All work in `services/agent-spine`. Test: `cd services/agent-spine && uv run --extra dev pytest` (add `--extra emro` for the FastAPI `fake_emro`/`rest` tests); lint `uv run --extra dev ruff check .` (line-length 100, select E/F/I/B/UP/N/SIM).
- **All new contract models** are pydantic v2 `ConfigDict(frozen=True, extra="forbid")` (use the existing `_Base` in `contracts.py`).
- **`WritebackTarget.write` base signature is unchanged.** New `WritebackRequest` fields are **defaulted** (`tier: AutonomyTier | None = None`, `shadow: bool = False`) so every existing caller/test still constructs valid requests. **Keep `InMemoryWritebackTarget.history`** (the success-only `list[WritebackResult]`) — existing tests depend on it.
- **Write scope stays the four columns** `("rop", "eoq", "safety_stock", "max_stock")`; `old_values`/`new_values` are dicts over exactly these.
- **`fake_emro` is backed by an `InMemoryWritebackTarget`** — one behavior definition, never a parallel reimplementation.
- **Rollback walks only `WRITTEN` entries**; shadow entries are audit-only and never mutate `_levels` or the rollback chain. `rollback_window_days` must be `> 0` (validated at construction).
- Reference (verified): `WritebackStatus(StrEnum){WRITTEN, DEFERRED_OPEN_ORDER, FAILED}`; `AutonomyTier(IntEnum){ADVISOR=1, BOUNDED=2, AUTONOMOUS=3}` (`from trax_io_reco.contracts.enums import AutonomyTier`, already imported in contracts.py); `WritebackResult.old_values/new_values: dict[str,int] | None`; supervisor idempotency key `f"{tenant}:{pn}:{location}:{input_snapshot_hash}"`; `GuardrailOutcome.tier: AutonomyTier`.
- Commit after each task with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Contracts + `AuditedWritebackTarget` Protocol

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/contracts.py`
- Modify: `services/agent-spine/src/trax_io_spine/writeback/target.py` (add the Protocol)
- Test: `services/agent-spine/tests/writeback/test_contracts_hardening.py`

**Interfaces:**
- Produces in `contracts.py`: `WritebackStatus.SHADOWED="shadowed"`; `RollbackStatus(StrEnum){ROLLED_BACK, OUTSIDE_WINDOW, NOTHING_TO_REVERT}`; `WritebackRequest` += `tier: AutonomyTier | None = None`, `shadow: bool = False`; `HistoryEntry`, `RollbackRequest`, `RollbackResult` (fields below); `OrchestrationResult` += `shadowed: tuple[WritebackResult, ...] = ()`.
- Produces in `target.py`: `AuditedWritebackTarget(WritebackTarget, Protocol)` with `get_history(*, tenant_id, pn, location) -> tuple[HistoryEntry, ...]` and `rollback(req: RollbackRequest) -> RollbackResult`.

- [ ] **Step 1: Write the failing test** — `tests/writeback/test_contracts_hardening.py`

```python
from datetime import UTC, datetime

from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import (
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
    RollbackStatus,
    WritebackRequest,
    WritebackStatus,
)


def test_writeback_request_backward_compatible_defaults():
    req = WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=5, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key="k1",
    )
    assert req.tier is None and req.shadow is False


def test_writeback_request_carries_tier_and_shadow():
    req = WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=5, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key="k1",
        tier=AutonomyTier.BOUNDED, shadow=True,
    )
    assert req.tier is AutonomyTier.BOUNDED and req.shadow is True


def test_shadowed_status_exists():
    assert WritebackStatus.SHADOWED.value == "shadowed"


def test_history_entry_round_trips():
    e = HistoryEntry(
        tenant_id="acme", pn="P1", location="YYZ", version=1, status=WritebackStatus.WRITTEN,
        old_values=None, new_values={"rop": 5, "eoq": 10, "safety_stock": 2, "max_stock": 20},
        provenance_id="prov-1", tier=AutonomyTier.BOUNDED, agent_version="agent-spine-v1",
        changed_by_principal="agent-spine", idempotency_key="k1", parent_version=None,
        changed_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    assert HistoryEntry.model_validate_json(e.model_dump_json()) == e


def test_rollback_request_and_result():
    req = RollbackRequest(
        tenant_id="acme", pn="P1", location="YYZ", reason="bad rec",
        requested_at=datetime(2026, 4, 2, tzinfo=UTC),
    )
    assert req.principal == "planner"
    res = RollbackResult(
        tenant_id="acme", pn="P1", location="YYZ", status=RollbackStatus.ROLLED_BACK,
        from_values={"rop": 7}, to_values={"rop": 5}, reverted_from_version=2, new_version=3,
        rolled_back_at=datetime(2026, 4, 2, tzinfo=UTC),
    )
    assert res.status is RollbackStatus.ROLLED_BACK
```

- [ ] **Step 2: Run it, verify it fails** — `cd services/agent-spine && uv run --extra dev pytest tests/writeback/test_contracts_hardening.py -v` → FAIL.

- [ ] **Step 3: Edit `contracts.py`**

Add `SHADOWED = "shadowed"` to `WritebackStatus`. Add `tier`/`shadow` to `WritebackRequest`:

```python
class WritebackRequest(_Base):
    tenant_id: str
    pn: str
    location: str
    rop: NonNegativeInt
    eoq: NonNegativeInt
    safety_stock: NonNegativeInt
    max_stock: NonNegativeInt
    provenance_id: str
    idempotency_key: str = Field(min_length=1)
    tier: AutonomyTier | None = None
    shadow: bool = False
```

Add `shadowed` to `OrchestrationResult` (after `failed`):

```python
    failed: tuple[WritebackResult, ...] = ()
    shadowed: tuple[WritebackResult, ...] = ()
```

Add the new types (near the other writeback contracts):

```python
class RollbackStatus(StrEnum):
    ROLLED_BACK = "rolled_back"
    OUTSIDE_WINDOW = "outside_window"
    NOTHING_TO_REVERT = "nothing_to_revert"


class HistoryEntry(_Base):
    tenant_id: str
    pn: str
    location: str
    version: int  # monotonic per (tenant, pn, location), starting at 1
    status: WritebackStatus
    old_values: dict[str, int] | None
    new_values: dict[str, int]
    provenance_id: str
    tier: AutonomyTier | None
    agent_version: str
    changed_by_principal: str
    idempotency_key: str | None
    parent_version: int | None
    changed_at: datetime


class RollbackRequest(_Base):
    tenant_id: str
    pn: str
    location: str
    reason: str
    principal: str = "planner"
    requested_at: datetime


class RollbackResult(_Base):
    tenant_id: str
    pn: str
    location: str
    status: RollbackStatus
    from_values: dict[str, int] | None = None
    to_values: dict[str, int] | None = None
    reverted_from_version: int | None = None
    new_version: int | None = None
    rolled_back_at: datetime | None = None
    error_message: str | None = None
```

Add `HistoryEntry`, `RollbackRequest`, `RollbackResult`, `RollbackStatus` to `__all__`.

- [ ] **Step 4: Add the Protocol to `target.py`** (import the new types)

```python
from trax_io_spine.contracts import (
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
    WritebackRequest,
    WritebackResult,
    WritebackStatus,
)
```

```python
class AuditedWritebackTarget(WritebackTarget, Protocol):
    """WritebackTarget + provenance history & rollback (the #6 hardening surface)."""

    def get_history(self, *, tenant_id: str, pn: str, location: str) -> tuple[HistoryEntry, ...]: ...
    def rollback(self, req: RollbackRequest) -> RollbackResult: ...
```

- [ ] **Step 5: Run tests, verify pass + ruff clean.** Then run the existing writeback suite to confirm nothing broke: `uv run --extra dev pytest tests/writeback -q`.

- [ ] **Step 6: Commit** — `git add -A services/agent-spine/src/trax_io_spine/contracts.py services/agent-spine/src/trax_io_spine/writeback/target.py services/agent-spine/tests/writeback/test_contracts_hardening.py && git commit -m "#6 writeback: hardening contracts + AuditedWritebackTarget Protocol"`

---

### Task 2: Provenance history in `InMemoryWritebackTarget`

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/writeback/target.py`
- Test: `services/agent-spine/tests/writeback/test_history.py`

**Interfaces:**
- Produces on `InMemoryWritebackTarget`: a per-key `HistoryEntry` ledger recorded on every `WRITTEN` write (monotonic `version` from 1; `parent_version` = the prior `WRITTEN` entry's version or `None`; full provenance from the request); `get_history(*, tenant_id, pn, location) -> tuple[HistoryEntry, ...]`. `.history` (the `WritebackResult` list) is preserved.

- [ ] **Step 1: Write the failing test** — `tests/writeback/test_history.py`

```python
from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import WritebackRequest, WritebackStatus
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(key="k1", *, rop=5, **over):
    base = dict(
        tenant_id="acme", pn="P1", location="YYZ", rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=key, tier=AutonomyTier.BOUNDED,
    )
    base.update(over)
    return WritebackRequest(**base)


def test_first_write_logs_version_1_with_no_parent():
    t = InMemoryWritebackTarget()
    t.write(_req("k1"))
    hist = t.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert len(hist) == 1
    e = hist[0]
    assert e.version == 1 and e.parent_version is None
    assert e.status is WritebackStatus.WRITTEN
    assert e.old_values is None
    assert e.new_values == {"rop": 5, "eoq": 10, "safety_stock": 2, "max_stock": 20}
    assert e.tier is AutonomyTier.BOUNDED and e.provenance_id == "prov-1"
    assert e.idempotency_key == "k1" and e.changed_by_principal == "agent-spine"


def test_second_write_chains_parent_version_and_old_values():
    t = InMemoryWritebackTarget()
    t.write(_req("k1", rop=5))
    t.write(_req("k2", rop=7))
    hist = t.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert [e.version for e in hist] == [1, 2]
    assert hist[1].parent_version == 1
    assert hist[1].old_values == {"rop": 5, "eoq": 10, "safety_stock": 2, "max_stock": 20}
    assert hist[1].new_values["rop"] == 7


def test_idempotent_rewrite_does_not_double_log():
    t = InMemoryWritebackTarget()
    t.write(_req("k1"))
    t.write(_req("k1"))  # same idempotency_key -> cached, no new history
    assert len(t.get_history(tenant_id="acme", pn="P1", location="YYZ")) == 1


def test_get_history_empty_for_unknown_key():
    t = InMemoryWritebackTarget()
    assert t.get_history(tenant_id="acme", pn="ZZZ", location="YYZ") == ()
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement** — in `target.py`, add a module constant and extend `InMemoryWritebackTarget`:

```python
_AGENT_VERSION = "agent-spine-v1"
```

In `__init__`, add the ledger:

```python
        self._history: dict[tuple[str, str, str], list[HistoryEntry]] = {}
```

Add a helper and call it from the `WRITTEN` branch of `write` (right before building the result), recording the entry. Refactor `write`'s applied branch so it appends a `HistoryEntry`:

```python
    def _record(
        self, *, key: tuple[str, str, str], req: WritebackRequest, status: WritebackStatus,
        old_values: dict[str, int] | None, new_values: dict[str, int],
        principal: str, changed_at: datetime,
    ) -> HistoryEntry:
        entries = self._history.setdefault(key, [])
        version = len(entries) + 1
        parent = next((e.version for e in reversed(entries)
                       if e.status is WritebackStatus.WRITTEN), None)
        entry = HistoryEntry(
            tenant_id=key[0], pn=key[1], location=key[2], version=version, status=status,
            old_values=old_values, new_values=new_values, provenance_id=req.provenance_id,
            tier=req.tier, agent_version=_AGENT_VERSION, changed_by_principal=principal,
            idempotency_key=req.idempotency_key, parent_version=parent, changed_at=changed_at,
        )
        entries.append(entry)
        return entry

    def get_history(self, *, tenant_id: str, pn: str, location: str) -> tuple[HistoryEntry, ...]:
        return tuple(self._history.get((tenant_id, pn, location), ()))
```

In the applied-write branch of `write`, after computing `new_values`/`old_values` and before/with building the result, call `self._record(key=key, req=req, status=WritebackStatus.WRITTEN, old_values=old_values, new_values=new_values, principal="agent-spine", changed_at=<the same datetime used for written_at>)`. Use one `now = datetime.now(UTC)` for both `written_at` and `changed_at`. Keep `self.history.append(result)` and `self._seen[...] = result` exactly as before.

- [ ] **Step 4: Run tests, verify pass** — `uv run --extra dev pytest tests/writeback/test_history.py tests/writeback/test_target.py -q` (the existing `test_target.py` must still pass). ruff clean.

- [ ] **Step 5: Commit** — `git commit -m "#6 writeback: per-key provenance HistoryEntry ledger + get_history"`

---

### Task 3: Shadow-mode in `InMemoryWritebackTarget`

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/writeback/target.py`
- Test: `services/agent-spine/tests/writeback/test_shadow.py`

**Interfaces:** `InMemoryWritebackTarget.write` honors `req.shadow`: logs a `SHADOWED` `HistoryEntry`, returns `WritebackResult(status=SHADOWED, old_values=current, new_values=intended)`, and **does not mutate `_levels`** or append to `.history`. The shadow check is after idempotency, before open-order deferral.

- [ ] **Step 1: Write the failing test** — `tests/writeback/test_shadow.py`

```python
from trax_io_spine.contracts import WritebackRequest, WritebackStatus
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(key, *, rop, shadow=False):
    return WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=key, shadow=shadow,
    )


def test_shadow_write_returns_shadowed_and_does_not_apply():
    t = InMemoryWritebackTarget()
    t.write(_req("k1", rop=5))                       # applied
    res = t.write(_req("k2", rop=99, shadow=True))   # shadow
    assert res.status is WritebackStatus.SHADOWED
    assert res.old_values == {"rop": 5, "eoq": 10, "safety_stock": 2, "max_stock": 20}
    assert res.new_values["rop"] == 99
    # a subsequent applied read shows the OLD value: shadow did not mutate
    after = t.write(_req("k3", rop=7))
    assert after.old_values["rop"] == 5  # not 99


def test_shadow_logs_a_shadowed_history_entry_not_in_dot_history():
    t = InMemoryWritebackTarget()
    res = t.write(_req("k1", rop=99, shadow=True))
    hist = t.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert len(hist) == 1 and hist[0].status is WritebackStatus.SHADOWED
    assert t.history == []  # success-only applied list is untouched
    assert res.status is WritebackStatus.SHADOWED
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement** — in `write`, after the `_seen` idempotency check and before the open-order check, add:

```python
        key = (req.tenant_id, req.pn, req.location)
        if req.shadow:
            new_values = {f: getattr(req, f) for f in _FIELDS}
            old_values = self._levels.get(key)
            now = datetime.now(UTC)
            self._record(
                key=key, req=req, status=WritebackStatus.SHADOWED,
                old_values=old_values, new_values=new_values,
                principal="agent-spine", changed_at=now,
            )
            result = WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.SHADOWED, old_values=old_values,
                new_values=new_values, written_at=now,
            )
            self._seen[req.idempotency_key] = result
            return result
```

(The existing `key = ...` line later in `write` becomes redundant — either remove the duplicate or leave the earlier assignment; ensure `key` is defined once before both the open-order and applied branches.)

- [ ] **Step 4: Run tests, verify pass + ruff clean** — `uv run --extra dev pytest tests/writeback -q`.

- [ ] **Step 5: Commit** — `git commit -m "#6 writeback: shadow-mode write (SHADOWED, logged, no mutation)"`

---

### Task 4: Rollback in `InMemoryWritebackTarget`

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/writeback/target.py`
- Test: `services/agent-spine/tests/writeback/test_rollback.py`

**Interfaces:** `InMemoryWritebackTarget(__init__)` gains `rollback_window_days: int = 90` (validated `> 0`, else `ValueError`). `rollback(req: RollbackRequest) -> RollbackResult`: reverts the latest `WRITTEN` entry to its `old_values` within the window; emits a new `WRITTEN` `HistoryEntry` (`parent_version` = reverted entry's version, principal = `req.principal`); returns `ROLLED_BACK`/`OUTSIDE_WINDOW`/`NOTHING_TO_REVERT`.

- [ ] **Step 1: Write the failing test** — `tests/writeback/test_rollback.py`

```python
from datetime import UTC, datetime, timedelta

import pytest

from trax_io_spine.contracts import RollbackRequest, RollbackStatus, WritebackRequest
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(key, *, rop):
    return WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=key,
    )


def _rollback(*, at=None):
    return RollbackRequest(
        tenant_id="acme", pn="P1", location="YYZ", reason="bad rec",
        requested_at=at or datetime.now(UTC),
    )


def test_zero_window_is_rejected():
    with pytest.raises(ValueError):
        InMemoryWritebackTarget(rollback_window_days=0)


def test_rollback_reverts_latest_write_to_prior_values():
    t = InMemoryWritebackTarget()
    t.write(_req("k1", rop=5))
    t.write(_req("k2", rop=7))
    res = t.rollback(_rollback())
    assert res.status is RollbackStatus.ROLLED_BACK
    assert res.from_values["rop"] == 7
    assert res.to_values["rop"] == 5
    assert res.reverted_from_version == 2 and res.new_version == 3
    # the level is back to 5, and a new chained entry was logged
    after = t.write(_req("k3", rop=9))
    assert after.old_values["rop"] == 5
    hist = t.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert hist[2].parent_version == 2  # the rollback entry links to v2


def test_rollback_with_no_prior_write_is_nothing_to_revert():
    t = InMemoryWritebackTarget()
    assert t.rollback(_rollback()).status is RollbackStatus.NOTHING_TO_REVERT


def test_rollback_of_only_first_write_is_nothing_to_revert():
    t = InMemoryWritebackTarget()
    t.write(_req("k1", rop=5))  # old_values is None -> nothing to revert to
    assert t.rollback(_rollback()).status is RollbackStatus.NOTHING_TO_REVERT


def test_rollback_outside_window():
    t = InMemoryWritebackTarget(rollback_window_days=30)
    t.write(_req("k1", rop=5))
    t.write(_req("k2", rop=7))
    far_future = datetime.now(UTC) + timedelta(days=31)
    res = t.rollback(_rollback(at=far_future))
    assert res.status is RollbackStatus.OUTSIDE_WINDOW
    # nothing mutated
    after = t.write(_req("k3", rop=9))
    assert after.old_values["rop"] == 7
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement** — add `rollback_window_days` to `__init__` with validation, and the `rollback` method:

```python
    def __init__(
        self,
        open_orders: set[tuple[str, str, str]] | None = None,
        *,
        rollback_window_days: int = 90,
    ) -> None:
        if rollback_window_days <= 0:
            raise ValueError("rollback_window_days must be > 0")
        self._open_orders = open_orders or set()
        self._levels: dict[tuple[str, str, str], dict[str, int]] = {}
        self._seen: dict[str, WritebackResult] = {}
        self.history: list[WritebackResult] = []
        self._history: dict[tuple[str, str, str], list[HistoryEntry]] = {}
        self._window = rollback_window_days
```

```python
    def rollback(self, req: RollbackRequest) -> RollbackResult:
        key = (req.tenant_id, req.pn, req.location)
        entries = self._history.get(key, [])
        latest = next(
            (e for e in reversed(entries) if e.status is WritebackStatus.WRITTEN), None
        )
        base = dict(tenant_id=req.tenant_id, pn=req.pn, location=req.location)
        if latest is None or latest.old_values is None:
            return RollbackResult(**base, status=RollbackStatus.NOTHING_TO_REVERT)
        if req.requested_at - latest.changed_at > timedelta(days=self._window):
            return RollbackResult(**base, status=RollbackStatus.OUTSIDE_WINDOW)

        current = self._levels.get(key)
        to_values = dict(latest.old_values)
        self._levels[key] = dict(to_values)
        # _record computes parent_version as the most-recent WRITTEN entry = `latest` (the one
        # being reverted), which is exactly the link we want — no correction needed.
        entry = self._record(
            key=key,
            req=WritebackRequest(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                rop=to_values["rop"], eoq=to_values["eoq"],
                safety_stock=to_values["safety_stock"], max_stock=to_values["max_stock"],
                provenance_id=f"rollback:{latest.provenance_id}",
                idempotency_key=f"rollback:{latest.version}:{req.requested_at.isoformat()}",
                tier=latest.tier,
            ),
            status=WritebackStatus.WRITTEN, old_values=current, new_values=to_values,
            principal=req.principal, changed_at=req.requested_at,
        )
        return RollbackResult(
            **base, status=RollbackStatus.ROLLED_BACK, from_values=current,
            to_values=to_values, reverted_from_version=latest.version,
            new_version=entry.version, rolled_back_at=req.requested_at,
        )
```

Add `from datetime import timedelta` (extend the existing `from datetime import UTC, datetime` import to include `timedelta`).

- [ ] **Step 4: Run tests, verify pass + ruff clean** — `uv run --extra dev pytest tests/writeback -q`.

- [ ] **Step 5: Commit** — `git commit -m "#6 writeback: rollback with configurable non-zero window (revert + chained entry)"`

---

### Task 5: `fake_emro` (backed by `InMemoryWritebackTarget`) + `RestWritebackClient`

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/writeback/fake_emro.py`
- Modify: `services/agent-spine/src/trax_io_spine/writeback/rest.py`
- Test: `services/agent-spine/tests/writeback/test_fake_emro_hardening.py`

**Interfaces:**
- `create_fake_emro(open_orders=None, *, rollback_window_days=90)` — backed by one `InMemoryWritebackTarget`; routes: `POST /inventory-levels` (honors `shadow`; 200 for written/shadowed, 409 for deferred), `GET /history?tenant_id&pn&location` (full `HistoryEntry` list), `POST /rollback` (`RollbackResult`).
- `RestWritebackClient` implements `AuditedWritebackTarget`: `write` sends `tier`/`shadow` and maps `200`+`status=="shadowed"`→`SHADOWED`; `get_history(*, tenant_id, pn, location) -> tuple[HistoryEntry, ...]`; `rollback(req) -> RollbackResult`.

- [ ] **Step 1: Write the failing test** — `tests/writeback/test_fake_emro_hardening.py` (requires the `emro` extra)

```python
from datetime import UTC, datetime

import httpx

from trax_io_spine.contracts import RollbackRequest, RollbackStatus, WritebackRequest, WritebackStatus
from trax_io_spine.writeback.fake_emro import create_fake_emro
from trax_io_spine.writeback.rest import RestWritebackClient


def _client(app):
    return RestWritebackClient(client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                        base_url="http://emro.test"))


def _req(key, *, rop, shadow=False):
    return WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=key, shadow=shadow,
    )


def test_applied_write_then_history_round_trips():
    c = _client(create_fake_emro())
    assert c.write(_req("k1", rop=5)).status is WritebackStatus.WRITTEN
    assert c.write(_req("k2", rop=7)).status is WritebackStatus.WRITTEN
    hist = c.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert [e.version for e in hist] == [1, 2]
    assert hist[1].old_values["rop"] == 5 and hist[1].new_values["rop"] == 7


def test_shadow_write_over_the_wire_is_shadowed_and_not_applied():
    c = _client(create_fake_emro())
    assert c.write(_req("k1", rop=5)).status is WritebackStatus.WRITTEN
    res = c.write(_req("k2", rop=99, shadow=True))
    assert res.status is WritebackStatus.SHADOWED
    assert c.write(_req("k3", rop=7)).old_values["rop"] == 5  # shadow didn't apply


def test_rollback_over_the_wire():
    app = create_fake_emro()
    c = _client(app)
    c.write(_req("k1", rop=5))
    c.write(_req("k2", rop=7))
    res = c.rollback(RollbackRequest(
        tenant_id="acme", pn="P1", location="YYZ", reason="bad",
        requested_at=datetime.now(UTC),
    ))
    assert res.status is RollbackStatus.ROLLED_BACK
    assert res.to_values["rop"] == 5
```

- [ ] **Step 2: Run it, verify it fails** — `uv run --extra dev --extra emro pytest tests/writeback/test_fake_emro_hardening.py -v`.

- [ ] **Step 3: Reimplement `fake_emro.py`** (delegate to one `InMemoryWritebackTarget`)

```python
"""In-memory FastAPI mock of the eMRO Writeback REST surface (#6).

Backed by a single InMemoryWritebackTarget so the mock and the in-memory reference share one
behavior definition (no drift). Behind the `emro` extra (FastAPI imported lazily).
"""

from __future__ import annotations

from typing import Any

from trax_io_spine.contracts import (
    RollbackRequest,
    WritebackRequest,
    WritebackStatus,
)
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def create_fake_emro(
    open_orders: set[tuple[str, str, str]] | None = None, *, rollback_window_days: int = 90
) -> Any:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    target = InMemoryWritebackTarget(open_orders, rollback_window_days=rollback_window_days)
    app = FastAPI(title="fake_emro")

    @app.post("/inventory-levels")
    def write_level(body: dict[str, Any]) -> JSONResponse:
        result = target.write(WritebackRequest.model_validate(body))
        code = 409 if result.status is WritebackStatus.DEFERRED_OPEN_ORDER else 200
        return JSONResponse(result.model_dump(mode="json"), status_code=code)

    @app.get("/history")
    def get_history(tenant_id: str, pn: str, location: str) -> JSONResponse:
        entries = target.get_history(tenant_id=tenant_id, pn=pn, location=location)
        return JSONResponse([e.model_dump(mode="json") for e in entries])

    @app.post("/rollback")
    def rollback(body: dict[str, Any]) -> JSONResponse:
        result = target.rollback(RollbackRequest.model_validate(body))
        return JSONResponse(result.model_dump(mode="json"))

    return app
```

- [ ] **Step 4: Extend `rest.py`** — map shadow, add `get_history` + `rollback` (all sync wrappers over the async client, like `write`):

```python
from trax_io_spine.contracts import (
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
    WritebackRequest,
    WritebackResult,
    WritebackStatus,
)
```

In `_async_write`, change the 200 branch to read the status:

```python
        if resp.status_code == 200:
            body = resp.json()
            status = (
                WritebackStatus.SHADOWED if body.get("status") == "shadowed"
                else WritebackStatus.WRITTEN
            )
            return WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location, status=status,
                old_values=body.get("old_values"), new_values=body.get("new_values"),
            )
```

And `write` posts the full request (already does `req.model_dump()`; change to `mode="json"` so `tier`/`datetime` serialize):

```python
        resp = await self._client.post(
            f"{self._base_url}/inventory-levels", json=req.model_dump(mode="json")
        )
```

Add the two new methods:

```python
    def get_history(
        self, *, tenant_id: str, pn: str, location: str
    ) -> tuple[HistoryEntry, ...]:
        return asyncio.run(self._async_history(tenant_id=tenant_id, pn=pn, location=location))

    async def _async_history(
        self, *, tenant_id: str, pn: str, location: str
    ) -> tuple[HistoryEntry, ...]:
        resp = await self._client.get(
            f"{self._base_url}/history",
            params={"tenant_id": tenant_id, "pn": pn, "location": location},
        )
        return tuple(HistoryEntry.model_validate(e) for e in resp.json())

    def rollback(self, req: RollbackRequest) -> RollbackResult:
        return asyncio.run(self._async_rollback(req))

    async def _async_rollback(self, req: RollbackRequest) -> RollbackResult:
        resp = await self._client.post(
            f"{self._base_url}/rollback", json=req.model_dump(mode="json")
        )
        return RollbackResult.model_validate(resp.json())
```

- [ ] **Step 5: Run tests, verify pass** — `uv run --extra dev --extra emro pytest tests/writeback -q` (the existing `test_fake_emro.py` + `test_rest.py` must still pass; if `test_fake_emro.py` asserted the old minimal `/history` tuple shape, update it to the `HistoryEntry` shape — this is an intended contract upgrade, note it in the report). ruff clean.

- [ ] **Step 6: Commit** — `git commit -m "#6 writeback: fake_emro backed by InMemory (history/shadow/rollback) + RestWritebackClient parity"`

---

### Task 6: Supervisor `shadow` mode + `trax-io-spine run --shadow`

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/supervisor.py`
- Modify: `services/agent-spine/src/trax_io_spine/cli.py`
- Test: `services/agent-spine/tests/test_shadow_run.py`

**Interfaces:** `to_writeback_request(rec, *, idempotency_key, tier=None, shadow=False)`; `Supervisor.__init__(..., shadow: bool = False)`; `run` routes `SHADOWED` results into a `shadowed` bucket → `OrchestrationResult.shadowed` + `summary["shadowed"]`. `trax-io-spine run` gains `--shadow/--no-shadow`.

- [ ] **Step 1: Write the failing test** — `tests/test_shadow_run.py`

```python
from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.supervisor import Supervisor
from trax_io_spine.writeback.target import InMemoryWritebackTarget

_SAMPLE = (
    Path(__file__).resolve().parents[1] / "recommendation-engine" / "examples" / "extract_sample"
)


def test_shadow_run_logs_but_applies_nothing():
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    wb = InMemoryWritebackTarget()
    sup = Supervisor(feature_store=fs, inventory_state=inv, writeback=wb, shadow=True)
    res = sup.run(tenant=TenantContext(tenant_id=tid), keys=keys, now=datetime(2026, 4, 1, tzinfo=UTC))
    assert res.summary["written"] == 0
    assert res.summary["shadowed"] >= 1
    assert len(res.shadowed) == res.summary["shadowed"]
    assert wb.history == []  # nothing applied


def test_default_run_is_unchanged():
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    sup = Supervisor(feature_store=fs, inventory_state=inv)
    res = sup.run(tenant=TenantContext(tenant_id=tid), keys=keys, now=datetime(2026, 4, 1, tzinfo=UTC))
    assert res.summary["shadowed"] == 0
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Edit `supervisor.py`**

`to_writeback_request` gains `tier`/`shadow`:

```python
def to_writeback_request(
    rec: Recommendation, *, idempotency_key: str,
    tier: AutonomyTier | None = None, shadow: bool = False,
) -> WritebackRequest:
    if rec.policy is None:  # pragma: no cover
        raise ValueError("recommendation has no policy to write")
    p = rec.policy
    return WritebackRequest(
        tenant_id=rec.tenant_id, pn=rec.part_number, location=rec.current_location,
        rop=p.rop, eoq=p.eoq, safety_stock=p.safety_stock, max_stock=p.max_stock,
        provenance_id=p.provenance_id, idempotency_key=idempotency_key,
        tier=tier, shadow=shadow,
    )
```

(Import `AutonomyTier` in `supervisor.py` if not already: `from trax_io_reco.contracts.enums import AutonomyTier`.)

`__init__` gains `shadow`:

```python
        service: Any = None,
        shadow: bool = False,
    ) -> None:
        ...
        self._writeback: WritebackTarget = writeback or InMemoryWritebackTarget()
        self._shadow = shadow
```

In `run`, add a `shadowed` list, pass `tier`/`shadow` to the request, and route the result:

```python
            shadowed: list[WritebackResult] = []
            ...
                else:  # APPROVED_FOR_WRITE
                    idem = (...)
                    result = self._writeback.write(
                        to_writeback_request(
                            rec, idempotency_key=idem, tier=outcome.tier, shadow=self._shadow,
                        )
                    )
                    if result.status is WritebackStatus.WRITTEN:
                        written.append(result)
                    elif result.status is WritebackStatus.SHADOWED:
                        shadowed.append(result)
                    elif result.status is WritebackStatus.DEFERRED_OPEN_ORDER:
                        deferred.append(result)
                    else:
                        failed.append(result)
```

Add `"shadowed": len(shadowed)` to the `summary` Counter, and `shadowed=tuple(shadowed)` to the `OrchestrationResult(...)` construction.

- [ ] **Step 4: Edit `cli.py`** — add `--shadow` to the `run` command and pass it through:

```python
    apply: bool = typer.Option(False, "--apply/--dry-run"),
    shadow: bool = typer.Option(False, "--shadow/--no-shadow"),
    writeback_url: str = typer.Option("http://localhost:9000", "--writeback-url"),
) -> None:
    ...
    supervisor = Supervisor(
        feature_store=fs, inventory_state=inv, writeback=target, shadow=shadow,
    )
```

- [ ] **Step 5: Run tests, verify pass** — `uv run --extra dev pytest tests/test_shadow_run.py -v`, then the **full** agent-spine suite `uv run --extra dev --extra emro pytest -q` (all prior tests + new). If any existing test asserts the supervisor summary dict exactly, add `"shadowed": 0` to it (intended). ruff clean. Verify the CLI live: `uv run trax-io-spine run --extract-dir ../recommendation-engine/examples/extract_sample --tenant acme --dry-run --shadow` prints a summary with `shadowed` > 0 and `written` 0.

- [ ] **Step 6: Commit** — `git commit -m "#6 writeback: supervisor shadow mode + trax-io-spine run --shadow"`

---

## Post-implementation (controller, after final review)

- ADR `docs/adr/2026-06-28-0010-audited-writeback-seam.md` (AuditedWritebackTarget seam; provenance/rollback/shadow against fake_emro; fake_emro backed by InMemory to avoid drift; eMRO auth/business-rules/persistence/bulk-rollback/events deferred).
- CLAUDE.md: note `trax-io-spine run --shadow` + the `AuditedWritebackTarget` surface.
- ROADMAP #6: mark provenance-history / rollback / shadow done; keep real-eMRO deployment + auth + bulk-rollback + persistence deferred.
- TASKS.md session entry. Merge `feat/writeback-hardening` → main, push, delete branch (restore any unrelated lockfile churn first).

## Self-Review

- **Spec coverage:** §3 contracts → Task 1; §4.1 history → Task 2, shadow → Task 3, rollback → Task 4; §4.2/4.3 rest+fake → Task 5; §5 supervisor/CLI → Task 6. All covered.
- **Type consistency:** `HistoryEntry`/`RollbackRequest`/`RollbackResult`/`RollbackStatus`/`AuditedWritebackTarget` names + fields consistent across tasks; `_record` used by write (Task 2), shadow (Task 3), rollback (Task 4); `WritebackStatus.SHADOWED` routed in Task 6.
- **Backward-compat:** new `WritebackRequest` fields defaulted; `write()` base signature unchanged; `.history` preserved; `rollback_window_days` defaulted. The one identified ripple (an existing exact-summary or old-`/history`-shape assertion) is called out inline for the implementer to update.
- **Placeholders:** none — every step has runnable code. The rollback entry's `parent_version` falls out of `_record` for free: it computes parent as the most-recent `WRITTEN` entry, which is exactly the reverted entry (`latest`).
