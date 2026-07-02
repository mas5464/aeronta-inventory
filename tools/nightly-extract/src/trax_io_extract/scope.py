"""Station + part-cap scope filter for the nightly extract.

Lets a run pull just one location's planning-active parts (capped), or
(Wave 3) all planning-active parts network-wide, instead of the whole
network unscoped — by generically wrapping each domain's SQL rather than
editing any of the 21 canonical extract files. Absent a scope, behavior is
unchanged: :func:`wrap_scoped_sql` is a no-op when ``scope_key`` or ``scope``
is ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Oracle bind-variable placeholders (and IN-lists built from them) top out at
# 1000 entries per list. Wave 3 lifts the old hard cap on total scope size by
# chunking the IN-list into multiple OR-ed IN(...) clauses, each within this
# per-clause limit.
_MAX_IN_LIST_SIZE = 1000


@dataclass(frozen=True)
class ExtractScope:
    """A resolved scope for one extract run.

    ``location`` is the station code for a per-station scope, or ``None``
    for a network-wide (all-stations) planning-active scope.
    """

    location: str | None
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


def resolve_scope_planning_active(conn: Any, *, max_parts: int) -> ExtractScope:
    """Resolve DISTINCT planning-active PNs network-wide (all locations), capped
    to ``max_parts``.

    Wave 3: replaces the single-station scope with a network-wide one for
    runs that need every planning-active PN (~62K), not just one station's.
    Same "planning-active" definition as :func:`resolve_scope` (non-zero
    reorder level or max stock), but without a ``LOCATION`` predicate.
    Returns an :class:`ExtractScope` with ``location=None``.
    """
    sql = (
        "SELECT DISTINCT PN FROM PN_INVENTORY_LEVEL "
        "WHERE (NVL(REORDER_LEVEL,0)>0 OR NVL(MAXIMUM_STOCK,0)>0) "
        "ORDER BY PN FETCH FIRST :cap ROWS ONLY"
    )
    cursor = conn.cursor()
    try:
        cursor.execute(sql, {"cap": max_parts})
        rows = cursor.fetchall()
    finally:
        try:
            cursor.close()
        except Exception:
            pass
    parts = tuple(row[0] for row in rows)
    return ExtractScope(location=None, parts=parts)


def _chunked_in_list(part_binds: dict[str, Any]) -> str:
    """Build one or more ``traxscope.hostpartid IN (...)`` clauses, OR-ed
    together, each covering at most ``_MAX_IN_LIST_SIZE`` binds."""
    names = list(part_binds)
    chunks = [
        names[i : i + _MAX_IN_LIST_SIZE] for i in range(0, len(names), _MAX_IN_LIST_SIZE)
    ]
    clauses = [
        "traxscope.hostpartid IN (" + ", ".join(f":{n}" for n in chunk) + ")"
        for chunk in chunks
    ]
    if len(clauses) == 1:
        return clauses[0]
    return "(" + " OR ".join(clauses) + ")"


def wrap_scoped_sql(
    sql_text: str, scope_key: str | None, scope: ExtractScope | None
) -> tuple[str, dict[str, Any]]:
    """Wrap ``sql_text`` with a scoping filter, or return it unchanged.

    ``scope_key is None`` or ``scope is None`` → ``(sql_text, {})`` unchanged
    (unscoped path is byte-identical to today's behavior).

    Otherwise strips a trailing ``;`` and wraps the statement as
    ``SELECT * FROM ( <inner> ) traxscope WHERE ...`` filtering on
    ``traxscope.hostpartid`` (and, for ``"part_location"`` with a station
    set, also ``traxscope.hostlocid``). Named binds ``scope_p0``,
    ``scope_p1``, ... (one per part) and, when scoped to a station,
    ``scope_loc`` are returned alongside the wrapped SQL; merge them with the
    domain's own date binds without clobbering either.

    ``scope.parts`` may exceed the Oracle 1000-bind IN-list cap: the part
    binds are chunked into multiple ``IN (...)`` clauses of at most 1000
    binds each, OR-ed together in the WHERE clause. Every part still gets
    exactly one named bind across the chunks.

    For ``scope_key="part_location"``: if ``scope.location`` is set (a
    single-station scope), the ``hostlocid = :scope_loc`` predicate is kept.
    If ``scope.location is None`` (network-wide planning-active scope), the
    station predicate is replaced by a **planning-active row filter** — an
    ``EXISTS`` against ``PN_INVENTORY_LEVEL`` with the same definition as
    :func:`resolve_scope_planning_active` — because a planning-active PART
    still has rows at every location, and without the row filter the
    part_location domains (policy #19, part_location #4) land the whole
    network's rows for each scoped part (984,021 downstream planning keys
    observed vs the true 62,492 on the real DB).
    """
    if scope_key is None or scope is None:
        return sql_text, {}

    if scope_key not in ("part", "part_location"):
        raise ValueError(f"unknown scope_key: {scope_key!r}")

    inner = sql_text.rstrip()
    if inner.endswith(";"):
        inner = inner[:-1].rstrip()

    part_binds = {f"scope_p{i}": pn for i, pn in enumerate(scope.parts)}
    in_clause = _chunked_in_list(part_binds)

    if scope_key == "part_location" and scope.location is not None:
        where_clause = f"{in_clause} AND traxscope.hostlocid = :scope_loc"
        binds: dict[str, Any] = {**part_binds, "scope_loc": scope.location}
    elif scope_key == "part_location":
        planning_active = (
            "EXISTS (SELECT 1 FROM PN_INVENTORY_LEVEL pil"
            " WHERE pil.PN = traxscope.hostpartid"
            " AND pil.LOCATION = traxscope.hostlocid"
            " AND (NVL(pil.REORDER_LEVEL,0)>0 OR NVL(pil.MAXIMUM_STOCK,0)>0))"
        )
        where_clause = f"{in_clause} AND {planning_active}"
        binds = dict(part_binds)
    else:
        where_clause = in_clause
        binds = dict(part_binds)

    wrapped = f"SELECT * FROM ( {inner} ) traxscope WHERE {where_clause}"
    return wrapped, binds
