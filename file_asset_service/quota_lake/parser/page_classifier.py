"""页面分类器 — 规则驱动, 将 OCR 输出分类为 6 种页面类型."""

from enum import Enum
from typing import List


class PageType(Enum):
    """页面类型枚举."""
    COVER_TOC = "cover_toc"          # 封面 / 目录 / 前言 / 编制单位
    CHAPTER_TITLE = "chapter_title"   # 章节标题页
    NOTES = "notes"                   # 说明 / 计算规则
    QUOTA_TABLE = "quota_table"       # 定额表 (含表格 + 定额编号/基价)
    APPENDIX_PRICE = "appendix_price" # 附录 / 取定价格表
    OTHER = "other"                   # 其他


def classify_page(
    blocks: List[dict],
    code_pattern: str = r"^[A-Z]{2}\d{4}$",
    chapter_pattern: str = r"^[A-Z]\.\d+",
) -> PageType:
    """根据 OCR blocks 判断页面类型.

    Args:
        blocks: OCR 解析结果, 每个 block 为 {type: str, text|html: str, ...}.
        code_pattern: 定额编号正则.
        chapter_pattern: 章节标题正则.

    Returns:
        PageType 枚举值.
    """
    import re

    has_table = False
    has_code = False
    has_price_keywords = False
    has_appendix_keywords = False
    has_chapter = False
    has_notes_keywords = False
    has_cover_keywords = False
    full_text = ""

    for blk in blocks:
        btype = blk.get("type") or blk.get("label", "")

        if btype == "table":
            has_table = True
            html = blk.get("html") or blk.get("res", {}).get("html", "")
            # 在表格 HTML 中检测编号和价格关键词
            if re.search(code_pattern, html):
                has_code = True
            if _contains_price_keywords(html):
                has_price_keywords = True
            if _contains_appendix_keywords(html):
                has_appendix_keywords = True
        else:
            text = blk.get("text") or blk.get("res", "")
            text = (text or "").strip()
            if text:
                full_text += text + "\n"

            # 章 节 检 测
            if re.search(chapter_pattern, text):
                has_chapter = True

            # 说明 / 计算规则关键词
            if _contains_notes_keywords(text):
                has_notes_keywords = True

            # 封面 / 目录关键词
            if _contains_cover_keywords(text):
                has_cover_keywords = True

    # ── 分类规则 (优先级从高到低) ──

    # 1. 含表格 + 定额编号/基价特征 → 定额表
    if has_table and (has_code or has_price_keywords):
        return PageType.QUOTA_TABLE

    # 2. 含表格 + 附录/取定价特征 → 附录价格表
    if has_table and has_appendix_keywords:
        return PageType.APPENDIX_PRICE

    # 3. 封面/目录关键词
    if has_cover_keywords:
        return PageType.COVER_TOC

    # 4. 章节标题
    if has_chapter and not has_table:
        return PageType.CHAPTER_TITLE

    # 5. 说明/计算规则 (无表格)
    if has_notes_keywords and not has_table:
        return PageType.NOTES

    # 6. 含表格但无识别特征
    if has_table:
        return PageType.QUOTA_TABLE  # 宽松回退

    return PageType.OTHER


# ── 关键词检测辅助 ──

def _contains_price_keywords(text: str) -> bool:
    """检测基价 / 费用行关键词."""
    keywords = ["基价", "人工费", "材料费", "机械费", "综合基价"]
    return any(kw in text for kw in keywords)


def _contains_appendix_keywords(text: str) -> bool:
    """检测附录 / 取定价关键词."""
    keywords = ["附录", "取定", "单价", "材料价格", "机械台班单价"]
    return any(kw in text for kw in keywords)


def _contains_notes_keywords(text: str) -> bool:
    """检测说明 / 计算规则关键词."""
    keywords = ["说明", "计算规则", "工程量计算", "章说明", "节说明"]
    return any(text.startswith(kw) or kw in text[:30] for kw in keywords)


def _contains_cover_keywords(text: str) -> bool:
    """检测封面 / 目录关键词."""
    keywords = ["目录", "前言", "编制单位", "主编单位", "批准部门", "总说明", "册说明"]
    return any(kw in text for kw in keywords)
