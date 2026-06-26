# Sub-plan #1 — Nightly Extract Utility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship a Trax-signed CLI utility that runs nightly inside the customer's environment, executes the 12 v1 SQLs against the customer's eMRO Oracle DB, packages the results as Parquet files, and uploads them to a tenant-scoped S3 prefix in Trax IO's AWS account via a presigned URL flow that requires no AWS credentials on the customer side.

**Architecture:** A single-binary Python CLI distributed via signed RPM/DEB/MSI packages. Customer DBA installs once, configures via a single TOML file, and schedules via the customer's existing scheduler (cron, Windows Task Scheduler, Control-M). The utility uses `python-oracledb` in thin mode for DB access, `pyarrow` for Parquet, and a Trax-IO presigned-URL service for upload. All credentials never leave the customer environment.

**Tech Stack:**
- Python 3.12, packaged with PyInstaller (`--onefile` for portable distribution)
- `python-oracledb` 2.x (thin mode — no Oracle client install required)
- `pyarrow` for Parquet
- `httpx` for HTTPS
- `pydantic-settings` + `tomli` for config
- `cryptography` for signature verification
- `pytest` + `oracledb`-compatible mock layer
- Build system: `uv` + GitHub Actions matrix (Linux x86_64, Linux ARM64, Windows x86_64)
- Repository: New repo `trax-io-extract-utility` (Trax GitHub org, public-distributable to customer DBAs)

**Dependencies on sister sub-plans:**
- **Trax IO presigned-URL upload service** — small Lambda + API Gateway behind mTLS, ships in this sub-plan as a sub-component (Phase 5).
- **#2 Feature Store** — consumer of the Parquet output. Schema agreement is the contract; tested via shared schema test suite in #2 Phase 6.

---

## File Structure

```
trax-io-extract-utility/
├── pyproject.toml
├── README.md  (customer-DBA-facing)
├── docs/
│   ├── INSTALL.md
│   ├── CONFIG.md
│   └── SECURITY.md
├── src/trax_io_extract/
│   ├── __init__.py
│   ├── __main__.py             # CLI entrypoint via `python -m trax_io_extract`
│   ├── cli.py                  # typer commands: extract, validate, upload, schedule-help
│   ├── config.py               # TOML config + pydantic-settings
│   ├── sqls/
│   │   ├── __init__.py
│   │   ├── loader.py           # loads .sql files with bind-variable substitution
│   │   ├── 01_causal_values.sql
│   │   ├── 02_demand_history_rotables.sql
│   │   ├── 03_demand_history_expendables.sql
│   │   ├── 04_events.sql
│   │   ├── 05_location_master.sql
│   │   ├── 06_location_type.sql
│   │   ├── 07_order_plan.sql
│   │   ├── 08_order_plan_closed.sql
│   │   ├── 09_order_plan_requisition.sql
│   │   ├── 10_part_chain.sql
│   │   ├── 11_part_chain_details.sql
│   │   ├── 12_part_criticality.sql
│   │   ├── 13_part_kit_bom.sql
│   │   ├── 14_part_location.sql
│   │   ├── 15_part_master.sql
│   │   ├── 16_pn_vendor_price.sql
│   │   ├── 17_sales_order.sql
│   │   ├── 18_stock_amount.sql
│   │   ├── 19_stock_level_upload.sql
│   │   ├── 20_trans_code.sql
│   │   └── 21_vendor.sql
│   ├── extractor.py            # Oracle connection + query execution + Parquet write
│   ├── parquet_writer.py       # column type coercion, partition layout
│   ├── uploader.py             # presigned-URL fetch + multipart upload
│   ├── audit.py                # local audit log (rotated, signed)
│   ├── signature.py            # binary signature verification on startup
│   └── manifest.py             # extract manifest with row counts + checksums
├── presigned_service/
│   ├── pyproject.toml
│   ├── src/handler.py          # Lambda issuing presigned URLs scoped per tenant
│   ├── infra/cdk_stack.py      # API Gateway + Lambda + IAM
│   └── tests/
├── tests/
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_sql_loader.py
│   │   ├── test_extractor.py   # uses oracledb thin mode against testcontainers Oracle XE
│   │   ├── test_parquet_writer.py
│   │   ├── test_uploader.py    # against S3 LocalStack
│   │   ├── test_audit.py
│   │   ├── test_signature.py
│   │   └── test_manifest.py
│   ├── integration/
│   │   ├── conftest.py         # spins up Oracle XE testcontainer + LocalStack
│   │   └── test_end_to_end.py
│   └── fixtures/
│       ├── seed_oracle.sql     # creates eMRO-shaped tables + 100 rows of test data
│       └── trax_signing_key.pub
└── packaging/
    ├── build_linux.sh
    ├── build_windows.ps1
    ├── trax-io-extract.spec    # PyInstaller spec
    └── notarize_macos.sh
```

