"""Station + part-cap scope filter for the nightly extract.

Lets a run pull just one location's planning-active parts (capped) instead
of the whole network, by generically wrapping each domain's SQL rather than
editing any of the 21 canonical extract files. Absent a scope, behavior is
unchanged: :func:`wrap_scoped_sql` is a no-op when ``scope_key`` or ``scope``
is ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Oracle bind-variable placeholders (and IN-lists built from them) top out at
# 1000 entries per list. W1 caps ``max_parts`` well under that; chunking a
# larger scope across multiple IN-lists is deferred to Wave 3.
_MAX_SCOPED_PARTS = 1000


@dataclass(frozen=True)
class ExtractScope:
    """A resolved station + part-cap scope for one extract run."""

    location: str
    parts: tuple[str, ...]


def resolve_scope(conn: Any, *, location: str, max_parts: int) -> ExtractScope:
    """Resolve the planning-active parts at ``location``, capped to ``max_parts``.

    Read-only query against ``PN_INVENTORY_LEVEL`` (real columns: ``PN``,
    ``LOCATION``, ``REORDER_LEVEL``, ``MAXIMUM_STOCK``). "Planning-active"
    means the PN carries a non-zero reorder level or max stock at that
    location.
    """
    sql = (
        "SELECT DISTINCT PN FROM PN_INVENTORY_LEVEL "
        "WHERE (NVL(REORDER_LEVEL,0)>0 OR NVL(MAXIMUM_STOCK,0)>0) "
        "AND LOCATION = :loc ORDER BY PN FETCH FIRST :cap ROWS ONLY"
    )
    cursor = conn.cursor()
    try:
        cursor.execute(sql, {"loc": location, "cap": max_parts})
        rows = cursor.fetchall()
    finally:
        try:
            cursor.close()
        except Exception:
            pass
    parts = tuple(row[0] for row in rows)
    return ExtractScope(location=location, parts=parts)


def wrap_scoped_sql(
    sql_text: str, scope_key: str | None, scope: ExtractScope | None
) -> tuple[str, dict[str, Any]]:
    """Wrap ``sql_text`` with a scoping filter, or return it unchanged.

    ``scope_key is None`` or ``scope is None`` → ``(sql_text, {})`` unchanged
    (unscoped path is byte-identical to today's behavior).

    Otherwise strips a trailing ``;`` and wraps the statement as
    ``SELECT * FROM ( <inner> ) traxscope WHERE ...`` filtering on
    ``traxscope.hostpartid`` (and, for ``"part_location"``, also
    ``traxscope.hostlocid``). Named binds ``scope_p0``, ``scope_p1``, ...
    (one per part) and ``scope_loc`` are returned alongside the wrapped SQL;
    merge them with the domain's own date binds without clobbering either.
    """
    if scope_key is None or scope is None:
        return sql_text, {}

    if len(scope.parts) > _MAX_SCOPED_PARTS:
        raise ValueError(
            f"scope has {len(scope.parts)} parts, exceeding the {_MAX_SCOPED_PARTS} "
            "Oracle IN-list bind cap; chunking is deferred to Wave 3"
        )

    inner = sql_text.rstrip()
    if inner.endswith(";"):
        inner = inner[:-1].rstrip()

    part_binds = {f"scope_p{i}": pn for i, pn in enumerate(scope.parts)}
    in_list = ", ".join(f":{name}" for name in part_binds)

    if scope_key == "part_location":
        where_clause = (
            f"traxscope.hostpartid IN ({in_list}) AND traxscope.hostlocid = :scope_loc"
        )
        binds: dict[str, Any] = {**part_binds, "scope_loc": scope.location}
    elif scope_key == "part":
        where_clause = f"traxscope.hostpartid IN ({in_list})"
        binds = dict(part_binds)
    else:
        raise ValueError(f"unknown scope_key: {scope_key!r}")

    wrapped = f"SELECT * FROM ( {inner} ) traxscope WHERE {where_clause}"
    return wrapped, binds
