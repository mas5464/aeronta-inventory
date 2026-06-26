# Lessons Log

Patterns learned from corrections. Updated automatically during sessions.

---

## 2026-04-16 — `uv run pytest` needs explicit dev group flag

**Context:** Phase 1 scaffold test suites initially appeared broken — ModuleNotFoundError for the project's own package, missing test-only deps.

**Root cause:** `uv run pytest` only installs the project's *main* dependencies. If pytest or other test-only tools live in `[project.optional-dependencies] dev` or `[dependency-groups] dev`, they're not on the path and the package itself may not be installed into the run environment.

**Rule:** Always invoke project test suites with the dev-extras flag:
- PEP 621 `[project.optional-dependencies]` → `uv run --extra dev pytest`
- PEP 735 `[dependency-groups]` → `uv run --group dev pytest`

**How to apply:** When a `uv run pytest` fails with `ModuleNotFoundError` for either the project or a test dep, *first* retry with `--extra dev` / `--group dev` before assuming a real bug. Pick the right flag by reading `pyproject.toml`.

---

## 2026-04-16 — AWS CDK L1 `CfnEventDataStore` uses `tags`, not `tags_raw`

**Context:** `aws_cloudtrail.CfnEventDataStore` was instantiated with `tags_raw=[{"key": ..., "value": ...}]` which fails at synth with `TypeError: got an unexpected keyword argument 'tags_raw'`.

**Root cause:** In `aws-cdk-lib` 2.147+, L1 constructs expose tags as `tags: Optional[Sequence[CfnTag]]`. `tags_raw` is only exposed on a small subset of services (e.g., `aws_forecast`, `aws_timestream`). `CfnEventDataStore` is not one of them.

**Rule:** When tagging L1 CDK constructs, use `tags=[CfnTag(key=..., value=...)]` (import `CfnTag` from `aws_cdk`). Don't assume `tags_raw` is available — inspect the constructor signature if unsure.

**How to apply:** Any time we add tags to a `Cfn*` construct, import `CfnTag` and pass a list of `CfnTag` objects. Verify with `inspect.signature(CfnX.__init__).parameters` if the construct is unfamiliar.

---

## 2026-04-17 — uv cross-project **editable** path deps don't expose src-layout packages

**Context:** `services/recommendation-engine` depends on `services/feature-store` via `[tool.uv.sources] { path = "../feature-store", editable = true }`. `uv pip list` showed the package installed, but `import trax_io_feature_store` failed with `ModuleNotFoundError`. The editable `.pth` (`_editable_impl_trax_io_feature_store.pth`) contained the correct `.../feature-store/src` path and the package imported fine via `PYTHONPATH=.../feature-store/src`, yet that path never landed on `sys.path` at runtime (the consuming project's own editable `.pth` worked, the dependency's did not).

**Root cause:** uv's editable finder for a *cross-project* src-layout path dependency did not reliably add the dependency's `src` dir to `sys.path` (the project's own editable install works; a sibling path-dep's editable `.pth` silently no-ops here). Likely interacts with the space in the repo path (`Inventory Opmimizer`).

**Rule:** For cross-project path dependencies on a src-layout sibling, prefer a **non-editable** path source: `[tool.uv.sources] trax-io-x = { path = "../x" }` (no `editable = true`). uv then builds the wheel and copies real files into site-packages — robust, no `.pth` finder reliance. Trade-off: live edits to the dep require `uv sync --reinstall-package <dist-name>` — acceptable when depending on a stable contract.

**How to apply:** If a sibling package shows as installed but its module won't import, check `.venv/.../site-packages/_editable_impl_*.pth` vs `sys.path`; if the dep's src isn't on `sys.path`, drop `editable = true` and `uv sync --reinstall-package <dist-name>`.

**Also affects the project's OWN editable install** (the workspace package, which uv always installs editable). Symptom: `uv run <console-script>` (e.g. `trax-io-reco`) fails with `ModuleNotFoundError: No module named '<pkg>'` even though `pytest` passes (pytest uses `pythonpath=["src"]`, bypassing the `.pth`). The `.pth` gets into a broken state across `uv sync` churn. Workaround: `uv sync --reinstall-package <dist-name>` then invoke with `uv run --no-sync python -m <pkg>.cli ...` (a re-sync can re-break the `.pth`). The CLI logic itself is verified by the in-process `CliRunner` tests regardless.
