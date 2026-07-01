# Real eMRO Full-Network Pipeline — Design Spec

**Date:** 2026-07-01
**Owner:** Miguel Sosa
**Status:** Approved (design); implementation sequenced in 3 waves.

## Goal

Run the real Trax IO pipeline (Oracle extract → demand/requirements projection →
recommendations → guardrails → Planner UI) against the **local eMRO Oracle
database**, over **all planning-active `part × location` keys network-wide
(~62,492)**, using the **statistical forecaster** (`StatisticalProjector`), and
view the resulting real requirements + recommendations in the Planner UI.

Replaces the 3-part committed sample the Docker deploy is currently seeded from.

## Source of truth: the local eMRO DB

- Container `oracle` (`oracle:dumbo2`), `localhost:1521/LOCAL`, user `ODB`.
  Connection config lives at `~/OracleDataUpdate/config.py` (read-only use).
  **Never** stop/restart/remove this container — read-only SELECTs only.
- Genuine eMRO schema: 1,657 tables; every table the 21 extract SQLs reference
  is present.

### Measured scale (read-only introspection, 2026-07-01)

| Metric | Count |
|---|---|
| Distinct `(PN, LOCATION)` keys in `PN_INVENTORY_LEVEL` | 2,287,993 |
| …planning-active (`REORDER_LEVEL>0 OR MAXIMUM_STOCK>0`) | **62,492** |
| Distinct parts / locations | 115,816 / 232 |
| `PN_INVENTORY_HISTORY` (expendable demand) | 23,512,041 |
| `AC_PN_TRANSACTION_HISTORY` (rotable demand) | 3,726,867 |
| `PN_MASTER` / `PN_VENDOR_PRICE` / `PN_INVENTORY_DETAIL` | 159,893 / 585,367 / 942,313 |

Top locations by inventory-level rows: YUL 175k, YYZ 169k, YVR 133k, YYC 119k,
YWG 100k, YOW 100k, YEG 100k, YHZ 100k, MIA-DIS 60k, LHR 33k, GRU 33k, TLV 32k.

**Full-network scoring (2.3M keys) is not runnable** on the in-memory engine +
browser UI (many hours + OOM; multi-GB extract). The working set is bounded to
the **62,492 planning-active keys**; the demand-history pulls are **windowed to
~24 months** and scoped to those keys' parts/locations.

## The four build areas

### 1. Extract — real, scoped, windowed
- **Blocking bug:** all 21 files in `tools/nightly-extract/sql/*.sql` end with a
  trailing `;`, which `oracledb.Cursor.execute()` rejects (`ORA-00933`).
  Fix once at the execute seam (`oracle.py::execute_domain`, strip a single
  trailing `;`), covering all domains. Add a real-Oracle smoke test (currently
  the suite only uses a `FakeCursor`, which is why the bug shipped).
- **Connection:** `TRAX_ORACLE_HOST/PORT/SERVICE/USER/PASSWORD` env
  (`localhost / 1521 / LOCAL / ODB / …`). No secrets committed.
- **Scope filter:** restrict the key universe to planning-active rows and window
  the two demand-history domains to a lookback (~24 months, via the existing
  `binds`). Implementation approach chosen during planning (WHERE-injection vs a
  scope temp table vs post-extract filter) — must preserve referential
  consistency of the slice (a part in `stock_amount` must appear in
  `part_master`, etc.).

### 2. Forecaster wiring
- Add `trax-io-forecasting` as a dependency of the recommendation engine.
- Inject `StatisticalProjector` (Croston/SBA/TSB for intermittent; falls back to
  `HistoricalScheduledProjector` otherwise) at both call sites:
  `recommendation-engine` CLI (`cli.py`) and BFF seed (`bff/store.py`).
  Preserve the default projector as a fallback/flag.

### 3. Scale fixes (mandatory at 62K)
- **Indexed dashboard aggregation:** `bff/store.py::dashboard()` currently scans
  `keys` and does an O(n) `_entries` lookup per key (≈O(keys²)). Re-key `_entries`
  by `(pn, location)` so aggregation is O(keys).
- **Server-side pagination:** the queue endpoint returns *all* rows and
  `QueueTable` renders them all. Add `limit`/`offset` (and server-side
  search/sort/filter, or a documented page-local variant) to the BFF queue
  endpoint; page (or virtualize) the React `QueueTable`.