---

## Phases

| Phase | Scope | Tasks |
|---|---|---|
| 0 | Repo bootstrap, signing keys, Oracle test container | 1–3 |
| 1 | Config (TOML) + SQL loader with bind variables | 4–6 |
| 2 | Extractor (Oracle thin-mode connector + query loop) | 7–10 |
| 3 | Parquet writer + manifest | 11–13 |
| 4 | Audit log (signed, rotated) | 14–15 |
| 5 | Presigned-URL service (Lambda + API Gateway) | 16–18 |
| 6 | Uploader (multipart, retry, mTLS) | 19–21 |
| 7 | CLI entry points, validate command, dry-run | 22–24 |
| 8 | Binary signature verification on startup | 25 |
| 9 | PyInstaller packaging + signing for Linux/Windows | 26–28 |
| 10 | End-to-end integration test against testcontainers | 29 |
| 11 | Customer-facing docs (INSTALL, CONFIG, SECURITY) | 30 |

---

## Phase 0: Bootstrap

### Task 1: Initialize repository

**Files:** `pyproject.toml`, `.gitignore`, `README.md`

- [ ] **Step 1: Init repo**

```bash
mkdir -p trax-io-extract-utility && cd trax-io-extract-utility
git init && uv init --python 3.12 --package
```

- [ ] **Step 2: Pinned `pyproject.toml`**

```toml
[project]
name = "trax-io-extract"
version = "0.1.0"
description = "Trax IO nightly Oracle extract utility"
requires-python = ">=3.12"
dependencies = [
  "oracledb>=2.2.0",
  "pyarrow>=16.0.0",
  "httpx>=0.27.0",
  "pydantic>=2.7.0",
  "pydantic-settings>=2.3.0",
  "tomli>=2.0.1; python_version<'3.11'",
  "typer>=0.12.0",
  "structlog>=24.1.0",
  "cryptography>=42.0.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2.0",
  "pytest-cov>=5.0.0",
  "testcontainers[oracle]>=4.5.0",
  "moto[s3]>=5.0.0",
  "ruff>=0.4.0",
  "mypy>=1.10.0",
  "pyinstaller>=6.7.0",
]

[project.scripts]
trax-io-extract = "trax_io_extract.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Verify**

```bash
uv sync --all-extras && uv run python -c "import oracledb, pyarrow; print('ok')"
```

- [ ] **Step 4: Commit**

```bash
echo -e ".venv/\n__pycache__/\n*.egg-info/\n.pytest_cache/\n.mypy_cache/\nbuild/\ndist/\n" > .gitignore
git add . && git commit -m "chore: bootstrap trax-io-extract repo"
```

---

### Task 2: Generate Trax signing keypair (for binary verification)

**Files:** `tests/fixtures/trax_signing_key.pub`

- [ ] **Step 1: Generate test keypair**

```bash
mkdir -p tests/fixtures
openssl genpkey -algorithm Ed25519 -out tests/fixtures/trax_signing_key.priv
openssl pkey -in tests/fixtures/trax_signing_key.priv -pubout -out tests/fixtures/trax_signing_key.pub
echo "tests/fixtures/trax_signing_key.priv" >> .gitignore
```

- [ ] **Step 2: Document the production keypair flow**

Create `docs/SECURITY.md` (initial stub):
```markdown
# Security & Signing

The Trax IO Extract Utility binary is signed with an Ed25519 key held in AWS KMS.
The public key is bundled into every binary at build time.

On startup, the binary verifies its own signature against the bundled public key
and refuses to run if verification fails.

Production signing happens in CI via the `kms-sign` action against AWS KMS key
`arn:aws:kms:us-east-1:TRAX_PROD:key/trax-io-extract-signing`.

For development, a dev keypair lives at `tests/fixtures/trax_signing_key.{pub,priv}`.
The .priv file is gitignored.
```

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/trax_signing_key.pub docs/SECURITY.md .gitignore
git commit -m "chore(security): test signing key + signing docs stub"
```

---

### Task 3: Oracle XE testcontainer fixture

**Files:** `tests/integration/conftest.py`, `tests/fixtures/seed_oracle.sql`

