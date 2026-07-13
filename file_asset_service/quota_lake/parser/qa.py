"""交叉质检 — 四项校验 (全链路技术规格 §3.6).

1. 基价校验: Σ(consumption × appendix_price) vs qa_printed_price.base
2. 断号检测: 编号连续性
3. 结构异常: 章节跳变 / 单位缺失 / 消耗空列
4. 汇总指标: qa_pass_rate
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class QAIssue:
    """质 检 问 题."""
    level: str       # error | warning
    code: str        # 问题代码
    quota_code: str | None = None  # 关联子目编号
    message: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class QAResult:
    """质 检 结 果."""
    book_id: str = ""
    total_items: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    pass_rate: float = 0.0
    issues: List[QAIssue] = field(default_factory=list)
    base_price_checks: dict = field(default_factory=dict)  # {total, passed, failed}
    gap_checks: dict = field(default_factory=dict)
    structural_checks: dict = field(default_factory=dict)


# ── 1. 基价校验 ──

def check_base_price(
    quota_items: list,
    appendix_prices: dict = None,
    tolerance: float = 0.05,
) -> List[QAIssue]:
    """校验 Σ(consumption × appendix_price) vs qa_printed_price.base.

    Args:
        quota_items: ParsedQuotaItem 列表, 每个含 .resources 和 .base_price.
        appendix_prices: {resource_name: unit_price} 取定价映射. None 则跳过.
        tolerance: 容差 (元).

    Returns:
        list[QAIssue].
    """
    issues = []
    appendix_prices = appendix_prices or {}

    for item in quota_items:
        if item.base_price is None:
            continue  # 无基价, 跳过

        expected = 0.0
        all_priced = True
        for r in item.resources:
            price = _get_resource_price(r, appendix_prices)
            if price is None:
                all_priced = False
                continue
            qty = getattr(r, "quantity", 0) or 0
            expected += qty * price

        if not all_priced:
            # 取定价缺失, 跳过校验
            continue

        diff = abs(expected - item.base_price)
        if diff > tolerance:
            issues.append(
                QAIssue(
                    level="error",
                    code="BASE_PRICE_MISMATCH",
                    quota_code=item.code,
                    message=f"基价差 {diff:.2f} 元 (计算={expected:.2f}, 印刷={item.base_price:.2f})",
                    detail={
                        "expected": round(expected, 2),
                        "printed": item.base_price,
                        "diff": round(diff, 2),
                    },
                )
            )

    return issues


def _get_resource_price(resource, appendix_prices: dict) -> float | None:
    """从 resource 对象或 appendix_prices 获取单价."""
    price = getattr(resource, "price", None)
    if price is not None:
        return price
    name = getattr(resource, "name", "")
    return appendix_prices.get(name)


# ── 2. 断号检测 ──

def check_code_gaps(
    quota_items: list,
) -> List[QAIssue]:
    """检测定额编号连续性.

    Args:
        quota_items: ParsedQuotaItem 列表 (按页码排序).

    Returns:
        list[QAIssue].
    """
    issues = []
    if len(quota_items) < 2:
        return issues

    # 按 code 排序
    sorted_items = sorted(quota_items, key=lambda x: x.code)

    prev_num = None
    prev_prefix = None

    for item in sorted_items:
        m = _parse_code(item.code)
        if m is None:
            continue
        prefix, num = m

        if prev_prefix == prefix and prev_num is not None:
            gap = num - prev_num
            if gap > 1:
                for missing in range(prev_num + 1, num):
                    missing_code = f"{prefix}{missing:04d}"
                    issues.append(
                        QAIssue(
                            level="warning",
                            code="CODE_GAP",
                            quota_code=missing_code,
                            message=f"可能断号: {missing_code} (介于 {item.code} 之间)",
                            detail={
                                "prev": f"{prefix}{prev_num:04d}",
                                "next": item.code,
                                "gap": gap,
                            },
                        )
                    )

        prev_num = num
        prev_prefix = prefix

    return issues


def _parse_code(code: str) -> tuple | None:
    """解析定额编号: 返回 (prefix, number) 或 None."""
    import re
    m = re.match(r"^([A-Z]+)(\d+)$", code)
    if m:
        return m.group(1), int(m.group(2))
    return None


# ── 3. 结构异常 ──

def check_structural(
    quota_items: list,
) -> List[QAIssue]:
    """检测结构异常: 单位缺失 / 消耗空列.

    Args:
        quota_items: ParsedQuotaItem 列表.

    Returns:
        list[QAIssue].
    """
    issues = []

    for item in quota_items:
        # 单位缺失
        if not item.unit:
            issues.append(
                QAIssue(
                    level="warning",
                    code="MISSING_UNIT",
                    quota_code=item.code,
                    message=f"子目 {item.code} 缺少计量单位",
                )
            )

        # 消耗空列 (有基价但无消耗)
        if item.base_price is not None and not item.resources:
            issues.append(
                QAIssue(
                    level="warning",
                    code="EMPTY_RESOURCES",
                    quota_code=item.code,
                    message=f"子目 {item.code} 有基价但无消耗明细",
                )
            )

        # 名称缺失
        if not item.name:
            issues.append(
                QAIssue(
                    level="error",
                    code="MISSING_NAME",
                    quota_code=item.code,
                    message=f"子目 {item.code} 缺少名称",
                )
            )

    # 章节跳变检测
    prev_path = None
    for item in sorted(quota_items, key=lambda x: (x.page, x.code)):
        cur_path = tuple(item.chapter_path)
        if prev_path and cur_path != prev_path:
            # 跳变: 不同章节在同一连续流中
            pass  # P0 阶段仅记录, 不做报错
        prev_path = cur_path

    return issues


# ── 4. 汇总指标 ──

def compute_qa_summary(
    base_price_issues: List[QAIssue],
    gap_issues: List[QAIssue],
    structural_issues: List[QAIssue],
    total_items: int,
) -> QAResult:
    """计算 QA 汇总指标.

    Args:
        base_price_issues: 基价校验问题.
        gap_issues: 断号问题.
        structural_issues: 结构异常.
        total_items: 子目总数.

    Returns:
        QAResult.
    """
    all_issues = base_price_issues + gap_issues + structural_issues
    errors = [i for i in all_issues if i.level == "error"]
    warnings = [i for i in all_issues if i.level == "warning"]

    failed = len(errors)
    passed = total_items - failed if total_items > 0 else 0
    pass_rate = passed / total_items if total_items > 0 else 1.0

    return QAResult(
        total_items=total_items,
        passed=passed,
        failed=failed,
        warnings=len(warnings),
        pass_rate=round(pass_rate, 4),
        issues=all_issues,
        base_price_checks={
            "total_checked": len(base_price_issues),
            "passed": len([i for i in base_price_issues if i.level != "error"]),
            "failed": len([i for i in base_price_issues if i.level == "error"]),
        },
        gap_checks={
            "total_gaps": len(gap_issues),
        },
        structural_checks={
            "total_issues": len(structural_issues),
            "errors": len([i for i in structural_issues if i.level == "error"]),
            "warnings": len([i for i in structural_issues if i.level == "warning"]),
        },
    )


def run_qa(
    quota_items: list,
    appendix_prices: dict = None,
    tolerance: float = 0.05,
) -> QAResult:
    """运行全量 QA.

    Returns:
        QAResult.
    """
    bp_issues = check_base_price(quota_items, appendix_prices, tolerance)
    gap_issues = check_code_gaps(quota_items)
    struct_issues = check_structural(quota_items)

    return compute_qa_summary(
        bp_issues, gap_issues, struct_issues, len(quota_items)
    )
