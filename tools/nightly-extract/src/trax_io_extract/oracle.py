"""Oracle connection + execution helpers for the nightly extract.

Thin-mode ``python-oracledb`` is used so the utility has no external
Oracle Instant Client dependency. Secrets are never logged: any logging
goes through :func:`_safe_repr` which redacts ``password`` and
``wallet_location`` to ``"***"``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterator, Mapping

import oracledb

if TYPE_CHECKING:
    from typing import Self


class MissingOracleConfigError(RuntimeError):
    """Raised when required Oracle env vars are missing."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        super().__init__(
            "missing required Oracle environment variable(s): " + ", ".join(missing)
        )


class OracleExecutionError(Exception):
    """Wraps an :class:`oracledb.DatabaseError` with a parsed ORA code."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


@dataclass(frozen=True)
class OracleConnectionConfig:
    """Configuration for a thin-mode Oracle connection.

    ``password`` is intentionally excluded from any log-friendly
    representation. Callers must not print this dataclass directly.
    """

    host: str
    service_name: str
    user: str
    password: str
    port: int = 1521
    wallet_location: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Build a config from environment variables.

        Required: ``TRAX_ORACLE_HOST``, ``TRAX_ORACLE_SERVICE``,
        ``TRAX_ORACLE_USER``, ``TRAX_ORACLE_PASSWORD``. Optional:
        ``TRAX_ORACLE_PORT`` (defaults to 1521),
        ``TRAX_ORACLE_WALLET``.
        """
        e = env if env is not None else os.environ
        required = {
            "TRAX_ORACLE_HOST": "host",
            "TRAX_ORACLE_SERVICE": "service_name",
            "TRAX_ORACLE_USER": "user",
            "TRAX_ORACLE_PASSWORD": "password",
        }
        missing = [k for k in required if not e.get(k)]
        if missing:
            raise MissingOracleConfigError(missing)

        port_raw = e.get("TRAX_ORACLE_PORT")
        try:
            port = int(port_raw) if port_raw else 1521
        except ValueError as exc:
            raise MissingOracleConfigError(["TRAX_ORACLE_PORT (not an int)"]) from exc

        return cls(
            host=e["TRAX_ORACLE_HOST"],
            port=port,
            service_name=e["TRAX_ORACLE_SERVICE"],
            user=e["TRAX_ORACLE_USER"],
            password=e["TRAX_ORACLE_PASSWORD"],
            wallet_location=e.get("TRAX_ORACLE_WALLET") or None,
        )


def _safe_repr(cfg: OracleConnectionConfig) -> str:
    """Log-safe representation with secrets redacted."""
    redacted = replace(
        cfg,
        password="***",
        wallet_location="***" if cfg.wallet_location else None,
    )
    return (
        f"OracleConnectionConfig(host={redacted.host!r}, port={redacted.port}, "
        f"service_name={redacted.service_name!r}, user={redacted.user!r}, "
        f"password={redacted.password!r}, wallet_location={redacted.wallet_location!r})"
    )


def _dsn(cfg: OracleConnectionConfig) -> str:
    return oracledb.makedsn(cfg.host, cfg.port, service_name=cfg.service_name)


@contextmanager
def oracle_connection(cfg: OracleConnectionConfig) -> Iterator[oracledb.Connection]:
    """Yield a thin-mode Oracle connection, closing it on exit."""
    kwargs: dict[str, Any] = {
        "user": cfg.user,
        "password": cfg.password,
        "dsn": _dsn(cfg),
    }
    if cfg.wallet_location:
        kwargs["config_dir"] = cfg.wallet_location
        kwargs["wallet_location"] = cfg.wallet_location
    conn = oracledb.connect(**kwargs)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            # Best-effort close; don't mask the original exception path.
            pass


def _normalize_column(name: str) -> str:
    return name.strip().lower()


def _coerce_value(value: Any) -> Any:
    """Convert an Oracle driver value into a JSON-serializable Python value.

    * ``datetime``/``date`` → ISO 8601 string
    * ``Decimal`` → string (preserves precision)
    * LOBs → ``.read()`` text
    * ``None`` stays ``None``
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(value).hex()
    # Oracle LOBs expose .read(); duck-type it.
    read = getattr(value, "read", None)
    if callable(read):
        try:
            data = read()
        except Exception:
            return None
        if isinstance(data, (bytes, bytearray)):
            try:
                return bytes(data).decode("utf-8")
            except UnicodeDecodeError:
                return bytes(data).hex()
        return data
    return value


def execute_domain(
    *,
    conn: Any,
    sql_text: str,
    binds: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    """Run ``sql_text`` with named ``binds`` and return ``(rows, row_count)``.

    ``rows`` is a list of dicts keyed by lowercase-stripped column names
    with JSON-serializable values. Raises :class:`OracleExecutionError`
    on a driver error.

    A single trailing ``;`` (plus surrounding whitespace) is stripped from
    ``sql_text`` before execution: ``oracledb.Cursor.execute()`` rejects a
    semicolon-terminated statement with ``ORA-00933``. Only the trailing
    terminator is touched — any internal ``;`` is left untouched, since the
    extract SQL is plain single-statement SQL, not a PL/SQL block.
    """
    statement = sql_text.rstrip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()

    try:
        cursor = conn.cursor()
        try:
            cursor.execute(statement, dict(binds))
            desc = cursor.description or []
            columns = [_normalize_column(d[0]) for d in desc]
            rows_raw = cursor.fetchall()
        finally:
            try:
                cursor.close()
            except Exception:
                pass
    except oracledb.DatabaseError as exc:
        ora = exc.args[0] if exc.args else None
        code = getattr(ora, "code", None)
        message = getattr(ora, "message", None) or str(exc)
        if isinstance(code, int):
            error_code = f"ORA-{code:05d}"
        else:
            error_code = _extract_ora_code(message)
        raise OracleExecutionError(error_code, message) from exc

    rows: list[dict[str, Any]] = []
    for raw in rows_raw:
        rows.append({col: _coerce_value(val) for col, val in zip(columns, raw)})
    return rows, len(rows)


def _extract_ora_code(message: str) -> str:
    """Best-effort extraction of an ``ORA-NNNNN`` code from a message."""
    for token in message.split():
        if token.startswith("ORA-"):
            return token.rstrip(":").rstrip(",")
    return "ORA-UNKNOWN"
