"""状态机 — 定额册生命周期状态转移.

包含 P0 修补: usable→qa_review 回归路径 (勘误/调价到达时).
"""

from dataclasses import dataclass
from enum import Enum


class BookStatus(Enum):
    """定额册状态枚举."""
    DRAFT = "draft"                # 草稿 (刚登记)
    INGESTED = "ingested"          # 已入湖 (PDF 上传/查重通过)
    PARSING = "parsing"            # 解析中
    PARSED = "parsed"              # 解析完成
    QA = "qa"                      # 质检中
    QA_PASSED = "qa_passed"        # 质检通过
    QA_FAILED = "qa_failed"        # 质检未通过
    REVIEW = "review"              # 人工复核队列
    USABLE = "usable"              # 可用
    ARCHIVED = "archived"          # 归档
    ERROR = "error"                # 异常


# ── 状态转移表 ──

TRANSITIONS: dict[BookStatus, set[BookStatus]] = {
    BookStatus.DRAFT:       {BookStatus.INGESTED, BookStatus.ERROR},
    BookStatus.INGESTED:    {BookStatus.PARSING, BookStatus.ERROR},
    BookStatus.PARSING:     {BookStatus.PARSED, BookStatus.ERROR},
    BookStatus.PARSED:      {BookStatus.QA, BookStatus.ERROR},
    BookStatus.QA:          {BookStatus.QA_PASSED, BookStatus.QA_FAILED, BookStatus.ERROR},
    BookStatus.QA_PASSED:   {BookStatus.REVIEW, BookStatus.USABLE, BookStatus.ARCHIVED},
    BookStatus.QA_FAILED:   {BookStatus.PARSED, BookStatus.REVIEW, BookStatus.ERROR},
    BookStatus.REVIEW:      {BookStatus.QA, BookStatus.USABLE, BookStatus.PARSED, BookStatus.ERROR},
    BookStatus.USABLE:      {
        BookStatus.QA,          # P0 修补: 勘误/调价到达时回归 qa_review
        BookStatus.ARCHIVED,
        BookStatus.ERROR,
    },
    BookStatus.ARCHIVED:    {BookStatus.ERROR},
    BookStatus.ERROR:       {BookStatus.DRAFT, BookStatus.INGESTED},
}


@dataclass
class TransitionResult:
    """状态转移结果."""
    ok: bool
    old_status: str
    new_status: str
    message: str = ""


def transition(current: str, target: str) -> TransitionResult:
    """执行状态转移 (纯函数).

    Args:
        current: 当前状态 (字符串, 与 BookStatus.value 匹配).
        target: 目标状态.

    Returns:
        TransitionResult.

    Raises:
        ValueError: 非法转移.
    """
    try:
        cur = BookStatus(current)
    except ValueError:
        return TransitionResult(ok=False, old_status=current, new_status=target,
                                message=f"未知状态: {current}")

    try:
        tgt = BookStatus(target)
    except ValueError:
        return TransitionResult(ok=False, old_status=current, new_status=target,
                                message=f"未知目标状态: {target}")

    allowed = TRANSITIONS.get(cur, set())
    if tgt not in allowed:
        allowed_str = ", ".join(s.value for s in allowed)
        return TransitionResult(
            ok=False,
            old_status=current,
            new_status=target,
            message=f"不允许从 {current} 转移到 {target} (允许: {allowed_str})",
        )

    return TransitionResult(ok=True, old_status=current, new_status=target,
                            message=f"{current} → {target}")


def allowed_transitions(status: str) -> list[str]:
    """返回当前状态允许转移到的目标状态列表."""
    try:
        cur = BookStatus(status)
    except ValueError:
        return []
    return [s.value for s in TRANSITIONS.get(cur, set())]
