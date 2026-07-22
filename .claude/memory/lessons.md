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

**DURABLE FIX for test collection: every package's `pyproject.toml` MUST set `[tool.pytest.ini_options] pythonpath = ["src"]`.** Without it, pytest relies on the flaky editable `.pth` and intermittently fails collection with `ModuleNotFoundError` for the package's own module (hit on `tools/nightly-extract`, which lacked it — added 2026-04-17). feature-store, recommendation-engine already had it. When scaffolding any new src-layout package, add `pythonpath=["src"]` up front.

---

## 2026-06-26 — PySpark Glue transforms: string-typed extract + ANSI mode + cast rounding

**Context:** New feature-store Glue jobs (`vendor_economics`/`part_attributes`/`criticality`) passed their tests, but an adversarial review caught the tests feeding **native ints** while the real eMRO extract (`examples/extract_sample/*.json`) delivers **every numeric field as a string** (`"shelflife":"0"`, `"price":"4200"`). That surfaced two deeper gaps that also affected the already-committed `stock_position`/`current_policy`/`demand_history` jobs.

**Root causes (three, compounding):**
1. **Test-fidelity:** native-int test inputs never exercise the production string→numeric cast path.
2. **ANSI mode mismatch:** local Spark 4.x defaults `spark.sql.ansi.enabled=true` (a bad cast *throws*, aborting the job); production **Glue 4.0 / Spark 3.3 defaults ANSI off** (bad cast → null). Local tests and prod disagree on malformed input, and the forgiving reco bridge (`_i`/`_f`/`_dec`, which return a default) is only matched under ANSI-off.
3. **Truncate vs round:** under ANSI-off, `cast("365.5" as int)` *truncates* → 365, but the bridge does `int(round(float(v)))` = **366** (Python's banker's rounding). Bare `cast(IntegerType())` diverges from the shadow-mode bridge on any fractional value. `demand_history` had a subtler variant — it summed raw `HistoryAmount` then cast (sum-then-truncate) vs the bridge's round-each-row-then-sum (two `"2.5"` issues → bridge 4, bug 5).

**Rules (apply to every PySpark transform over extract data):**
- **Feed numeric test inputs as strings**; pin `spark.sql.ansi.enabled=false` in the test `conftest` SparkSession so tests reflect Glue 4.0.
- **Call `disable_ansi_mode(spark)` in every job `main()`** — don't rely on the Glue default; it makes bad casts null like the bridge instead of crashing the job.
- **Never `cast(IntegerType())` a raw string field directly.** Use the shared `glue/_common.coerce_int(col, default)` = `coalesce(bround(col.cast(double)).cast(int), default)`. `bround` is Spark's HALF_EVEN, exactly matching Python's `round` (verified equivalent to the bridge `_i` across 23 edge inputs incl. `2.5→2`, `39.5→40`, `-2.5→-2`, `" 7 "→7`, `"1e3"→1000`, `""/"abc"/None→default`).
- **Round per row before aggregating** (wrap the field in `coerce_int` *inside* the `sum`), not after — round-then-sum ≠ sum-then-round.
- **Decimal fields are fine** with `cast("decimal(18,4)")` (handles decimal points; nulls on bad under ANSI-off) — truncation is integer-cast-specific.

**How to apply:** when adding a Glue feature-group job, route integer coercions through `coerce_int`, call `disable_ansi_mode` in `main()`, and write tests with string-typed numerics plus a fractional-string regression row asserting round-not-truncate. The decisive check is an empirical equivalence battery: same string inputs through `coerce_int` (real Spark, ANSI off) vs the bridge `_i`, assert exact agreement.

---

## Deterministic SDD workflows: every conditional branch needs its own fix-loop wrapper (2026-06-27, #3)
When driving a subagent-driven build as a `Workflow` script (per-task implement → review → fix loop), the **main loop** had the fix-loop, but a **conditional task** appended outside the loop (`if (schemathesisOk) { impl; review; push }`) did NOT — so that task's failing review (a critical + important finding) was never acted on and shipped un-fixed. The controller's final-review pass caught it, but that's luck, not design.

**How to apply:** in workflow SDD scripts, factor the implement→review→fix→re-review sequence into ONE reusable function and call it for every task, including conditional/optional ones. Never inline a bare `impl; review` for a side-branch. Also: a conditional task gated on a probe (e.g. "does dep X install?") should still go through review — and if the probe result makes the task pointless (the dep is unusable), prefer *not running it* over running it half-checked.

Also reaffirmed: a contract-fidelity harness must test against the contract's **own published examples verbatim** (`test_contract_examples.py`), or it can silently drift from the very contract it exists to lock (here: `transaction_no` typed `str` while the contract emits integer `88412`).

