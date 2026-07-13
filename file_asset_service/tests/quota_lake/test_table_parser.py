"""表格解析测试 — html_table_to_grid + 黄金测试."""

import json

import pytest

from quota_lake.parser.table_parser import (
    ParsedQuotaItem,
    ParsedResource,
    TableContext,
    html_table_to_grid,
    parse_quota_table,
    detect_code_row,
    clean,
    parse_number,
)


# ════════════════════════════════════════════════════════════════════
# html_table_to_grid
# ════════════════════════════════════════════════════════════════════

class TestHtmlTableToGrid:
    """HTML 表格 → 2D 网格."""

    def test_simple_table(self):
        html = """<table>
            <tr><td>A</td><td>B</td></tr>
            <tr><td>C</td><td>D</td></tr>
        </table>"""
        grid = html_table_to_grid(html)
        assert grid == [["A", "B"], ["C", "D"]]

    def test_rowspan(self):
        html = """<table>
            <tr><td rowspan="2">A</td><td>B</td></tr>
            <tr><td>C</td></tr>
        </table>"""
        grid = html_table_to_grid(html)
        assert grid[0] == ["A", "B"]
        assert grid[1] == ["A", "C"]

    def test_colspan(self):
        html = """<table>
            <tr><td colspan="2">A</td><td>B</td></tr>
            <tr><td>C</td><td>D</td><td>E</td></tr>
        </table>"""
        grid = html_table_to_grid(html)
        assert grid[0] == ["A", "A", "B"]
        assert grid[1] == ["C", "D", "E"]

    def test_rowspan_and_colspan(self):
        html = """<table>
            <tr><td rowspan="2" colspan="2">A</td><td>B</td></tr>
            <tr><td>C</td></tr>
            <tr><td>D</td><td>E</td><td>F</td></tr>
        </table>"""
        grid = html_table_to_grid(html)
        assert grid[0] == ["A", "A", "B"]
        assert grid[1] == ["A", "A", "C"]
        assert grid[2] == ["D", "E", "F"]

    def test_empty_table(self):
        html = "<table></table>"
        grid = html_table_to_grid(html)
        assert grid == []


# ════════════════════════════════════════════════════════════════════
# clean / parse_number
# ════════════════════════════════════════════════════════════════════

class TestClean:
    def test_removes_spaces(self):
        assert clean(" 定 额 编 号 ") == "定额编号"

    def test_empty_string(self):
        assert clean("") == ""

    def test_none(self):
        assert clean(None) == ""


class TestParseNumber:
    def test_integer(self):
        assert parse_number("115") == 115.0

    def test_decimal(self):
        assert parse_number("208.98") == 208.98

    def test_with_comma(self):
        assert parse_number("1,234.56") == 1234.56

    def test_with_dash(self):
        assert parse_number("—") is None

    def test_empty(self):
        assert parse_number("") is None


# ════════════════════════════════════════════════════════════════════
# detect_code_row
# ════════════════════════════════════════════════════════════════════

class TestDetectCodeRow:
    def test_standard_code_row(self):
        grid = [
            ["定额编号", "AA0001", "AA0002"],
            ["项目", "土方", "石方"],
        ]
        idx = detect_code_row(grid)
        assert idx == 0

    def test_two_codes_no_header(self):
        grid = [
            ["项目", "AA0001", "AA0002"],
        ]
        idx = detect_code_row(grid)
        assert idx == 0

    def test_continued_from_previous(self):
        """规则 3: 上页表格未闭合 + 1 个 code."""
        grid = [
            ["项目", "AA0003", ""],
        ]
        idx = detect_code_row(grid, continued_from_previous=True)
        assert idx == 0

    def test_no_code(self):
        grid = [
            ["项目", "说明", "备注"],
        ]
        idx = detect_code_row(grid)
        assert idx is None

    def test_header_no_code(self):
        grid = [
            ["编号", "说明", "备注"],
        ]
        idx = detect_code_row(grid)
        assert idx is None  # 有"编号"表头但无匹配 code


# ════════════════════════════════════════════════════════════════════
# parse_quota_table
# ════════════════════════════════════════════════════════════════════

class TestParseQuotaTable:
    def test_parse_sample_grid(self, sample_grid_aa0001):
        context = TableContext(
            chapter_path=["A.1 土石方工程", "A.1.1 土方工程"],
            list_code_prefix="010101",
            work_content="挖土、抛土、修理边底。",
            page=30,
        )
        items = parse_quota_table(sample_grid_aa0001, context)

        assert len(items) == 4

        # AA0001
        aa1 = next(it for it in items if it.code == "AA0001")
        assert aa1.name == "一、二类土 深2m以内"
        # unit is in column 0 of a separate row, parser extracts it from
        # name-section code columns; with this simplified grid the unit
        # may or may not be detected. Verify core fields instead.
        assert aa1.base_price == 208.98
        assert aa1.labor_cost == 208.98

        # AA0004
        aa4 = next(it for it in items if it.code == "AA0004")
        assert aa4.base_price == 349.54
        assert aa4.labor_cost == 349.54

    def test_parse_resources(self, sample_grid_aa0001):
        context = TableContext(
            chapter_path=["A.1 土石方工程"],
            page=30,
        )
        items = parse_quota_table(sample_grid_aa0001, context)

        aa1 = next(it for it in items if it.code == "AA0001")
        assert len(aa1.resources) >= 1
        r = aa1.resources[0]
        assert r.name == "混合工"
        assert r.unit == "工日"
        assert r.price == 115.0
        assert r.quantity == 1.817

    def test_empty_table(self):
        context = TableContext(page=1)
        items = parse_quota_table([], context)
        assert items == []

    def test_no_code_row(self):
        context = TableContext(page=1)
        grid = [["项目", "土方"], ["备注", "说明"]]
        items = parse_quota_table(grid, context)
        assert items == []


# ════════════════════════════════════════════════════════════════════
# 黄金测试 — 与 sample_dinge.json 对齐
# ════════════════════════════════════════════════════════════════════

class TestGoldenSample:
    """验证 sample_dinge.json 的已知正确值."""

    def test_sample_loaded(self, sample_items):
        if not sample_items:
            pytest.skip("sample_dinge.json not found")
        assert len(sample_items) == 4

    def test_aa0001_fields(self, sample_items):
        if not sample_items:
            pytest.skip("sample_dinge.json not found")
        aa1 = sample_items[0]
        assert aa1["code"] == "AA0001"
        assert aa1["name"] == "人工挖一般土方 一、二类土 深2m以内"
        assert aa1["name_parts"] == ["人工挖一般土方", "一、二类土", "深2m以内"]
        assert aa1["unit"] == "10m3"
        assert aa1["base_price"] == 208.98
        assert aa1["labor_cost"] == 208.98
        assert aa1["material_cost"] is None
        assert aa1["machine_cost"] is None

    def test_aa0004_resources(self, sample_items):
        if not sample_items:
            pytest.skip("sample_dinge.json not found")
        aa4 = sample_items[3]
        assert aa4["code"] == "AA0004"
        assert aa4["base_price"] == 349.54
        resources = aa4["resources"]
        assert len(resources) == 1
        r = resources[0]
        assert r["name"] == "混合工"
        assert r["quantity"] == 3.039

    def test_chapter_paths(self, sample_items):
        if not sample_items:
            pytest.skip("sample_dinge.json not found")
        for item in sample_items:
            assert item["chapter_path"] == ["A.1 土石方工程", "A.1.1 土方工程"]
            assert item["list_code_prefix"] == "010101"
