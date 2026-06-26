# Trax IO Nightly Extract Utility

Python CLI that runs nightly inside a customer eMRO environment, executes the
**21 canonical eMRO extract SQLs** against the customer's Oracle database, and
(in production) lands per-domain JSON files plus a manifest in a tenant-scoped
S3 prefix for the Trax IO Feature Store to ingest.

**Authoritative plan:**
[../../docs/plans/2026-04-14-nightly-extract-utility-plan.md](../../docs/plans/2026-04-14-nightly-extract-utility-plan.md)

**Manifest contract (the handshake with sub-project #2):**
[../../docs/contracts/2026-04-17-extract-manifest-contract.md](../../docs/contracts/2026-04-17-extract-manifest-contract.md)

**Design context:**
[../../docs/design/2026-04-14-trax-io-inventory-optimizer-design.md](../../docs/design/2026-04-14-trax-io-inventory-optimizer-design.md)

---

## Phase 2 scope — real Oracle execution, local-disk landing

This build is **Phase 2: real Oracle execution via `python-oracledb`
(thin mode, no Oracle Instant Client required)**. The CLI enumerates
the selected domains, resolves bind values from CLI flags, opens one
thin-mode Oracle connection, executes each SQL in sequence with named
binds, writes per-domain `<domain>.json` (UTF-8, sorted-keys) with
JSON-serialized rows, computes sha256, and emits a `manifest.json`
conforming to `ExtractManifest` schema v1.0.0.

**Per-domain isolation:** one domain failing (e.g., ORA-00942) is
captured on the artifact and never aborts the remaining domains.

**S3 upload is NOT in scope for Phase 2** — local-disk only. Phase 3
will add the tenant-scoped S3 landing with KMS envelope encryption.

The `--dry-run` flag preserves the Phase 1 behavior (empty `[]`
placeholders, no DB connection) for offline smoke-testing.

---

## The 21 domains

| # | Domain | Windowed | Binds |
|---|---|---|---|
| 1 | `causal_values` | yes | `:start_date`, `:end_date` |
| 2 | `demand_history_rotables` | yes | `:from_date`, `:to_date` |
| 3 | `demand_history_expendables` | yes | `:from_date`, `:to_date` |
| 4 | `events` | yes | `:as_of_date`, `:transaction` |
| 5 | `location_master` | no | — |
| 6 | `location_type` | no | — |
| 7 | `order_plan_closed_orders` | no | — |
| 8 | `order_plan` | no | — |
| 9 | `order_plan_data_requisition` | no | — |
| 10 | `part_chain` | no | — |
| 11 | `part_chain_details` | no | — |
| 12 | `part_criticality` | no | — |
| 13 | `part_kit_bom` | no | — |
| 14 | `part_location` | no | — |
| 15 | `part_master` | no | — |
| 16 | `pn_vendor_price` | no | — |
| 17 | `sales_order` | no | — |
| 18 | `stock_amount` | no | — |
| 19 | `stock_level_upload` | no | — |
| 20 | `trans_code` | no | — |
| 21 | `vendor` | no | — |

Source: customer-canonical SQL file (see
[ExtractManifest contract](../../docs/contracts/2026-04-17-extract-manifest-contract.md)).

## CLI

```text
trax-io-extract extract \
    --tenant-id <tid>           # required
    --extract-date YYYY-MM-DD   # required (UTC)
    --transaction <code>        # required if `events` domain is in the run
    --window-days 90            # default 90; causal_values lookback (days)
    --demand-history-months 36  # default 36; demand_history_* lookback (months)
    --domain <name>             # repeatable; default: all 21
    --output-dir ./out          # local JSON landing; S3 upload is Phase 3
    --dry-run / --no-dry-run    # default --no-dry-run; --dry-run skips DB

trax-io-extract list-domains    # print the 21-domain registry
```

### Oracle connection (Phase 2)

`--no-dry-run` (the default) requires these env vars:

| Env var | Required | Default | Notes |
|---|---|---|---|
| `TRAX_ORACLE_HOST` | yes | — | eMRO Oracle host |
| `TRAX_ORACLE_PORT` | no | `1521` | TNS listener port |
| `TRAX_ORACLE_SERVICE` | yes | — | Oracle service name |
| `TRAX_ORACLE_USER` | yes | — | read-only extract user |
| `TRAX_ORACLE_PASSWORD` | yes | — | never logged |
| `TRAX_ORACLE_WALLET` | no | — | optional wallet path |

Missing env vars cause the CLI to exit with code `2` and a readable
error listing the missing variables.

### Example (real run)

```bash
export TRAX_ORACLE_HOST=emro-prod.example.com
export TRAX_ORACLE_PORT=1521
export TRAX_ORACLE_SERVICE=EMRO
export TRAX_ORACLE_USER=trax_reader
export TRAX_ORACLE_PASSWORD=...

uv run trax-io-extract extract \
    --tenant-id lighthouse-01 \
    --extract-date 2026-04-16 \
    --transaction NR
```

### Example (offline dry-run)

```bash
uv run trax-io-extract extract \
    --tenant-id lighthouse-01 \
    --extract-date 2026-04-16 \
    --transaction NR \
    --dry-run
```

Yields:

```
./out/extract_date=2026-04-16/run_id=01JS7W2F.../
    manifest.json
    causal_values.json
    ...  (21 placeholder files)
```

Summary line on stdout:

```
[trax-io-extract] tenant=lighthouse-01 date=2026-04-16 run=01JS7W2F... \
    domains=21/21 status=succeeded
```

## Development

This project uses [`uv`](https://docs.astral.sh/uv/). From this directory:

```bash
uv sync --all-extras
uv run --extra dev pytest
uv run --extra dev ruff check
```

## Backlog (later phases)

| Phase | Scope |
|---|---|
| 1 | 21 SQL files, ExtractManifest, dry-run CLI (done) |
| 2 | **This build** — Oracle thin-mode extractor, JSON serialization, local-disk landing |
| 3 | S3 landing + per-tenant KMS + retry/parallelism + Parquet |
| 4 | Signed append-only audit log |
| 5 | Presigned-URL Lambda + API Gateway mTLS (TraxAi AWS) |
| 6 | Multipart uploader with retry |
| 7 | `validate` / `schedule-help` CLI commands |
| 8 | Binary signature self-verify on startup |
| 9 | PyInstaller + signed RPM/DEB/MSI via GitHub Actions |
| 10 | End-to-end integration test against testcontainers + moto |
| 11 | Customer-DBA-facing INSTALL.md / CONFIG.md / SECURITY.md |
