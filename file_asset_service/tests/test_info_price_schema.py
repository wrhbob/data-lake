import json
from io import BytesIO
from zipfile import ZipFile

from app.assets import register_asset
from app.models import FileProcessing
from app.processors import build_info_price_extract
from app.runner import run_processing_task


def minimal_xlsx(rows: list[list[object]]) -> bytes:
    def cell_ref(row_index: int, column_index: int) -> str:
        letters = ""
        value = column_index
        while value:
            value, remainder = divmod(value - 1, 26)
            letters = chr(65 + remainder) + letters
        return f"{letters}{row_index}"

    def cell_xml(row_index: int, column_index: int, value: object) -> str:
        ref = cell_ref(row_index, column_index)
        if isinstance(value, int | float):
            return f'<c r="{ref}"><v>{value}</v></c>'
        escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'

    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(cell_xml(row_index, column_index, value) for column_index, value in enumerate(row, start=1))
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>{''.join(sheet_rows)}</sheetData>
</worksheet>""",
        )
    return buffer.getvalue()


def test_info_price_extract_schema_matches_legacy_material_fields():
    result = build_info_price_extract(
        source_file_id="file-001",
        rows=[
            {
                "原始名称": "HPB300 盘螺",
                "名称": "盘螺",
                "规格型号": "HPB300",
                "单位": "t",
                "综合单价": "3810",
                "不含税价": "3371.68",
                "税率": "13%",
                "province": "广东省",
                "city": "广州市",
                "district": "",
                "time": "2024-01",
                "startTime": "2024-01-01",
                "endTime": "2024-01-31",
                "来源": "广州建设工程信息价",
            }
        ],
    )

    assert result["schema"] == "info_price_extract.v1"
    row = result["rows"][0]
    for field in [
        "原始名称",
        "名称",
        "规格型号",
        "单位",
        "综合单价",
        "不含税价",
        "税率",
        "province",
        "city",
        "district",
        "time",
        "startTime",
        "endTime",
        "来源",
    ]:
        assert field in row


def test_run_info_price_parse_extracts_xlsx_rows_to_json(db_session, fake_storage):
    workbook = minimal_xlsx(
        [
            ["乌鲁木齐市2026年4月份建设工程综合价格信息"],
            ["序号", "材料名称及规格型号", "单位", "除税综合\n信息价", "含税综合\n信息价"],
            [1, "热轧带肋钢筋 HRB400E Φ12", "t", 3371.68, 3810],
            [2, "普通硅酸盐水泥 P.O 42.5", "t", 420, 474.6],
            ["备注：价格含税口径按原表。"],
        ]
    )
    ingest = register_asset(
        db_session,
        fake_storage,
        tenant_code="platform_public",
        source_type="info_price_governance",
        batch_id="info-price-test",
        file_name="乌鲁木齐信息价.xlsx",
        content=workbook,
    )
    processing_id = db_session.query(FileProcessing).filter_by(
        file_id=ingest.file_id,
        processor="info_price_parse",
    ).one().processing_id

    result = run_processing_task(db_session, fake_storage, processing_id)

    task = db_session.get(FileProcessing, processing_id)
    assert result.status == "succeeded"
    payload = json.loads(fake_storage.get_object(task.output_bucket, task.output_key))
    assert task.output_bucket == "cost-extract"
    assert payload["schema"] == "info_price_extract.v1"
    assert payload["row_count"] == 2
    assert payload["rows"][0]["原始名称"] == "热轧带肋钢筋 HRB400E Φ12"
    assert payload["rows"][0]["单位"] == "t"
    assert payload["rows"][0]["综合单价"] == "3810"
    assert payload["rows"][0]["不含税价"] == "3371.68"
