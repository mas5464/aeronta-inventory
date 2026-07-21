# C3 — Upload Intake: CSV/Excel → Canonical Model v1 → Ingest Job → Recommendations

**Date:** 2026-07-21
**Status:** Approved (design walkthrough complete)
**Owner:** Miguel Sosa
**Parent spec:** [2026-07-20-commercialization-architecture-design.md](2026-07-20-commercialization-architecture-design.md) (§5 data intake, §10 row C3)
**Builds on the live stack:** Aeronta Inventory in production — apps/web on Vercel (https://aeronta-inventory.vercel.app), Railway `bff` + `worker`, Supabase `aeronta-inventory` (migrations 0001–0007). The C2 `jobs` table + idle worker (`trax_io_spine.pg.worker`, empty `HANDLERS` registry) exist for exactly this.

## 1. Decisions locked (user-approved)

| Decision | Choice |
|---|---|
| Upload path | **Direct-to-Storage + async job** — browser uploads to Supabase Storage via signed URLs (files never transit the BFF); a `jobs` row triggers the Railway worker |
| File formats | **CSV + Excel (.xlsx)** — one CSV per canonical file, or one workbook with one sheet per file |
| Data model | **Full replace** — each successful ingest is a complete snapshot replacing the tenant's dataset (the seeder's existing replace-per-tenant semantics) |
| Validation UX | **Job validates, then reports** — the ingest job validates ALL files first; the engine runs + data is replaced only on zero errors; on failure, structured per-row errors are recorded and existing data is untouched |
| Hardening | **Folded in as C3 task 0** — principal attribution (verified caller identity into the audit trail) + DB-layer owner-membership rules |
| Engine integration | **Approach A** — canonical CSVs map to the engine's existing extract JSON shape; the ingest handler writes a temp extract dir and calls `build_stores_from_extract` unchanged. Rejected: in-memory loader refactor (two drifting code paths), new canonical pydantic package (design-in-a-vacuum, umbrella-rejected) |

## 2. Task-0 hardening (independent of upload; clears the ledger)

- **Principal attribution.** `PgPlannerStore` currently impersonates with a random `sub` and `_decision`/writeback hardcode `principal="planner"`. C3 threads the verified `claims["sub"]` + `tenant_role` (from the C2 middleware, `request.state.claims`) into `PgPlannerStore` per request, so every `decisions` row and `writeback_ledger` entry records the actual actor — the SOC 2 attributable-audit rule. Change surface: a `principal`/`role` param on the store's decision path + the BFF app wiring that constructs the per-request store call. The in-memory `PlannerStore` keeps its default so local/test behavior is unchanged.
- **DB-layer owner rules.** A migration adds owner-specific `memberships` UPDATE/DELETE policies: only an `owner` claim may grant/revoke `owner` or modify an owner row (the app-layer `_require_owner`/last-owner guard stays as defense-in-depth on top). Closes the C2 final-review "app-layer-only owner rules" carry-forward.

## 3. Upload flow & storage

```
browser ─(1) POST /v1/tenants/{t}/uploads──► BFF mints signed Storage upload URLs (service key)
        ─(2) PUT files directly────────────► Supabase Storage  tenant-uploads/{tenant_id}/{batch_id}/{file}
        ─(3) POST /v1/tenants/{t}/ingest───► BFF inserts a jobs row (kind='ingest', payload below)
        ─(4) GET  …/ingest/{job_id} (poll)─► status + result summary OR structured errors
                                              ▲
        Railway worker ─ claims the ingest job ─ downloads from Storage ─ validates ─
          (if clean) map → temp extract dir → from_extract → seed_store ─ writes result
```

- **Storage bucket `tenant-uploads`:** Supabase Storage RLS so a tenant's JWT can only write objects under its own `{tenant_id}/` prefix; the worker reads via the service key. Files never transit the BFF.
- **Ingest job payload:** `{tenant_id, tenant_slug, batch_id, files: {canonical_name: storage_path}, uploaded_by: <sub>}`. (Carries `tenant_id` explicitly — the C2 worker discards the claimed row's tenant; enqueuers must embed it.)
- **BFF routes** (write paths role-floored to `planner`+ by the C2 middleware; status readable by any member):
  - `POST /v1/tenants/{t}/uploads` → mint signed PUT URLs for the named canonical files; returns `{batch_id, targets: {name: {url, path}}}`.
  - `POST /v1/tenants/{t}/ingest` → body `{batch_id, files}`; inserts the `jobs` row; returns `{job_id}`.
  - `GET /v1/tenants/{t}/ingest/{job_id}` → `{status, result?, errors?}`.
  - `GET /v1/tenants/{t}/ingest` → recent ingest jobs (history).
- **Migration:** `jobs` gains a `result jsonb` column (success summary) distinct from `error text` (failure/validation payload).

## 4. Canonical model v1 — the six files

Planner-friendly headers mapped by the ingest mapper to the engine's eMRO-native domain columns (`hostpartid`, `hostlocid`, …). **Required files:** `parts.csv` + `stock.csv` (the engine's `_REQUIRED_DOMAINS`: `part_master`, `stock_amount`, `stock_level_upload`). **Strongly recommended:** `demand_history.csv`. **Optional:** `locations.csv`, `open_orders.csv`, `vendors.csv`.

