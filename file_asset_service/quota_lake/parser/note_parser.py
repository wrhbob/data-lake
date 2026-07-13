"""说明文本解析 — 分类 + LLM 辅助系数提取.

模块设计为 LLM 实现可替换, 接口只接受 text→JSON。
P0 阶段: 系数规则 active=False, 需人工确认。
"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ParsedChapterNote:
    """解析出的章节说明."""
    chapter_path: list = field(default_factory=list)
    kind: str = "other"  # scope | calc_rule | other
    text: str = ""
    source_page: int = 0


@dataclass
class ParsedCoeffRule:
    """解析出的系数规则."""
    trigger: dict = field(default_factory=dict)
    action: dict = field(default_factory=dict)
    scope: dict = field(default_factory=dict)
    source_text: str = ""
    active: bool = False  # P0 默认不激活


# ── 规则匹配 ──

RE_SCOPE = re.compile(
    r"(?:本章|本节|本定额)\s*(?:适用于|包括|不适用于|不包括)\s*(.+?)(?:[。；;]|$)"
)
RE_CALC_RULE = re.compile(
    r"(?:计算\s*规则|工程量|按.*计算|乘以\s*(?:系数)?\s*[\d.]+|乘以系数)"
)
RE_COEFF_HINT = re.compile(
    r"(?:系数|乘\s*以|调整|增加|减少|乘以)\s*([\d.]+)"
)


def classify_note_kind(text: str) -> str:
    """规则匹配分类说明文本类型.

    Returns:
        'scope' | 'calc_rule' | 'other'
    """
    if RE_SCOPE.search(text):
        return "scope"
    if RE_CALC_RULE.search(text):
        return "calc_rule"
    return "other"


def parse_notes_text(
    blocks: list,
    chapter_path: list,
    page: int,
) -> List[ParsedChapterNote]:
    """从 OCR blocks 中解析章节说明.

    Args:
        blocks: 非表格 block 列表.
        chapter_path: 当前章节路径.
        page: 页码.

    Returns:
        list[ParsedChapterNote].
    """
    notes = []
    full_text = []

    for blk in blocks:
        txt = (blk.get("text") or blk.get("res", "")).strip()
        if not txt:
            continue
        full_text.append(txt)

    combined = "\n".join(full_text)

    # 按段落/说明关键词分割
    sections = re.split(r"(?:^|\n)(?:\d+[\.、]|说明|注[：:])", combined)

    for sec in sections:
        sec = sec.strip()
        if not sec or len(sec) < 5:
            continue
        kind = classify_note_kind(sec)
        notes.append(
            ParsedChapterNote(
                chapter_path=list(chapter_path),
                kind=kind,
                text=sec,
                source_page=page,
            )
        )

    if not notes and combined.strip():
        notes.append(
            ParsedChapterNote(
                chapter_path=list(chapter_path),
                kind="other",
                text=combined.strip(),
                source_page=page,
            )
        )

    return notes


def extract_coeff_rules(
    text: str,
    chapter_path: list,
    use_llm: bool = False,
    llm_fn=None,
) -> List[ParsedCoeffRule]:
    """从说明文本中提取系数规则.

    Args:
        text: 说明文本.
        chapter_path: 当前章节路径.
        use_llm: 是否使用 LLM 提取 (P0 默认关闭).
        llm_fn: LLM 调用函数, 签名 fn(text) → list[dict].

    Returns:
        list[ParsedCoeffRule].
    """
    if use_llm and llm_fn:
        return _extract_via_llm(text, chapter_path, llm_fn)
    return _extract_via_regex(text, chapter_path)


def _extract_via_regex(text: str, chapter_path: list) -> List[ParsedCoeffRule]:
    """正则提取系数规则 (简单/保守)."""
    rules = []
    matches = RE_COEFF_HINT.finditer(text)

    for m in matches:
        coeff_str = m.group(1)
        try:
            coeff_val = float(coeff_str)
        except ValueError:
            continue

        # 取匹配处前后各 50 个字符作为 source_text
        start = max(0, m.start() - 50)
        end = min(len(text), m.end() + 50)
        source_text = text[start:end].strip()

        rules.append(
            ParsedCoeffRule(
                trigger={"keyword": m.group(0), "value": coeff_val},
                action={"multiply": coeff_val},
                scope={"chapter_path": list(chapter_path)},
                source_text=source_text,
                active=False,
            )
        )

    return rules


def _extract_via_llm(text: str, chapter_path: list, llm_fn) -> List[ParsedCoeffRule]:
    """LLM 提取系数规则.

    LLM 期望输出:
        [{"trigger": {...}, "action": {...}, "scope": {...}, "source_text": "..."}]
    """
    try:
        result = llm_fn(text)
        if isinstance(result, str):
            result = json.loads(result)
        rules = []
        for r in result:
            rules.append(
                ParsedCoeffRule(
                    trigger=r.get("trigger", {}),
                    action=r.get("action", {}),
                    scope=r.get("scope", {}),
                    source_text=r.get("source_text", text[:200]),
                    active=False,
                )
            )
        return rules
    except Exception:
        return []
