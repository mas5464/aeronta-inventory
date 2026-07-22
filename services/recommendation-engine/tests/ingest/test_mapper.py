"""Mapper: canonical parsed rows -> the engine's eMRO-native extract dir shape. The real
gate is that the mapped output loads through the actual engine loader unchanged."""
import json

from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.ingest.mapper import to_extract_dir


def test_mapper_produces_loadable_extract(tmp_path):
    parsed = {
        "parts": [{"part_number": "P1", "part_class": "rotable", "unit_cost": "100",
                   "criticality": "AOG"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5",
                   "current_rop": "3", "current_eoq": "10", "current_safety_stock": "2",
                   "current_max": "20"}],
        "demand_history": [{"part_number": "P1", "location_code": "MIA",
                            "period": "2026-01-01", "quantity": "3"}],
    }
    out = tmp_path / "extract"
    out.mkdir()
    to_extract_dir(parsed, out, tenant_id="t1")
    # required domain files exist with the mapped eMRO keys
    pm = json.loads((out / "part_master.json").read_text())
    assert pm[0]["hostpartid"] == "P1" and pm[0]["marketunitcost"] == "100"
    sa = json.loads((out / "stock_amount.json").read_text())
    assert sa[0]["onhandnew"] == "5" and sa[0]["hostlocid"] == "MIA"
    slu = json.loads((out / "stock_level_upload.json").read_text())
    assert slu[0]["rop"] == "3"
    # rotable demand routed to the rotables file
    assert (out / "demand_history_rotables.json").exists()
    # and the whole thing loads through the real engine loader
    fs, inv, tid, keys = build_stores_from_extract(str(out), tenant_id="t1")
    assert ("P1", "MIA") in keys