| File | Required columns | Optional columns | Engine domain(s) |
|---|---|---|---|
| `parts.csv` | `part_number` | `description`, `criticality`, `part_class`, `unit_cost`, `repairable`, `shelf_life_days`, `hazmat`, `ata_chapter`, `is_kit` | `part_master` |
| `stock.csv` | `part_number`, `location_code`, `on_hand` | `allocated`, `in_repair`, `current_rop`, `current_eoq`, `current_safety_stock`, `current_max` | `stock_amount` + `stock_level_upload` |
| `demand_history.csv` | `part_number`, `location_code`, `period`, `quantity` | `transaction_type` | `demand_history_rotables/expendables` |
| `locations.csv` | `location_code` | `parent_location_code` | `location_master` |
| `open_orders.csv` | `part_number`, `location_code`, `quantity`, `expected_date` | `order_type` | `order_plan` |
| `vendors.csv` | `part_number`, `vendor_code`, `unit_price`, `lead_time_days` | `min_order_qty`, `condition`, `preferred` | `pn_vendor_price` + `vendor` |

- **`.xlsx`:** one workbook, one sheet per file named exactly (`parts`, `stock`, `demand_history`, `locations`, `open_orders`, `vendors`); openpyxl reads sheets, then the identical validation/mapping path runs. openpyxl is a new dependency scoped to the ingest/reco package.
- **Column↔domain mapping** is a documented lookup table in the mapper; the canonical header names ARE the public connector spec (a simpler sibling of the eMRO 22-domain mapper #1). `demand_history` rows split to the two engine demand domains by the part's `part_class` (rotable → `demand_history_rotables`, everything else → `demand_history_expendables`); when `part_class` is absent, default to expendables (the loader reads both identically for demand observations, so this only affects the rotable-pooling path).

## 5. Validation module

Produces a structured `list[IngestError]` (`{file, row, column, message}`); the engine runs ONLY when the list is empty.

1. **Header/file check** — required columns present per uploaded file; a missing required file (`parts`/`stock`) → error.
2. **Per-row typing** — numeric fields parse; `period`/`expected_date` parse (ISO or MM/DD/YYYY via the loader's `_parse_date`); `part_number`/`location_code` non-empty.
3. **Referential** — every `(part_number, location_code)` in `stock`/`demand_history`/`open_orders` references a `parts.csv` row (and a `locations.csv` row when that file is provided); `criticality` codes map via the existing `_DEFAULT_ESSENTIALITY_MAP` — unknown codes are flagged, never silently defaulted.
4. **Quota** — distinct `(part_number, location_code)` keys counted against `tenants.key_quota`; over → a single actionable error naming the count, the plan limit, and the remedy.

On any error: `jobs.status = 'failed'`, `jobs.error` = the error payload (bounded/sampled if huge), tenant data untouched.

## 6. Ingest job & worker handler

- **Registration:** `HANDLERS["ingest"] = ingest_handler` in `trax_io_spine.pg.worker` (C2 left the registry empty).
- **Worker durability fix (ledgered prerequisite):** split the C2 single-transaction claim+run — **commit the claim (`status='running'`, `attempts+1`) first**, execute the handler on a fresh connection, then commit the terminal state. A crash mid-run leaves the job `running` past a stale-claim timeout (reclaimable a bounded number of times) instead of rolling back the attempts increment and retrying a poison job forever. Add a stale-`running` reclaim (claim query also picks `running` rows older than a timeout, capped by `attempts < MAX_ATTEMPTS`).
- **Handler flow:** download the batch from Storage → parse (csv/xlsx) → validate (§5). If errors → record + `failed` + return. If clean → map to a temp extract dir → `PlannerStore.from_extract(tenant_id=slug, extract_dir=tmp)` → `seed_store(pool, store=…, slug=slug, name=…)` (its existing replace-per-tenant transaction) → `jobs.result = {files, keys, recommendations, seeded_at}`, `status='done'`. Temp dir removed in `finally`.
- **Atomicity:** validation gates the run; `seed_store` replaces in one transaction — the tenant keeps the old dataset or gets the fully-new one, never a mix.
- **BVR cache:** the seed already replaces `bvr_cache`; no extra invalidation needed.

## 7. Frontend — upload surface

Extends the existing **Data & Connections** view (`apps/web`, the honest 13-feed table) with an **Upload** panel — not a new nav item.

- Canonical-file dropzones (parts, stock, demand_history, locations, open_orders, vendors) accepting `.csv`/`.xlsx`, each with a "download template" link and required/optional hints.
- Upload → progress → "Run ingest" → poll job status. Terminal states render either a **success summary** (files, keys under management, recommendations produced, link to the Workbench) or a **grouped error table** (by file: row/column/message, scrollable + searchable via the existing table primitives).
- An **ingest history** list (recent jobs: when, who, status, key count) from `GET …/ingest`.
- Role-gated: `planner`+ sees upload/run controls (matches the write-role floor); `viewer` sees status/history read-only.
- Client methods: `mintUploadUrls`, `putToStorage` (direct signed-URL PUT), `createIngest`, `getIngest`, `listIngests`; all authed via the existing `request<T>`/`downloadWithAuth` patterns.

## 8. Error handling

- Signed-URL mint failure → 502; malformed ingest request (unknown file names, missing batch) → 422; over-quota + validation failures are **job outcomes** surfaced in the poll, not HTTP errors.
- Worker: Storage-download failure → job `failed` with a clear message (reclaimable); unknown file/sheet names → validation error naming the expected set; parser exceptions caught → `failed`, never crash the loop.
- Existing data is never partially mutated — the replace is all-or-nothing behind validation.

## 9. Testing

- **Validation module** unit tests: each rule, clean + dirty fixtures, including an `.xlsx` fixture and an over-quota case.
- **Parser/mapper** tests: csv + xlsx → the exact engine domain shape (column-name mapping pinned against real domain keys).
- **Ingest handler** end-to-end on the pg testcontainer: sample CSVs → handler → real Postgres seed → queue non-empty + `result` summary correct; a dirty batch → `failed` + errors + prior data untouched.
- **Worker durability** test: claim committed before the handler runs (a handler that raises leaves a reclaimable `running`/`failed`, not an infinite-retry poison job).
- **BFF routes** on the harness with a faked Storage client (protocol seam, like `fake_emro`): mint, create, poll, history + role gating.
- **Frontend** Vitest: dropzone, upload/poll state machine, success summary, error table, role gating, history.
- **Live smoke:** `deploy/aeronta_smoke.py` gains an optional ingest stage — upload sample CSVs to the real bucket, enqueue, poll to `done`, assert the queue populated (env-gated, skips clean).

## 10. Task decomposition (~10 tasks)

| # | Task | Delivers |
|---|---|---|
| 0a | Principal attribution | verified `sub`/role threaded into `PgPlannerStore` decisions + writeback |
| 0b | DB owner-rule migration | owner-specific memberships UPDATE/DELETE policies + isolation tests |
| 1 | Storage + jobs.result migration | `tenant-uploads` bucket + RLS, `jobs.result` column |
| 2 | Canonical schema + validation module | the 6-file contract + `list[IngestError]` validator |
| 3 | csv/xlsx parsers + mapper | parse both formats → engine domain dicts/temp dir |
| 4 | Ingest handler + worker durability | `HANDLERS["ingest"]`, claim-commit-before-run, stale reclaim |
| 5 | BFF routes | mint-URLs / create-ingest / poll / history, role-floored |
| 6 | Frontend upload panel + history | Data & Connections extension |
| 7 | Live smoke ingest stage | `aeronta_smoke.py` optional ingest gate |
| 8 | Bookkeeping | ROADMAP C3 row, TASKS, CLAUDE.md, spec status |

## 11. Out of scope (deferred)

Incremental/delta uploads; live connector auto-sync (AMOS/eMRO); scheduled re-ingest; fuzzy/auto column mapping; the 59K scale-gate run (still pending a real full-network snapshot); OAuth/SAML sign-in; Stripe billing (C4).

## 12. Risks

| Risk | Mitigation |
|---|---|
| Large uploads exceed worker memory (full-replace holds the whole dataset) | Streaming CSV parse; the engine already handles 58.9K keys in-process; document a soft row cap tied to plan tier |
| Storage RLS misconfigured → cross-tenant file read/write | Per-prefix Storage policy + a two-tenant Storage isolation test in the smoke/route layer |
| Planner uploads eMRO-native headers by habit | Templates + a validation error that names the expected canonical headers; the eMRO 22-domain mapper stays a separate path |
| Worker durability change regresses C2's retry semantics | The C2 worker tests stay green; the new durability test pins claim-before-run without breaking retry×3 |