- **Offline precomputed seed:** the BFF runs the whole reco at import
  (`from_extract`). A 62K-key run (minutes–tens of minutes, with the statistical
  forecaster) cannot happen at container boot. Split into: (a) an **offline
  batch** that runs extract → reco → guardrails and **persists** the queue +
  provenance + history + part-context inputs; (b) a BFF **load path** that reads
  the persisted artifact at boot instead of recomputing.

### 4. Deploy + verify
- Extract → offline reco (real forecaster) → persist → redeploy Docker (project
  `trax-io-planner`, single sequential build, never touch `oracle`/MySQL) →
  confirm the UI renders real parts, paged, with real requirements +
  recommendations, and `/dashboard` returns real portfolio KPIs.

## Wave sequencing (de-risk: see real data before building scale plumbing)

- **Wave 1 — prove the real path (smallest new code):** fix the `;` bug + a
  real-Oracle smoke test; extract **one station** (e.g. YYZ, planning-active)
  through the *existing* engine (default projector); seed the BFF from that real
  extract; **real parts render in the UI.** Validates extract→reco→BFF→UI against
  live Oracle.
- **Wave 2 — forecaster:** wire `StatisticalProjector` (dep + both call sites +
  fallback); re-run the station; confirm requirements now come from the
  statistical model.
- **Wave 3 — scale to 62K:** indexed dashboard aggregation + server-side
  pagination (BFF + UI) + offline precomputed seed pipeline + full
  planning-active network extract; deploy; verify.

Each wave gets its own implementation plan and is executed via
subagent-driven development; Waves 2–3 are re-planned against what Wave 1 learns.

## Out of scope (v1 of this effort)
- Iceberg/Glue/S3 feature store (extract_loader reads local JSON into an
  in-memory store; the Iceberg path stays unwired).
- Secrets-manager integration (plain `TRAX_ORACLE_*` env for local use).
- Nightly re-seed cadence / `run_id` rotation (single resolved extract dir).
- The 9 of 21 extracted domains the loader doesn't consume today
  (`causal_values`, `sales_order`, `trans_code`, `vendor`, `part_chain`,
  `part_kit_bom`, `part_location`, `order_plan_data_requisition`) — remain
  extracted-but-unused unless a wave needs them.

## Risks
- **62K-key reco runtime** with the statistical forecaster is unproven; the
  offline batch may take tens of minutes. Acceptable (one-time seed), but Wave 3
  must measure and log it.
- **Memory:** in-memory feature store over 62K keys + 24-month history for those
  keys may reach multiple GB. Windowing + scoping mitigate; Wave 3 measures.
- **Extract referential consistency** under scoping — the slice must be
  self-consistent across domains.
- **UI at 62K** — pagination is required; search/sort semantics may become
  page-local or must move server-side (decided in the Wave 3 plan).


## RESOLVED (2026-07-01): Location model = network-pooled

Investigation during Wave 1 proved eMRO separates **planning locations**
(`PN_INVENTORY_LEVEL.LOCATION`, e.g. `YYZ` — where ROP/EOQ live) from **physical
storage locations** (`PN_INVENTORY_DETAIL.LOCATION`, e.g. `YYZ-TRM`/`JFK`/`YUL`).
The canonical legacy extract (`StockAmountData.java`) is identical to our SQL —
stock at physical grain, `GROUP BY location_master.LOCATION`, no rollup — so the
reconciliation lived in **Xelus** (the planner Trax IO replaces). We own it now.

**Decision (owner):** **network-pooled.** For a planning key `(PN, planning-loc)`:
- `on_hand` (and its serviceable/unserviceable/in-repair components) = **SUM of that
  PN's physical stock across ALL locations**.
- demand history = **pooled across all physical locations for that PN**.
- policy (ROP/EOQ/SS/Max) stays per `(PN, planning-loc)` from `PN_INVENTORY_LEVEL`.

**Implementation:**
- **Extract:** the poolable domains (`stock_amount`, `demand_history_rotables`,
  `demand_history_expendables`, `pn_vendor_price`, `order_plan*`) scope by **part
  only** (network-wide), not part+location. The planning-key-defining domains
  (`stock_level_upload`, `part_location`) stay part+location (the target station).
- **Reco loader:** add **opt-in** `pool_by_part` — sum stock components by PN and
  concatenate/pool demand by PN, then assign the PN's network total to every
  planning key for that PN. **Default off** so the committed sample + its tests are
  unchanged; on for real eMRO runs (CLI + BFF flag).
- Accepted trade-off (owner-acknowledged): a part planned at multiple locations
  counts its network stock against each planning key (may over-state availability).