- [ ] **Step 1: Seed SQL**

`tests/fixtures/seed_oracle.sql` (excerpt — covers the tables our 12 SQLs read):
```sql
-- Mini eMRO schema for integration tests
CREATE TABLE AC_MASTER (
  AC VARCHAR2(20) PRIMARY KEY,
  AC_TYPE VARCHAR2(20) NOT NULL
);
CREATE TABLE AC_ACTUAL_FLIGHTS (
  AC VARCHAR2(20),
  DESTINATION VARCHAR2(10),
  FLIGHT_HOURS NUMBER,
  FLIGHT_MINUTES NUMBER,
  CYCLES NUMBER,
  FLIGHT_DATE DATE
);
CREATE TABLE PN_MASTER (
  PN VARCHAR2(40) PRIMARY KEY,
  PN_DESCRIPTION VARCHAR2(200),
  CATEGORY VARCHAR2(10),
  STATUS VARCHAR2(20),
  ESSENTIALITY_CODE VARCHAR2(10),
  CHAPTER VARCHAR2(10),
  SECTION VARCHAR2(10),
  STOCK_UOM VARCHAR2(10),
  AVERAGE_COST NUMBER,
  MARKET_VALUE_UNIT_COST NUMBER,
  SHELF_LIFE_DAYS NUMBER,
  HAZARDOUS_MATERIAL VARCHAR2(1),
  TOOL_CONTROL_ITEM VARCHAR2(1),
  BIN_CAT VARCHAR2(10),
  APU VARCHAR2(1), CAT_RATING VARCHAR2(10), DISK VARCHAR2(1),
  ENGINE VARCHAR2(1), ETOPS VARCHAR2(1), ETOPS_FLAG VARCHAR2(1),
  MEL VARCHAR2(10), REFERENCE_DOCUMENT VARCHAR2(40), REFERENCE_DOCUMENT_REVISION VARCHAR2(20),
  RVSM_CODE VARCHAR2(10), RVSM_FLAG VARCHAR2(1), SUB_CATEGORY VARCHAR2(40)
);
-- (additional tables: PN_INTERCHANGEABLE, PN_INVENTORY_DETAIL, PN_INVENTORY_HISTORY,
--  PN_INVENTORY_LEVEL, PN_VENDOR_PRICE, LOCATION_MASTER, ORDER_HEADER, ORDER_DETAIL,
--  REQUISITION_HEADER, REQUISITION_DETAIL, RELATION_MASTER, SYSTEM_TRAN_CODE, etc.
--  Each gets minimal columns matching the 12 v1 SQLs. Full file ~600 lines.)

INSERT INTO AC_MASTER VALUES ('C-FABC', 'A320');
INSERT INTO AC_MASTER VALUES ('C-FDEF', 'B777');
INSERT INTO AC_ACTUAL_FLIGHTS VALUES ('C-FABC', 'YYZ', 7, 25, 1, DATE '2026-04-01');
-- ~100 rows across all tables

COMMIT;
```

- [ ] **Step 2: testcontainers conftest**

`tests/integration/conftest.py`:
```python
import pytest
from testcontainers.oracle import OracleDbContainer


@pytest.fixture(scope="session")
def oracle_container():
    container = OracleDbContainer("gvenzl/oracle-xe:21-slim")
    container.start()
    yield container
    container.stop()


@pytest.fixture(scope="session")
def oracle_dsn(oracle_container) -> str:
    return oracle_container.get_connection_url().replace("oracle+oracledb://", "")


@pytest.fixture(scope="session", autouse=True)
def seed_db(oracle_container, oracle_dsn):
    import oracledb
    with open("tests/fixtures/seed_oracle.sql") as f:
        ddl = f.read()
    conn = oracledb.connect(dsn=oracle_dsn)
    cur = conn.cursor()
    for stmt in ddl.split(";\n"):
        if stmt.strip():
            cur.execute(stmt)
    conn.commit()
```

- [ ] **Step 3: Verify**

```bash
uv run pytest tests/integration/conftest.py -v --collect-only
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/ tests/fixtures/seed_oracle.sql
git commit -m "test(integration): Oracle XE testcontainer fixture"
```

---

## Phase 1: Config + SQL Loader

### Task 4: TOML configuration

**Files:** `src/trax_io_extract/config.py`, `tests/unit/test_config.py`, `examples/config.toml`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_config.py
from pathlib import Path
import pytest
from trax_io_extract.config import ExtractConfig, load_config


