# AGENTS.md

Trax IO / Aeronta Inventory — a multi-tenant, AI-driven airline spares inventory optimizer.
The authoritative developer reference is [CLAUDE.md](CLAUDE.md): it holds the full run/test/lint
command matrix per package. This file only adds durable, non-obvious operating notes for agents.

## Cursor Cloud specific instructions

The primary product is **two processes**: the FastAPI **BFF** (`services/agent-spine`, imports the
four sibling Python packages as non-editable path deps) and the React **web UI** (`apps/web`).
Everything else (Java writeback, Deno billing functions, Astro marketing site, Postgres/Supabase
worker paths) is optional and not needed for the core inventory-optimizer demo. Standard per-package
commands live in `CLAUDE.md`; the notes below are only the gotchas.

### Python version — use 3.12 (do NOT use 3.14)
- All Python packages are set up with **`uv` on Python 3.12** (the base image's system Python). `uv`
  lives at `~/.local/bin` (on PATH via `~/.bashrc`).
- Do **not** run these on Python 3.14: the pinned `statsforecast==2.0.1` (via `services/forecasting`)
  has no 3.14 wheel and fails to build from source, which breaks `forecasting`, `feature-store`, and
  `agent-spine` installs entirely.
- **Known pre-existing test failures on 3.12 (not an environment problem):** the code uses
  `uuid.uuid7()`, which only exists in Python 3.14's stdlib (see
  `services/event-publisher/src/trax_io_event_publisher/ids.py`). This contradicts the
  3.12-pinned scientific stack, so on 3.12 the event-ID paths raise `AttributeError`:
  `services/event-publisher` (~46 tests) and `services/agent-spine` `tests/event_lane/` +
  `tests/test_cli_ingest.py` (~13 tests) fail. The core read/approve/reject/dashboard/BVR loop
  does **not** touch `uuid7` and works fine.

### Running the primary product locally (no Docker)
- Docker is **not installed** in this VM, so the root `docker-compose.yml` path, the
  `agent-spine` `--extra pg-test` suite (testcontainers), and `services/emro-writeback-java`
  (`mvn`, Oracle/Kafka Dev Services) cannot run here.
- **`uvicorn` is not in any `pyproject` extra** — install it into the agent-spine venv the same way
  the image does (`cd services/agent-spine && uv pip install uvicorn`), then run the BFF in
  in-memory `EXTRACT_DIR` mode (no database):
  `EXTRACT_DIR=../recommendation-engine/examples/extract_sample PLANNER_TENANT=acme uv run --no-sync uvicorn trax_io_spine.bff.asgi:app --port 8000`
  (auth is disabled in this mode: path-param tenant, tenant slug `acme`).
- The BFF ships **no CORS middleware** on purpose (production is same-origin via nginx/Vercel
  rewrite). For a cross-origin Vite dev server you must either serve same-origin or add a CORS shim.
  The web dev server reads `VITE_BFF_URL` (empty = same-origin; a URL = cross-origin), `VITE_DEV_TENANT`
  (dev tenant slug when auth is disabled), and leaves auth off when `VITE_SUPABASE_URL` is unset:
  `cd apps/web && VITE_BFF_URL=http://localhost:8000 VITE_DEV_TENANT=acme npm run dev`.

### apps/web build & lint caveats (pre-existing, on `main`)
- `npm run build` currently **fails** (`tsc -b`) due to a bug in
  `apps/web/src/features/scenarios/ScenarioControls.tsx` (references undefined `tatValue`; should be
  `repairTatValue`), which also breaks `npm run lint` (2 errors) and crashes the **Scenarios** view at
  runtime. Use `npm run dev` for local work and avoid the `/scenarios` route until it's fixed. All
  other views build and run correctly.

### Handy test invocations here
- BFF/core suite (skips the Docker-only pg suite): `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra emro --extra cedar pytest --ignore=tests/pg` (psycopg comes from the `pg`/`pg-test` extra; `agent-spine.bff.app` imports the `pg` module at import time even in EXTRACT_DIR mode).
- `services/event-publisher` needs `--extra http` to even collect (`tests/test_endpoint.py`).
