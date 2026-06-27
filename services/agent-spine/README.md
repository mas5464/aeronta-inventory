# Trax IO Agent Spine — service

Deterministic orchestration core for sub-project #4. Wires the real Feature Store (#2)
and Recommendation Engine (#11) into an enforced, written-or-queued outcome.

## Dev setup
```bash
cd services/agent-spine
uv sync --extra dev --extra emro
uv run pytest
uv run ruff check .
```

The `emro` extra pulls FastAPI for the `fake_emro` writeback harness used by the
writeback + integration tests. Core tests run without it.