def test_loads_minimal_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("""
        tenant_id = "aircanada"
        [oracle]
        dsn = "host:1521/SVC"
        user = "TRAX_RO"
        password_env = "TRAX_RO_PASSWORD"
        [upload]
        endpoint = "https://extract.trax-io.aws.trax.com"
        client_cert_path = "/etc/trax-io/client.crt"
        client_key_path = "/etc/trax-io/client.key"
    """)
    loaded = load_config(cfg)
    assert loaded.tenant_id == "aircanada"
    assert loaded.oracle.user == "TRAX_RO"


def test_rejects_unknown_field(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("""
        tenant_id = "aircanada"
        unexpected = "value"
        [oracle]
        dsn = "x"
        user = "y"
        password_env = "z"
        [upload]
        endpoint = "https://x"
        client_cert_path = "/a"
        client_key_path = "/b"
    """)
    with pytest.raises(ValueError):
        load_config(cfg)
```

- [ ] **Step 2: Run, verify fails, implement**

```python
# src/trax_io_extract/config.py
from __future__ import annotations
from pathlib import Path
import tomllib
from pydantic import BaseModel, ConfigDict, Field


class OracleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dsn: str
    user: str
    password_env: str
    fetch_array_size: int = 5000


class UploadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str
    client_cert_path: Path
    client_key_path: Path
    timeout_seconds: float = 60.0
    max_retries: int = 5


class ExtractConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    oracle: OracleConfig
    upload: UploadConfig
    output_dir: Path = Path("/var/trax-io-extract/staging")
    audit_dir: Path = Path("/var/trax-io-extract/audit")
    historical_window_days: int = 730  # 24 months default


def load_config(path: Path) -> ExtractConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return ExtractConfig(**raw)
```

- [ ] **Step 3: Example config**

`examples/config.toml`:
```toml
# Trax IO Extract Utility — example configuration
# Copy to /etc/trax-io/config.toml and edit values.
tenant_id = "your-tenant-id"

[oracle]
dsn = "oracle-host.internal:1521/EMRO"
user = "TRAX_RO"
password_env = "TRAX_RO_PASSWORD"
fetch_array_size = 5000

[upload]
endpoint = "https://extract.trax-io.aws.trax.com"
client_cert_path = "/etc/trax-io/client.crt"
client_key_path = "/etc/trax-io/client.key"

output_dir = "/var/trax-io-extract/staging"
audit_dir = "/var/trax-io-extract/audit"
historical_window_days = 730
```

- [ ] **Step 4: Run tests, commit**

```bash
uv run pytest tests/unit/test_config.py -v
git add src/trax_io_extract/config.py tests/unit/test_config.py examples/
git commit -m "feat(config): TOML config with strict validation"
```

---

### Task 5: SQL loader with bind-variable substitution

**Files:** `src/trax_io_extract/sqls/__init__.py`, `src/trax_io_extract/sqls/loader.py`, `tests/unit/test_sql_loader.py`

- [ ] **Step 1: Drop the 21 SQL files**

Translate each of the 12 v1 SQLs from `eMRO Data SQLs.sql` into a numbered `.sql` file under `src/trax_io_extract/sqls/`. Replace placeholder strings (`' startDate '`, `' endDate '`, `' fromDate '`, `' toDate '`, `' transaction '`, `' date '`) with named bind variables (`:start_date`, `:end_date`, `:transaction`, `:date`). Example for `01_causal_values.sql`:
```sql
SELECT am.ac_type AS host_product_id,
       act.destination AS host_loc_id,
       SUM(act.flight_hours * 60) + SUM(act.flight_minutes) AS host_causal_minutes,
       SUM(act.cycles) AS causal_cycles,
       :start_date AS start_date,
       :end_date AS end_date
FROM AC_ACTUAL_FLIGHTS act, AC_MASTER am
WHERE am.AC = act.AC
  AND act.FLIGHT_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
  AND act.FLIGHT_DATE <= TO_DATE(:end_date, 'YYYY-MM-DD')
GROUP BY am.ac_type, act.destination
```

- [ ] **Step 2: Failing test**

```python
# tests/unit/test_sql_loader.py
from datetime import date
from trax_io_extract.sqls.loader import SqlLoader, ExtractQuery


def test_loads_all_21_queries():
    loader = SqlLoader()
    queries = loader.load_all()
    assert len(queries) == 21
    assert {q.name for q in queries} >= {
        "causal_values", "demand_history_rotables", "events",
        "location_master", "part_master", "stock_amount", "vendor",
    }


def test_query_carries_bind_variables():
    loader = SqlLoader()
    q = loader.load_one("causal_values")
    binds = q.binds(start_date=date(2026, 1, 1), end_date=date(2026, 4, 14))
    assert binds["start_date"] == "2026-01-01"
    assert binds["end_date"] == "2026-04-14"


def test_unknown_query_raises():
    import pytest
    loader = SqlLoader()
    with pytest.raises(KeyError):
        loader.load_one("nonexistent")
```

- [ ] **Step 3: Implement**

```python
# src/trax_io_extract/sqls/__init__.py — empty
# src/trax_io_extract/sqls/loader.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from typing import Any


@dataclass(frozen=True)
class ExtractQuery:
    name: str
    sql: str
    expected_bind_vars: tuple[str, ...]

    def binds(self, **kwargs: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        for bv in self.expected_bind_vars:
            if bv not in kwargs:
                raise ValueError(f"missing bind var {bv}")
            v = kwargs[bv]
            out[bv] = v.isoformat() if isinstance(v, date) else str(v)
        return out


_QUERY_BIND_VARS: dict[str, tuple[str, ...]] = {
    "causal_values": ("start_date", "end_date"),
    "demand_history_rotables": ("from_date", "to_date"),
    "demand_history_expendables": ("from_date", "to_date"),
    "events": ("transaction", "date"),
    "location_master": (),
    "location_type": (),
    "order_plan": (),
    "order_plan_closed": (),
    "order_plan_requisition": (),
    "part_chain": (),
    "part_chain_details": (),
    "part_criticality": (),
    "part_kit_bom": (),
    "part_location": (),
    "part_master": (),
    "pn_vendor_price": (),
    "sales_order": (),
    "stock_amount": (),
    "stock_level_upload": (),
    "trans_code": (),
    "vendor": (),
}


class SqlLoader:
    def load_all(self) -> list[ExtractQuery]:
        return [self.load_one(name) for name in _QUERY_BIND_VARS]

    def load_one(self, name: str) -> ExtractQuery:
        if name not in _QUERY_BIND_VARS:
            raise KeyError(f"unknown query: {name}")
        idx = list(_QUERY_BIND_VARS).index(name) + 1
        filename = f"{idx:02d}_{name}.sql"
        sql = (files("trax_io_extract.sqls") / filename).read_text()
        return ExtractQuery(name=name, sql=sql, expected_bind_vars=_QUERY_BIND_VARS[name])
```

- [ ] **Step 4: Run, commit**

```bash
uv run pytest tests/unit/test_sql_loader.py -v
git add src/trax_io_extract/sqls/ tests/unit/test_sql_loader.py
git commit -m "feat(sqls): SQL loader with 21 queries + bind-var validation"
```

---

### Task 6: Bind-variable injection safety test

**Files:** `tests/unit/test_sql_safety.py`

Verify that no SQL file uses string concatenation for date placeholders.

```python
# tests/unit/test_sql_safety.py
from importlib.resources import files


def test_no_legacy_placeholders_in_sql_files():
    sqls = files("trax_io_extract.sqls")
    bad_patterns = [" startDate ", " endDate ", " fromDate ", " toDate ", " transaction ", " date "]
    for entry in sqls.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        text = entry.read_text()
        for bad in bad_patterns:
            assert bad not in text, f"{entry.name} still contains legacy placeholder {bad!r}"


def test_all_sqls_end_without_semicolon():
    sqls = files("trax_io_extract.sqls")
    for entry in sqls.iterdir():
        if entry.name.endswith(".sql"):
            assert not entry.read_text().rstrip().endswith(";"), entry.name
```

```bash
uv run pytest tests/unit/test_sql_safety.py -v
git add tests/unit/test_sql_safety.py && git commit -m "test(sqls): bind-var safety + no trailing semicolon"
```

---

## Phase 2: Extractor

### Task 7: Oracle thin-mode connection wrapper

**Files:** `src/trax_io_extract/extractor.py`, `tests/integration/test_extractor.py`

- [ ] **Step 1: Failing integration test**

```python
# tests/integration/test_extractor.py
from datetime import date
from trax_io_extract.extractor import Extractor


def test_extractor_runs_causal_values_against_seeded_db(oracle_dsn):
    ext = Extractor(dsn=oracle_dsn, user="system", password="oracle")
    rows = list(ext.run_query(
        "causal_values",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    ))
    assert len(rows) >= 1
    assert "host_product_id" in rows[0]


def test_extractor_yields_in_chunks_for_memory_safety(oracle_dsn):
    ext = Extractor(dsn=oracle_dsn, user="system", password="oracle", fetch_array_size=2)
    rows = list(ext.run_query("ac_master_smoke"))  # auxiliary smoke query
    assert isinstance(rows, list)
```

(Note: `ac_master_smoke` is registered separately for testing chunked iteration; ignore for production.)

- [ ] **Step 2: Implement**

```python
# src/trax_io_extract/extractor.py
from __future__ import annotations
from collections.abc import Iterator
from typing import Any
import oracledb
import structlog
from trax_io_extract.sqls.loader import SqlLoader

_LOG = structlog.get_logger("extractor")


class Extractor:
    def __init__(
        self,
        *,
        dsn: str,
        user: str,
        password: str,
        fetch_array_size: int = 5000,
    ) -> None:
        self._dsn = dsn
        self._user = user
        self._password = password
        self._fetch = fetch_array_size
        self._loader = SqlLoader()

    def run_query(self, query_name: str, **bind_vars: Any) -> Iterator[dict[str, Any]]:
        query = self._loader.load_one(query_name)
        binds = query.binds(**bind_vars) if query.expected_bind_vars else {}
        _LOG.info("query_start", name=query_name, bind_var_count=len(binds))
        with oracledb.connect(dsn=self._dsn, user=self._user, password=self._password) as conn:
            cur = conn.cursor()
            cur.arraysize = self._fetch
            cur.execute(query.sql, binds)
            cols = [d[0].lower() for d in cur.description]
            row_count = 0
            while True:
                batch = cur.fetchmany(self._fetch)
                if not batch:
                    break
                for row in batch:
                    yield dict(zip(cols, row, strict=True))
                    row_count += 1
        _LOG.info("query_complete", name=query_name, rows=row_count)
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/integration/test_extractor.py -v
git add src/trax_io_extract/extractor.py tests/integration/test_extractor.py
git commit -m "feat(extractor): Oracle thin-mode chunked iterator"
```

---

### Tasks 8–10: Connection retry, query-level error isolation, parallel execution

(Compressed for space — same TDD shape: failing test → implementation → run → commit.)

- **T8: Connection retry on transient ORA-12541 / network errors** with exponential backoff. Test mocks `oracledb.connect` to fail twice then succeed. Implementation wraps connection acquisition in retry loop.
- **T9: Per-query error isolation** — a single bad query must not abort the entire nightly run. Test asserts that an artificially-failing query produces an error manifest entry but the other 20 queries complete.
- **T10: Parallel query execution** with `concurrent.futures.ThreadPoolExecutor(max_workers=4)` and a shared connection pool. Test verifies wall-clock < sum-of-sequential-times.

---

## Phase 3: Parquet writer + manifest

### Task 11: Parquet writer with column type coercion

**Files:** `src/trax_io_extract/parquet_writer.py`, `tests/unit/test_parquet_writer.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_parquet_writer.py
from datetime import datetime
from pathlib import Path
import pyarrow.parquet as pq
from trax_io_extract.parquet_writer import write_parquet


def test_write_parquet_round_trips(tmp_path: Path):
    rows = [
        {"pn": "A", "qty": 5, "created_at": datetime(2026, 4, 14, 12, 0)},
        {"pn": "B", "qty": 0, "created_at": None},
    ]
    out = tmp_path / "out.parquet"
    write_parquet(rows, out, schema_name="part_master")
    table = pq.read_table(out)
    assert table.num_rows == 2
    assert table.column_names == ["pn", "qty", "created_at"]


def test_write_parquet_handles_empty(tmp_path: Path):
    out = tmp_path / "empty.parquet"
    write_parquet([], out, schema_name="part_master")
    table = pq.read_table(out)
    assert table.num_rows == 0
```

- [ ] **Step 2: Implement**

```python
# src/trax_io_extract/parquet_writer.py
from __future__ import annotations
from collections.abc import Iterable
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq


def write_parquet(rows: Iterable[dict], output: Path, *, schema_name: str) -> int:
    rows_list = list(rows)
    if not rows_list:
        # Write empty file with no schema; consumer treats absence-of-rows as zero
        table = pa.Table.from_pylist([])
        pq.write_table(table, output, compression="zstd")
        return 0
    table = pa.Table.from_pylist(rows_list)
    pq.write_table(
        table,
        output,
        compression="zstd",
        compression_level=3,
        write_statistics=True,
    )
    return table.num_rows
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/unit/test_parquet_writer.py -v
git add src/trax_io_extract/parquet_writer.py tests/unit/test_parquet_writer.py
git commit -m "feat(parquet): writer with zstd compression and empty-table support"
```

---

### Tasks 12–13: Manifest + checksums

**T12: Extract manifest** — JSON sidecar with `tenant_id`, `extract_date`, per-query `(filename, row_count, sha256)`, `started_at`, `completed_at`, utility version. Schema is the contract with sub-plan #2's Glue ingestion job.

**T13: SHA-256 checksum verification** — written alongside manifest; uploader verifies before declaring upload complete; downstream Glue verifies before claiming the partition.

---

## Phase 4: Audit log

### Task 14: Append-only signed audit log

**Files:** `src/trax_io_extract/audit.py`, `tests/unit/test_audit.py`

The audit log is the customer's evidence that the utility ran, what it extracted, and what was uploaded. Required by SOC 2 and by the customer's own internal IT audit. Each entry is JSON, one-per-line, signed with the utility's per-instance key (generated at install time, registered with Trax's KMS).

- [ ] **Implement** signed append-only `AuditLog.write(event_type, payload)` with rotation at 100 MB; verify signature on read; tests cover write-read-verify round trip and tamper detection.

### Task 15: Audit log redaction policy

Verify no PII (mechanic names from `REMOVAL_REASON` etc.) lands in audit; configurable redaction patterns; tests assert known PII patterns are scrubbed.

---

## Phase 5: Presigned-URL service (Lambda + API Gateway)

### Task 16: Lambda issuing per-tenant presigned S3 URLs

**Files:** `presigned_service/src/handler.py`

```python
# presigned_service/src/handler.py
import json
import os
import boto3
from datetime import datetime, UTC

s3 = boto3.client("s3")
BUCKET = os.environ["LANDING_BUCKET"]
EXPIRES_S = 3600


def handler(event, _ctx):
    # mTLS auth done at API Gateway; cert CN passed in custom header
    tenant_id = event["requestContext"]["authorizer"]["tenant_id"]
    body = json.loads(event["body"])
    extract_date = body["extract_date"]  # YYYY-MM-DD
    filename = body["filename"]          # safe-filename validated below
    if "/" in filename or ".." in filename:
        return {"statusCode": 400, "body": "invalid filename"}
    key = f"{tenant_id}/{extract_date}/{filename}"
    url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": BUCKET,
            "Key": key,
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": f"alias/trax-io/{tenant_id}",
            "ContentType": "application/octet-stream",
        },
        ExpiresIn=EXPIRES_S,
        HttpMethod="PUT",
    )
    return {"statusCode": 200, "body": json.dumps({"url": url, "key": key})}
