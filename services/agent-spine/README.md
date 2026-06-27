# Trax IO Agent Spine — service

Deterministic orchestration core for sub-project #4. Wires the real Feature Store (#2)
and Recommendation Engine (#11) into an enforced, written-or-queued outcome.

## Dev setup
```bash
cd services/agent-spine
uv sync --extra dev --extra emro
uv run --extra dev pytest
uv run --extra dev ruff check .
```

The `emro` extra pulls FastAPI for the `fake_emro` writeback harness used by the
writeback + integration tests. Core tests run without it.

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
