"""页面分类器测试."""

import pytest

from quota_lake.parser.page_classifier import (
    PageType,
    classify_page,
)


def make_text_block(text: str):
    return {"type": "text", "text": text, "res": text}


def make_table_block(html: str):
    return {"type": "table", "html": html, "res": {"html": html}}


class TestQuotaTable:
    """定额表页分类."""

    def test_table_with_code(self):
        blocks = [make_table_block("<td>定额编号</td><td>AA0001</td><td>AA0002</td>")]
        result = classify_page(blocks)
        assert result == PageType.QUOTA_TABLE

    def test_table_with_price(self):
        blocks = [make_table_block("<td>基价</td><td>208.98</td>")]
        result = classify_page(blocks)
        assert result == PageType.QUOTA_TABLE

    def test_table_with_labor_cost(self):
        blocks = [make_table_block("<td>人工费</td><td>100</td>")]
        result = classify_page(blocks)
        assert result == PageType.QUOTA_TABLE


class TestChapterTitle:
    """章节标题页分类."""

    def test_chapter_title(self):
        blocks = [make_text_block("A.1 土石方工程")]
        result = classify_page(blocks)
        assert result == PageType.CHAPTER_TITLE

    def test_sub_chapter(self):
        blocks = [make_text_block("A.1.1 土方工程")]
        result = classify_page(blocks)
        assert result == PageType.CHAPTER_TITLE


class TestNotes:
    """说明页分类."""

    def test_notes_keyword(self):
        blocks = [make_text_block("说明: 本章适用于...")]
        result = classify_page(blocks)
        assert result == PageType.NOTES

    def test_calc_rule(self):
        blocks = [make_text_block("计算规则: 工程量按...")]
        result = classify_page(blocks)
        assert result == PageType.NOTES


class TestCoverToc:
    """封面/目录页分类."""

    def test_cover_keyword(self):
        blocks = [make_text_block("前言")]
        result = classify_page(blocks)
        assert result == PageType.COVER_TOC

    def test_toc(self):
        blocks = [make_text_block("目录")]
        result = classify_page(blocks)
        assert result == PageType.COVER_TOC


class TestAppendix:
    """附录价格表分类."""

    def test_appendix_table(self):
        blocks = [make_table_block("<td>材料名称</td><td>取定单价</td>")]
        result = classify_page(blocks)
        assert result == PageType.APPENDIX_PRICE


class TestOther:
    """其他页面."""

    def test_empty_blocks(self):
        result = classify_page([])
        assert result == PageType.OTHER

    def test_plain_text(self):
        blocks = [make_text_block("这是普通的文本内容，没有特殊关键词")]
        result = classify_page(blocks)
        assert result == PageType.OTHER


class TestPriority:
    """分类优先级: 表格 > 文本."""

    def test_table_with_code_beats_chapter_text(self):
        """含定额编号的表格优先于章节文本."""
        blocks = [
            make_table_block("<td>定额编号</td><td>AA0001</td>"),
            make_text_block("B.2 附录"),
        ]
        result = classify_page(blocks)
        assert result == PageType.QUOTA_TABLE
