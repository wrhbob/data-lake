"""QA 模块测试."""

import pytest

from quota_lake.parser.qa import (
    QAResult,
    QAIssue,
    check_base_price,
    check_code_gaps,
    check_structural,
    compute_qa_summary,
    run_qa,
)
from quota_lake.parser.table_parser import ParsedQuotaItem, ParsedResource


# ════════════════════════════════════════════════════════════════════
# 基价校验
# ════════════════════════════════════════════════════════════════════

class TestBasePriceCheck:
    def test_exact_match(self):
        """基价差 = 0"""
        item = ParsedQuotaItem(
            code="AA0001",
            name="测试子目",
            base_price=208.98,
        )
        item.resources = [
            ParsedResource(category="人工", name="混合工", unit="工日",
                           price=115.0, quantity=1.817)
        ]
        issues = check_base_price([item], tolerance=0.05)
        assert len(issues) == 0  # 1.817 * 115 = 208.955 ≈ 208.98 (diff = 0.025 < 0.05)

    def test_difference_exceeds_tolerance(self):
        """基价差 > 0.05"""
        item = ParsedQuotaItem(
            code="AA0001",
            name="测试",
            base_price=300.0,  # 错误基价
        )
        item.resources = [
            ParsedResource(category="人工", name="混合工", unit="工日",
                           price=115.0, quantity=1.817)
        ]
        issues = check_base_price([item], tolerance=0.05)
        # 1.817 * 115 = 208.955, diff = 91.045 > 0.05
        assert len(issues) >= 1
        assert issues[0].level == "error"
        assert issues[0].code == "BASE_PRICE_MISMATCH"

    def test_boundary_tolerance(self):
        """边界: diff === 0.05 应通过"""
        item = ParsedQuotaItem(
            code="AA0001",
            name="测试",
            base_price=100.05,
        )
        item.resources = [
            ParsedResource(category="人工", name="混合工", unit="工日",
                           price=100.0, quantity=1.0)
        ]
        issues = check_base_price([item], tolerance=0.05)
        assert len(issues) == 0

    def test_no_base_price_skipped(self):
        """无基价跳过校验"""
        item = ParsedQuotaItem(
            code="AA0001",
            name="测试",
            base_price=None,
        )
        item.resources = [
            ParsedResource(category="人工", name="人", unit="工日",
                           price=100.0, quantity=1.0)
        ]
        issues = check_base_price([item])
        assert len(issues) == 0

    def test_missing_appendix_price_skipped(self):
        """取定价缺失跳过"""
        item = ParsedQuotaItem(
            code="AA0001",
            name="测试",
            base_price=100.0,
        )
        item.resources = [
            ParsedResource(category="人工", name="人", unit="工日",
                           price=None, quantity=1.0)
        ]
        issues = check_base_price([item])
        assert len(issues) == 0


# ════════════════════════════════════════════════════════════════════
# 断号检测
# ════════════════════════════════════════════════════════════════════

class TestCodeGaps:
    def test_no_gap(self):
        items = [
            ParsedQuotaItem(code="AA0001", name="a", page=1),
            ParsedQuotaItem(code="AA0002", name="b", page=1),
            ParsedQuotaItem(code="AA0003", name="c", page=1),
        ]
        issues = check_code_gaps(items)
        assert len(issues) == 0

    def test_single_gap(self):
        items = [
            ParsedQuotaItem(code="AA0001", name="a", page=1),
            ParsedQuotaItem(code="AA0003", name="c", page=1),
        ]
        issues = check_code_gaps(items)
        assert len(issues) == 1
        assert issues[0].code == "CODE_GAP"
        assert "AA0002" in issues[0].quota_code

    def test_empty_list(self):
        issues = check_code_gaps([])
        assert len(issues) == 0

    def test_single_item(self):
        issues = check_code_gaps([ParsedQuotaItem(code="AA0001", name="a", page=1)])
        assert len(issues) == 0

    def test_gap_different_prefix(self):
        """不同前缀不计断号"""
        items = [
            ParsedQuotaItem(code="AA0001", name="a", page=1),
            ParsedQuotaItem(code="BB0001", name="b", page=1),
        ]
        issues = check_code_gaps(items)
        assert len(issues) == 0


# ════════════════════════════════════════════════════════════════════
# 结构异常
# ════════════════════════════════════════════════════════════════════

class TestStructural:
    def test_missing_unit(self):
        items = [ParsedQuotaItem(code="AA0001", name="测试", unit="")]
        issues = check_structural(items)
        warning_codes = [i.code for i in issues]
        assert "MISSING_UNIT" in warning_codes

    def test_empty_resources_with_price(self):
        items = [
            ParsedQuotaItem(code="AA0001", name="测试", base_price=100.0,
                            unit="m3", resources=[])
        ]
        issues = check_structural(items)
        codes = [i.code for i in issues]
        assert "EMPTY_RESOURCES" in codes

    def test_missing_name(self):
        items = [ParsedQuotaItem(code="AA0001", name="", unit="m3")]
        issues = check_structural(items)
        codes = [i.code for i in issues]
        assert "MISSING_NAME" in codes

    def test_all_ok(self):
        items = [
            ParsedQuotaItem(
                code="AA0001", name="测试", unit="m3", base_price=100.0
            )
        ]
        items[0].resources = [
            ParsedResource(category="人工", name="混合工", unit="工日",
                           price=100.0, quantity=1.0)
        ]
        issues = check_structural(items)
        assert len(issues) == 0


# ════════════════════════════════════════════════════════════════════
# 汇总指标
# ════════════════════════════════════════════════════════════════════

class TestQASummary:
    def test_compute_summary(self):
        base_issues = [
            QAIssue(level="error", code="BASE_PRICE_MISMATCH", quota_code="AA0001",
                    message="基价差 10 元")
        ]
        gap_issues = [
            QAIssue(level="warning", code="CODE_GAP", quota_code="AA0002",
                    message="断号")
        ]
        struct_issues = [
            QAIssue(level="warning", code="MISSING_UNIT", quota_code="AA0003",
                    message="缺失单位")
        ]

        result = compute_qa_summary(base_issues, gap_issues, struct_issues, 10)
        assert result.total_items == 10
        assert result.failed == 1
        assert result.warnings == 2
        assert result.passed == 9
        assert result.pass_rate == pytest.approx(0.9, abs=0.01)

    def test_perfect_score(self):
        result = compute_qa_summary([], [], [], 100)
        assert result.pass_rate == 1.0
        assert result.failed == 0

    def test_run_qa_integration(self):
        """端到端 QA 调用."""
        items = [
            ParsedQuotaItem(
                code="AA0001", name="测试", unit="m3",
                base_price=115.0,
            )
        ]
        items[0].resources = [
            ParsedResource(category="人工", name="混合工", unit="工日",
                           price=115.0, quantity=1.0)
        ]
        result = run_qa(items, tolerance=0.05)
        assert result.total_items == 1
        assert result.pass_rate == 1.0

    def test_target_pass_rate(self):
        """P0 需 ≥99%."""
        items = []
        for i in range(100):
            it = ParsedQuotaItem(
                code=f"AA{i+1:04d}", name="测试", unit="m3",
                base_price=100.0,
            )
            it.resources = [
                ParsedResource(category="人工", name="混合工", unit="工日",
                               price=100.0, quantity=1.0)
            ]
            items.append(it)

        result = run_qa(items, tolerance=0.05)
        assert result.pass_rate >= 0.99, f"P0 目标 ≥99%, 实际 {result.pass_rate}"
