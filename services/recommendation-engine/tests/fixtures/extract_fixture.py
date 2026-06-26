"""Writes a realistic sample nightly-extract output dir (lowercased eMRO aliases, string
values — exactly what the extract produces) for the extract_loader golden test and CLI demos.

Four parts model the canonical situations: a short rotable with an excess donor at a sibling
location (-> transfer), a high-value dead expendable (-> sell), and a busy under-leveled part
(-> adjust min/max).
"""

from __future__ import annotations

import json
from pathlib import Path

_TENANT = "acme"
_EXTRACT_DATE = "2026-04-01"

# The 21 extract domains (so we land a complete, manifest-consistent dir).
_DOMAINS = [
    "causal_values", "demand_history_rotables", "demand_history_expendables", "events",
    "location_master", "location_type", "order_plan_closed_orders", "order_plan",
    "order_plan_data_requisition", "part_chain", "part_chain_details", "part_criticality",
    "part_kit_bom", "part_location", "part_master", "pn_vendor_price", "sales_order",
    "stock_amount", "stock_level_upload", "trans_code", "vendor",
]


def _months_2025(n: int) -> list[str]:
    return [f"2025-{((i % 12) + 1):02d}-15T00:00:00" for i in range(n)]


def _demand_rotable(pn: str, loc: str, n: int) -> list[dict]:
    return [
        {"hostpartid": pn, "hostlocid": loc, "historybegdate": d, "historyamount": "1",
         "transactiontype": "REMOVE", "workordernumber": f"WO-{i}", "taskcard": f"TC-{i}"}
        for i, d in enumerate(_months_2025(n))
    ]


def _demand_expendable(pn: str, loc: str, per_month: int) -> list[dict]:
    rows = []
    for i, d in enumerate(_months_2025(12)):
        rows.append({"hostpartid": pn, "hostlocid": loc, "historybegdate": d,
                     "historyamount": str(per_month), "transactiontype": "ISSUED",
                     "workordernumber": f"WO-E{i}"})
    return rows


def write_sample_extract(target_dir: str | Path) -> Path:
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    data: dict[str, list[dict]] = {d: [] for d in _DOMAINS}

    data["stock_amount"] = [
        _stock("HYD-PUMP-001", "YYZ", 2), _stock("HYD-PUMP-001", "YOW", 30),
        _stock("FILTER-EXP-042", "YYZ", 120), _stock("VALVE-MOD-117", "YYZ", 22),
    ]
    data["stock_level_upload"] = [
        _policy("HYD-PUMP-001", "YYZ", 5, 5, 2, 40, 60), _policy("HYD-PUMP-001", "YOW", 5, 5, 2, 10, 60),
        _policy("FILTER-EXP-042", "YYZ", 2, 2, 1, 10, 21), _policy("VALVE-MOD-117", "YYZ", 1, 1, 0, 2, 21),
    ]
    data["part_master"] = [
        _part("HYD-PUMP-001", "HYDRAULIC PUMP", "3", repairable=True),
        _part("FILTER-EXP-042", "FUEL FILTER", "5"),
        _part("VALVE-MOD-117", "MODULATING VALVE", "4"),
    ]
    data["part_criticality"] = [
        {"hostpartcriticalid": "3", "partcriticaldesc": "DISPATCH CRITICAL"},
        {"hostpartcriticalid": "4", "partcriticaldesc": "ROUTINE EXPENDABLE"},
        {"hostpartcriticalid": "5", "partcriticaldesc": "CONSUMABLE"},
    ]
    data["pn_vendor_price"] = [
        _price("HYD-PUMP-001", "HONEYWELL", "4200", 60, preferred=True),
        _price("FILTER-EXP-042", "PARKER", "8500", 21, preferred=True),
        _price("VALVE-MOD-117", "EATON", "250", 21, preferred=True),
    ]
    # ~10 removals/month at YYZ -> a real shortage vs 2 on hand over the 60d protection window.
    data["demand_history_rotables"] = _demand_rotable("HYD-PUMP-001", "YYZ", 120)
    data["demand_history_expendables"] = _demand_expendable("VALVE-MOD-117", "YYZ", 30)
    data["location_master"] = [
        {"hostlocid": "YYZ", "hostparentlocid": ""},
        {"hostlocid": "YOW", "hostparentlocid": "YYZ"},
    ]

    for domain, rows in data.items():
        (target / f"{domain}.json").write_text(json.dumps(rows, indent=2))
    (target / "manifest.json").write_text(json.dumps({
        "schema_version": "1.0.0", "tenant_id": _TENANT, "extract_date": _EXTRACT_DATE,
        "run_id": "01JSAMPLE", "run_status": "succeeded", "source": "eMRO-Oracle",
        "artifacts": [{"domain": d, "status": "succeeded"} for d in _DOMAINS],
    }, indent=2))
    return target


def _stock(pn: str, loc: str, on_hand: int) -> dict:
    return {"hostpartid": pn, "hostlocid": loc, "onhandnew": str(on_hand), "onhandbad": "0",
            "inrepair": "0", "allocated": "0", "rentalqty": "0", "loanqty": "0"}


def _policy(pn: str, loc: str, rop: int, eoq: int, ss: int, mx: int, lead: int) -> dict:
    return {"hostpartid": pn, "hostlocid": loc, "rop": str(rop), "eoq": str(eoq),
            "safetylevel": str(ss), "stockmax": str(mx), "slreplenishmentlength": str(lead)}


def _part(pn: str, desc: str, crit: str, *, repairable: bool = False) -> dict:
    return {"hostpartid": pn, "partdescription": desc, "atachapter": "29",
            "hostpartcriticalid": crit, "shelflife": "0", "hazmat": "N", "tool": "N",
            "nooftails": "10", "partrepairable": "Y" if repairable else "N",
            "partserializable": "Y" if repairable else "N", "ispartkit": "N",
            "marketunitcost": "0", "averagecost": "0", "repaircost": "0"}


def _price(pn: str, vendor: str, price: str, lead: int, *, preferred: bool) -> dict:
    return {"hostvendorlocid": vendor, "hostpartid": pn, "price": price,
            "processinglength": str(lead), "condition": "NEW",
            "preferred": "Y" if preferred else "N", "minoq": "1"}