## macOS: DYLD_* env vars vanish through venv console scripts (repo path has a space)
- **Symptom:** `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run … pytest` can't load native dylibs (weasyprint → "cannot load library 'libgobject-2.0-0'", fallback path never tried), while `uv run … python -c "import weasyprint"` works.
- **Cause:** the repo path contains a space ("Inventory Opmimizer"), so venv console scripts (`.venv/bin/pytest`) are `/bin/sh` exec-wrappers (shebangs can't contain spaces). macOS SIP strips `DYLD_*` env vars whenever an Apple-signed binary (`/bin/sh`) execs the next process.
- **Fix:** invoke module-style — `uv run … python -m pytest …` — for anything that needs `DYLD_*` (weasyprint/pango). Docker (Debian, apt pango) is unaffected.

---

## `[[icloud-sync-conflicts]]`: iCloud-synced repo path silently corrupts `.venv` and inflates test counts
- **Symptom (struck 4+ times across the 2026-06-27/28 sessions):** stray ` 2.py`/` 2.pth`/` 3.lock`/`.pyc` conflict-copy duplicates appear inside the repo and inside `.venv` — sometimes re-collected by pytest (once inflating a reported count from 112→132 by re-running the writeback tests twice), sometimes breaking `ruff` with an `N999` invalid-module-name error on the ` 2.py` filename, and at least twice breaking the editable install / CLI import entirely until a clean `rm -rf .venv && uv sync`.
- **Root cause:** the working copy lived under an iCloud Drive-synced folder (`Documents/Claude/Projects/…`). iCloud's conflict-resolution creates numbered-suffix duplicate files whenever it detects concurrent writes to the same path (agent processes writing rapidly count as "concurrent" to iCloud's sync daemon), and it does this silently — no error, just extra files that a build or test collector can pick up.
- **Fix applied:** the repo was moved out of iCloud sync entirely (now under `~/Projects/…`, not `~/Documents/Claude/Projects/…`) — this is the durable fix, not a per-incident cleanup.
- **Rule:** if a project's working copy must live under `~/Documents`, `~/Desktop`, or any other iCloud Drive-monitored folder, treat inflated/inconsistent test counts, an `N999` ruff error on a suffixed filename, or an editable-install `ModuleNotFoundError` that wasn't there yesterday as symptoms of iCloud duplication *first* — search for ` 2.`/` 3.` suffixed files in the repo and `.venv` before debugging the "real" failure. The durable fix is de-syncing the repo from iCloud (move it or exclude the folder), not repeatedly cleaning duplicates by hand.

---

## Background-agent notifications: verify task-id before trusting the report (2026-07-06)

**Context:** During a full-product UAT pass, two contradictory `<task-notification>`s arrived claiming to be the same `apps/web` retry dispatch — one said PASS (77/80, 0 fail), the other said FAIL (68/74, 1 real bug), with genuinely different case counts and evidence depth. Taking the PASS one at face value, I had already written it into `TASKS.md` and a new guide doc's "Quality Posture" table and staged them for `git commit` before the second notification arrived. A near-identical thing happened earlier the same session: a `<task-notification>` referenced a background agent (`add557f717ad24e70`) that turned out to be a stale, silently-dead orphan from before a context compaction — its "still running" status was never real.

**Root cause:** this environment's background-task plumbing can deliver notifications that don't correspond to a live, currently-owned dispatch — orphaned leftovers from before a compaction, or (as here) some duplicate/stray execution under an unrecorded id. Nothing in the notification's framing distinguishes a trustworthy report from a stray one.

**Rule:** when a `<task-notification>`'s `task-id` doesn't match an `agentId` you actually recorded from your own `Agent` tool call this session, distrust it by default — don't average two contradictory reports or default to the more convenient one. Cross-check: (1) does the task-id match a real dispatch you made? `TaskOutput({task_id, block:false})` returns "No task found" for dead/foreign ids; (2) for anything about to be committed, documented, or reported as a verified fact (test pass/fail, "0 bugs found"), reproduce the specific claim live yourself before trusting either report — here, a 6-second live wait against the real running app settled it in one shot, on both the suspect CORS path and, decisively, the same-origin Docker path with a clean 404.

**How to apply:** never let "I already told the user X" create pressure to avoid re-checking X when new, contradictory information arrives — silently shipping a false "PASS" into permanent history is worse than a delayed, corrected answer. If a fix is needed, CLAUDE.md's "just fix it" autonomous-bug-fixing rule still applies — this isn't a reason to stop and ask, just a reason to verify before writing down a verdict as fact.

---

## An observed edge-case UX gap belongs in the recorded-Minors list, not a passing mention (2026-07-06)

**Context:** During Wave 2 (apps/web writeback rollback) live verification, I noticed that a rollback returning `nothing_to_revert`/`outside_window` (valid BFF results with `error_message: null`) left the confirm dialog open with no message — the planner gets no feedback. I mentioned it once in prose to the user ("below the threshold of anything to fix now") but did NOT add it to the ledger's per-slice Minor list that the final whole-branch review triages. The opus final review then didn't flag it (it verified the loop was "coherent" — the dialog "stays open, not silently closing" was technically true, so the missing *message* slipped past). A Codex PR reviewer caught it as P2 post-merge-request, and it was a legitimate, correct catch.

