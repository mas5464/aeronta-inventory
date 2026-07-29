from __future__ import annotations

from datetime import UTC, date
from types import SimpleNamespace

import trax_io_spine.pg.ingest as ingest_module


class _Result:
    def __init__(self, row=None) -> None:
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def execute(self, sql, params):
        if "select key_quota" in sql:
            return _Result((5000,))
        return _Result()


class _Storage:
    _FILES = {
        "parts": (
            b"part_number,part_class,unit_cost,criticality\n"
            b"P1,rotable,100,AOG\n"
        ),
        "stock": (
            b"part_number,location_code,on_hand,current_rop,current_eoq,"
            b"current_safety_stock,current_max\n"
            b"P1,MIA,5,3,10,2,20\n"
        ),
        "demand_history": (
            b"part_number,location_code,period,quantity,"
            b"observation_start,observation_end\n"
            b"P1,MIA,2026-01-01,3,2025-01-01,2026-01-01\n"
        ),
    }

    def download(self, path: str) -> bytes:
        return self._FILES[path]


def test_ingest_uses_immutable_canonical_manifest_date_for_planning(
    monkeypatch,
) -> None:
    calls = []

    def _from_extract(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(keys=[("P1", "MIA")])

    monkeypatch.setattr(
        ingest_module.PlannerStore,
        "from_extract",
        staticmethod(_from_extract),
    )
    monkeypatch.setattr(
        ingest_module,
        "seed_store",
        lambda *args, **kwargs: SimpleNamespace(
            recommendations=0,
            operational_telemetry={},
        ),
    )
    payload = {
        "tenant_id": "tenant-id",
        "tenant_slug": "acme",
        "batch_id": "batch",
        "files": {
            "parts": "parts",
            "stock": "stock",
            "demand_history": "demand_history",
        },
        "uploaded_by": "user",
    }

    first = ingest_module.run_ingest(
        _Conn(),
        object(),
        payload,
        storage=_Storage(),
    )
    second = ingest_module.run_ingest(
        _Conn(),
        object(),
        payload,
        storage=_Storage(),
    )

    assert first["status"] == second["status"] == "done"
    assert [call["as_of"] for call in calls] == [
        date(2026, 1, 1),
        date(2026, 1, 1),
    ]
    assert all(call["now"].tzinfo == UTC for call in calls)