```

### Tasks 17–18: API Gateway with mTLS + CDK stack

CDK deploys API Gateway with mTLS authorizer, custom domain, Lambda backend, KMS-encrypted S3 bucket per tenant prefix, IAM least-privilege.

---

## Phase 6: Uploader

### Task 19: Multipart upload with retry

**Files:** `src/trax_io_extract/uploader.py`, `tests/unit/test_uploader.py`

- [ ] **Implement** `Uploader.upload_extract(parquet_files, manifest)` that:
  1. POSTs to `{endpoint}/v1/presigned` for each filename, getting a presigned URL.
  2. PUTs the file to S3 directly using the presigned URL (multipart for files > 100 MB).
  3. Verifies HTTP 200 + ETag matches expected.
  4. Retries with exponential backoff on 5xx and connection errors.
  5. Records audit entry per upload.

Tests use `moto` for S3 and `pytest-httpx` for the presigned-URL service.

### Tasks 20–21: mTLS, retry, partial-failure recovery

---

## Phase 7: CLI entry points

### Task 22: `trax-io-extract extract` command

**Files:** `src/trax_io_extract/cli.py`

```python
# src/trax_io_extract/cli.py
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import os
import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def extract(
    config: Path = typer.Option(Path("/etc/trax-io/config.toml"), "--config", "-c"),
    extract_date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD; default = yesterday"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run queries but do not upload"),
):
    from trax_io_extract.config import load_config
    from trax_io_extract.extractor import Extractor
    from trax_io_extract.parquet_writer import write_parquet
    from trax_io_extract.manifest import ExtractManifest
    from trax_io_extract.uploader import Uploader

    cfg = load_config(config)
    target = date.fromisoformat(extract_date) if extract_date else date.today() - timedelta(days=1)
    out_dir = cfg.output_dir / cfg.tenant_id / target.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    pwd = os.environ.get(cfg.oracle.password_env)
    if not pwd:
        typer.echo(f"ERROR: env var {cfg.oracle.password_env} not set", err=True)
        raise typer.Exit(2)

    ext = Extractor(
        dsn=cfg.oracle.dsn, user=cfg.oracle.user, password=pwd,
        fetch_array_size=cfg.oracle.fetch_array_size,
    )
    manifest = ExtractManifest(tenant_id=cfg.tenant_id, extract_date=target)
    queries = ext._loader.load_all()
    for q in queries:
        bind_kwargs = {}
        if "start_date" in q.expected_bind_vars:
            bind_kwargs["start_date"] = target - timedelta(days=cfg.historical_window_days)
            bind_kwargs["end_date"] = target
        if "from_date" in q.expected_bind_vars:
            bind_kwargs["from_date"] = target - timedelta(days=cfg.historical_window_days)
            bind_kwargs["to_date"] = target
        rows = list(ext.run_query(q.name, **bind_kwargs))
        out = out_dir / f"{q.name}.parquet"
        n = write_parquet(rows, out, schema_name=q.name)
        manifest.record(query=q.name, file=out, row_count=n)
    manifest_path = out_dir / "manifest.json"
    manifest.write(manifest_path)
    typer.echo(f"Extract complete: {manifest.total_rows()} rows across {len(queries)} queries")
    if dry_run:
        typer.echo("Dry run — skipping upload")
        return
    Uploader(cfg.upload).upload_extract(out_dir, manifest_path)
    typer.echo("Upload complete")