**Rule:** when I observe a real behavioral/UX gap during verification — even a rare-path one I judge low-priority — record it in the SDD progress ledger's Minor list for that slice, so the final whole-branch review explicitly adjudicates it. A one-off prose mention to the user is not durable and doesn't reliably reach the final reviewer.

**How to apply:** if I catch myself thinking "that's a real gap but not worth fixing now," that is precisely the signal to write it into the ledger as a Minor (with file:line), not to let it live only in a chat sentence. The final review is the designed place to decide fix-now-vs-defer; give it the input.

## Uncommitted work is not safe while subagents run (2026-07-07)
**What happened:** Session-authored ROADMAP.md/TASKS.md edits (Phases 2–6 expansion, deck entry) were left uncommitted "for the user to review" while a 13-task subagent-driven build ran in the same worktree. Somewhere mid-run the working tree was cleaned (a raced implementer draft was stood down / tree restored) and the uncommitted edits were silently lost; the loss surfaced only when the bookkeeping task's ROADMAP commit was suspiciously small.
**Rule:** Commit doc/tracker edits IMMEDIATELY after making them — a commit can always be amended/reverted, but an uncommitted edit in a worktree where implementer subagents run is one `git checkout -- .` away from gone. If work must stay uncommitted, snapshot it (`git stash push -m` or a copy in .superpowers/) before dispatching any implementer. Also: verify tracker diffs after any subagent that touches shared files (`git show --stat` on their commits — a too-small diff on a file you edited is the tell).

## Railway ignores non-standard config filenames — force the Dockerfile builder per-service (2026-07-21)
**What happened (C3 Task 7 live deploy):** After merging C2/C3 code, `railway up` for the `bff` and `worker` services kept reporting **"Deploy failed"** while the old (pre-C3) image stayed live (healthz 200 but no C3 ingest routes in `/openapi.json`). Hours of misdiagnosis: I assumed a stale deploy, then a missing dependency (`openpyxl`), then a runtime crash. A **local `docker build` of the exact Dockerfile succeeded in 8s** and the image booted against the live prod env (healthz 200, all 3 ingest routes) — proving the code/image were fine. The real signal only appeared when I streamed the *failed deployment's* build logs by id (`railway logs -b <deploymentId>`, NOT the default which shows the last **successful** deploy): **`Railpack could not determine how to build the app`** — Railway was using its **railpack auto-builder**, not the Dockerfile, and railpack can't build a monorepo root.
**Root cause:** the repo's Railway config lived at `deploy/railway-bff.json` / `deploy/railway-worker.json` — **non-standard names Railway never auto-reads**. Railway only auto-applies a `railway.json`/`railway.toml` at the **archive/build-context root**, or a per-service dashboard "Config-as-Code path". Neither was set (and no root config exists), so both the Dockerfile builder AND the `startCommand` in those JSONs were silently ignored. `railway up` fell back to railpack → fail.
**Fix (all CLI, persistent service variables — no dashboard needed):**
1. `RAILWAY_DOCKERFILE_PATH=deploy/bff.Dockerfile` on `bff`, `=deploy/worker.Dockerfile` on `worker` — forces the Dockerfile builder and picks the right Dockerfile per service. (Both services share one image but need different entrypoints, so I added `deploy/worker.Dockerfile` = the bff image with `CMD python -m trax_io_spine.pg.worker`.)
2. `PORT=8000` on `bff` — with the JSON `startCommand` dead, the container runs the Dockerfile CMD (hardcoded `--port 8000`); Railway's proxy targets whatever `PORT` says, so pin it to 8000 to match (otherwise: healthz **502** — container healthy on :8000 but proxy hits the wrong port).
3. `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` on `worker` — the C3 ingest handler reads them at job time (`os.environ[...]`, KeyError otherwise) to download uploads from Storage; they existed on `bff` but not `worker`.
**Rules:**
- **Diagnose a "Deploy failed" by the FAILED deployment's own build logs** (`railway logs -b <deploymentId>` using the id from `railway up`'s output URL) — the default `railway logs -b` shows the last *successful* deploy and will lie to you (you'll see "healthcheck succeeded" from an old image).
- **Reproduce the build locally** (`docker build -f <dockerfile> .`) early — it isolates code/image faults from platform/config faults in one step.
- **`deploy/railway-*.json` is decorative unless referenced.** Railway only reads root `railway.json`/`railway.toml` or a dashboard config path. Prefer `RAILWAY_DOCKERFILE_PATH` (+ a per-service Dockerfile) for monorepos — it's the CLI-settable, persistent way to pin the builder without the dashboard.
- **A 502 on a "healthy" Railway service = port mismatch**, not a crash — check the container binds the port Railway proxies to (`PORT`).
