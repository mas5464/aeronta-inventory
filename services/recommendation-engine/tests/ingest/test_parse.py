"""Parsers: csv/xlsx bytes -> the {canonical_name: rows} shape the validator consumes."""
from trax_io_reco.ingest.parse import parse_csv, parse_uploads, parse_xlsx


def test_parse_csv_lowercases_headers():
    data = b"Part_Number,On_Hand\nP1,5\n"
    rows = parse_csv("stock", data)
    assert rows == [{"part_number": "P1", "on_hand": "5"}]


def test_parse_uploads_merges_files():
    files = {"parts": b"part_number\nP1\n", "stock": b"part_number,location_code,on_hand\nP1,MIA,5\n"}
    parsed = parse_uploads(files)
    assert set(parsed) == {"parts", "stock"}
    assert parsed["stock"][0]["location_code"] == "MIA"


def test_parse_xlsx_sheets(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "parts"
    ws.append(["part_number"])
    ws.append(["P1"])
    ws2 = wb.create_sheet("stock")
    ws2.append(["part_number", "location_code", "on_hand"])
    ws2.append(["P1", "MIA", "5"])
    p = tmp_path / "u.xlsx"
    wb.save(p)
    parsed = parse_xlsx(p.read_bytes())
    assert parsed["parts"] == [{"part_number": "P1"}]
    assert parsed["stock"][0]["on_hand"] == "5"