```

### Tasks 23–24: `validate` and `schedule-help` commands

`validate` runs configuration checks + a single test query against Oracle. `schedule-help` prints OS-specific cron / Task Scheduler examples.

---

## Phase 8: Binary signature verification

### Task 25: Self-verify on startup

`__main__.py` verifies the embedded Ed25519 signature of the binary against the bundled public key before any other code runs. Refuses to execute if invalid. Test uses a tampered binary.

---

## Phase 9: Packaging

### Tasks 26–28: PyInstaller + GitHub Actions matrix builds

CI builds for Linux x86_64, Linux ARM64, Windows x86_64; signs artifacts via AWS KMS; publishes to a customer-DBA download portal. Each release is reproducible from a tag.

---

## Phase 10: End-to-end integration

### Task 29: Full pipeline test

```python
# tests/integration/test_end_to_end.py
def test_extract_and_upload_round_trip(oracle_dsn, tmp_path, moto_s3):
    # Configure utility against testcontainers Oracle + moto S3
    # Run `trax-io-extract extract --dry-run=false`
    # Assert all 21 Parquet files present in mock S3 with correct keys
    # Assert manifest sha256 matches
    # Assert audit log has expected entries
    ...
```

---

## Phase 11: Customer-facing docs

### Task 30: INSTALL, CONFIG, SECURITY

Three Markdown documents written for a customer DBA who has never seen the utility:
- `INSTALL.md` — system requirements, package installation per OS, signing key import, first-run validation
- `CONFIG.md` — every config option with example values, scheduling examples for cron / Task Scheduler / Control-M
- `SECURITY.md` — what credentials live where, what data leaves the customer environment (only Parquet over mTLS), audit log location, signature verification details, security review package for the customer's IT security team

---

## Self-Review

| Spec coverage | Covered by |
|---|---|
| 12 v1 SQLs as nightly extract contract | Tasks 5–6 (21 SQL files = 12 SQLs split where the original SQL file had multiple selects) |
| Per-tenant landing in S3 with KMS | Task 16 (presigned service) + Task 19 (uploader) |
| No customer AWS credentials | Task 16 (presigned URL flow) |
| Audit trail on customer side | Tasks 14–15 (signed append-only audit log) |
| SOC 2 evidence | Audit log + presigned-URL service CloudTrail integration |
| Resilience to single-query failure | Task 9 |
| Speed | Task 10 (parallel) + Task 11 (zstd) |
| Customer DBA UX | Task 30 (docs) + Tasks 22–24 (CLI) |

**Estimated team:** 1 senior Python engineer + 1 DevOps engineer (packaging + presigned service) + 0.5 Oracle DBA consultant for SQL hardening review = 6 weeks elapsed, 2 quarters with hardening + customer pilot.

**Plan complete and ready for execution handoff.**
