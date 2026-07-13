import csv
from pathlib import Path


LEDGER_PATH = Path(__file__).resolve().parents[1] / "data" / "national_info_price_sources.csv"


def load_rows():
    with LEDGER_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            row["target_region_code"]: row
            for row in csv.DictReader(handle)
            if row["province_code"] in {"510000", "120000"}
        }


def test_sichuan_first_batch_has_no_pending_placeholders():
    rows = load_rows()
    sichuan_rows = {
        code: row
        for code, row in rows.items()
        if row["province_code"] == "510000"
    }

    assert len(sichuan_rows) == 21
    assert {
        code
        for code, row in sichuan_rows.items()
        if row["source_status"] == "pending_verify"
    } == set()
    assert {
        code
        for code, row in sichuan_rows.items()
        if row["source_status"] == "auto_crawl_ready"
    } == {"510300", "510400", "510500", "510600", "510700", "510900", "511100", "511700"}
    assert {
        code
        for code, row in sichuan_rows.items()
        if row["source_status"] == "source_blocked"
    } == {"510100", "510800", "511400", "511800"}
    assert {
        code
        for code, row in sichuan_rows.items()
        if row["source_status"] == "coverage_declaration"
    } == {"511000", "511300", "511500", "511600", "511900", "512000", "513200", "513300", "513400"}


def test_tianjin_first_batch_uses_manual_blocked_path():
    row = load_rows()["120000"]

    assert row["source_status"] == "source_blocked"
    assert row["manual_path"] == "017/manual_upload"
    assert row["entry_url"]
    assert row["evidence_url"]
    assert "公开下载入口未发现" in row["audit_note"]
