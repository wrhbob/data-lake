#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
narrative_parser.py — 定额 MD 的"叙述类"内容抽取器（省份无关）

设计目标：
  从含 HTML <table> 的定额 Markdown 中,抽出"册说明 / 每章说明 / 每章工程量计算规则"
  这三块叙述类信息,供 xlsx_writer 装进多 sheet xlsx。

约束（详见 PIPELINE_SPEC / plan mutable-chasing-honey.md）：
  - **省份无关**：四川 (sc) / 重庆 (cq) 的 MD 结构差异由本模块统一吸收
  - **不读 HTML <table>**：tables 由 province 子脚本单独抽,本模块只关心叙述段
  - **章标题可能缺失**：CQ 章 A 没有 `## A` 显式标题,只有 `## 说明` 作为隐式入口
  - **front + back 双 preface**：SC 在 `## 目录` 前后各有一处 `## 总说明` / `## 册说明`,
    都算册说明
  - **front matter 续段**（如 CQ 的 `## 建筑面积计算规则`）并入 back_preface 末尾

输出（dict）：
  {
    "preface":    str,                                    # 册说明全文
    "chapters":   [                                       # 章列表（按出现顺序）
        {
            "code":        str,                           # 单字母 code, 如 "A" / "P"
            "title":       str,                           # 章标题(可能为空,如 CQ 章 A)
            "description": str,                           # 章说明全文
            "calc_rules":  str,                           # 工程量计算规则全文(可能为空)
        },
        ...
    ],
    "warnings":   list[str],                              # 解析过程中的容错提示
  }
"""
from __future__ import annotations

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────────────
# 正则常量
# ─────────────────────────────────────────────────────────────────────

# 二级标题行(整行以 `## ` 开头,允许全/半角空格 + 可选冒号)
# 注:用 `^##\s+` 限定行首,避免命中注释/正文中的 "## 符号"
H2_RE = re.compile(r"^##\s+(.+?)\s*$")

# 章标题:`## [A-Z] 章名` —— 单字母 + 空格 + 章名
# 排除 `## A.1 ...`(子章)和 `## AB ...`(双字母)
# 用 lookahead/lookbehind 区分;允许章名前后有空格、冒号等
CHAPTER_TITLE_RE = re.compile(r"^##\s+([A-Z])\s*[\s:：、].*$")  # 必须后接分隔符

# 子章标题:`## [A-Z]\.\d+ ...`（含三级如 A.1.1）
SUBCHAPTER_RE = re.compile(r"^##\s+([A-Z](?:\.\d+){1,2})\b")

# "册说明"标题(CQ 命名)或"总说明"标题(SC 命名);两省都用 `## 总说明`,
# SC 额外有 `## 册说明`
PREFACE_TITLES = ("总说明", "册说明")
PREFACE_TITLE_RE = re.compile(
    rf"^##\s+(?:{'|'.join(PREFACE_TITLES)})\s*[:：]?\s*$"
)

# 章说明小节:`## 说明` / `## 说明：` / `## 说明:`
DESCRIPTION_TITLE_RE = re.compile(r"^##\s+说明\s*[:：]?\s*$")

# 工程量计算规则小节:不在乎井号个数(`#`/`##`/`###` 都被 OCR 识别成),
# 只要命中"工程量计算规则"这几个字就认。
CALC_RULES_TITLE_RE = re.compile(r"^#+\s+工程量计算规则\s*[:：]?\s*$")

# TOC 标题:`## 目录`
TOC_TITLE_RE = re.compile(r"^##\s+目录\s*[:：]?\s*$")

# 章代码字母表(按出现顺序;跳过 I/O 避免误识)
CHAPTER_LETTERS = list("ABCDEFGHJKLMNPQRSTUVWXYZ")  # 跳过 I、O


# ─────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────

def parse_narrative(md_text: str) -> dict:
    """解析定额 MD 的册说明 + 各章节说明 + 工程量计算规则。

    返回 dict 包含:
      - preface:   str (册说明全文;可能含 front + back 两段)
      - chapters:  list[dict] (每章一个 dict)
      - warnings:  list[str] (解析容错提示)
    """
    lines = md_text.splitlines()
    warnings: list[str] = []

    # 1) 找 ## 目录 行号
    toc_idx = _find_first_match(lines, TOC_TITLE_RE)
    if toc_idx is None:
        warnings.append("未找到 `## 目录` 标题;按 '无 TOC' 退化处理")

    # 2) front_preface(TOC 之前的 `## 总说明` / `## 册说明`)
    front_preface = ""
    front_end = toc_idx if toc_idx is not None else len(lines)
    if toc_idx is not None and toc_idx > 0:
        front_preface = _capture_section(
            lines, start=0, end=toc_idx, stop_predicate=_is_h2_preface_or_chapter_title,
        )

    # 3) back_preface(TOC 之后到第一个章标题/章说明入口)
    back_preface = ""
    chapters: list[dict] = []

    if toc_idx is not None:
        scan_start = toc_idx + 1
    else:
        scan_start = 0

    # 在 post-TOC 范围里找册说明标题
    back_preface_start = _find_first_match_in_range(
        lines, scan_start, len(lines), PREFACE_TITLE_RE,
    )
    if back_preface_start is None:
        warnings.append("`## 目录` 之后未找到 `## 总说明` / `## 册说明`;back_preface 为空")
    else:
        # back_preface 内容:从 back_preface_start 开始,直到第一个"章入口"
        back_preface_end = _find_chapter_entry(lines, start=back_preface_start + 1)
        if back_preface_end is None:
            back_preface_end = len(lines)
            warnings.append("`## 总说明` 之后未找到任何章入口;册说明截到文件末尾")
        back_preface = _join_lines(lines, back_preface_start + 1, back_preface_end)

    # 4) 章节扫描
    # 起点:back_preface_end(若 back_preface 有) 或 toc_idx+1(若没有)
    chapters = _scan_chapters(lines, start=back_preface_end or scan_start)

    # 5) preface = front + back
    preface = _combine_preface(front_preface, back_preface)

    return {
        "preface":   preface,
        "chapters":  chapters,
        "warnings":  warnings,
    }


# ─────────────────────────────────────────────────────────────────────
# 段落捕获
# ─────────────────────────────────────────────────────────────────────

def _capture_section(
    lines: list[str],
    *,
    start: int,
    end: int,
    stop_predicate,
) -> str:
    """从 start 开始捕获内容,直到 stop_predicate(line, idx) 为真。

    stop_predicate 接收 (line_text, line_index) → bool。
    返回捕获到的 markdown 文本(不含 stop 行本身)。
    """
    captured: list[str] = []
    for i in range(start, min(end, len(lines))):
        line = lines[i]
        if stop_predicate(line, i):
            break
        captured.append(line)
    return _join_captured_lines(captured)


def _join_captured_lines(captured: list[str]) -> str:
    """清洗捕获的行列表:
      - 头尾多余空行去掉
      - 中间连续空行压成单个
      - 删 <details>/<summary> 等 OCR 噪音(整段剥)
    返回可写入 cell 的字符串。
    """
    if not captured:
        return ""

    # 剥 <details>...</details> 整段
    cleaned: list[str] = []
    in_details = False
    for line in captured:
        if "<details>" in line:
            in_details = True
            continue
        if "</details>" in line:
            in_details = False
            continue
        if in_details:
            continue
        # 单独一行的 ![](images/...) OCR 图片噪音
        if re.match(r"^\s*!\[\]\(images/[^)]+\)\s*$", line):
            continue
        cleaned.append(line)

    # 压连续空行
    squashed: list[str] = []
    prev_blank = True  # 开头不要空行
    for line in cleaned:
        is_blank = (line.strip() == "")
        if is_blank and prev_blank:
            continue
        squashed.append(line)
        prev_blank = is_blank

    # 剥末尾空行
    while squashed and squashed[-1].strip() == "":
        squashed.pop()

    return "\n".join(squashed)


def _join_lines(lines: list[str], start: int, end: int) -> str:
    """截取 [start, end) 范围的行并清洗。"""
    return _join_captured_lines(lines[start:end])


# ─────────────────────────────────────────────────────────────────────
# 锚点查找
# ─────────────────────────────────────────────────────────────────────

def _find_first_match(lines: list[str], pattern: re.Pattern) -> Optional[int]:
    """全文第一个匹配 pattern 的行号(从 0 起)。"""
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i
    return None


def _find_first_match_in_range(
    lines: list[str], start: int, end: int, pattern: re.Pattern,
) -> Optional[int]:
    """[start, end) 内第一个匹配 pattern 的行号。"""
    for i in range(start, min(end, len(lines))):
        if pattern.match(lines[i]):
            return i
    return None


def _find_chapter_entry(lines: list[str], *, start: int) -> Optional[int]:
    """从 start 起,找到第一个"章入口":
      - `## [A-Z] 章名`(显式章标题)
      - `## 说明`(隐式章入口,当还没有 current chapter)
      - `## [A-Z]\.\d+ ...`(子章标题也算章入口 —— 兜底)

    返回章入口行号;若没找到返回 None。
    """
    for i in range(start, len(lines)):
        line = lines[i]
        if CHAPTER_TITLE_RE.match(line):
            return i
        if SUBCHAPTER_RE.match(line):
            return i
        if DESCRIPTION_TITLE_RE.match(line):
            return i
    return None


# ─────────────────────────────────────────────────────────────────────
# 章节扫描(核心)
# ─────────────────────────────────────────────────────────────────────

def _scan_chapters(lines: list[str], *, start: int) -> list[dict]:
    """从 start 行开始线性扫描,产出 chapters 列表。

    状态机:
      current_chapter: 当前正在累积的章(初始 None)
      in_section:      'desc' / 'rules' / None(决定累积到哪)
      next_letter_idx: 章 code 字母表指针(隐式章入口时自增)

    触发事件:
      `## [A-Z]\.\d+` (子章)      → 截断 current,丢弃(进入表区域)
      `## [A-Z] ...`   (章标题)  → 截断 current,新建
      `## 说明`         (无 current 时) → 隐式章入口,新建
      `## 说明`         (有 current 时) → in_section = 'desc'
      `## 工程量计算规则`           → in_section = 'rules'
      其他 `## ` 二级标题           → 章内小节,作为当前 in_section 的内容
      普通行                       → 当前 in_section 的内容
    """
    chapters: list[dict] = []
    current: Optional[dict] = None
    in_section: Optional[str] = None  # 'desc' | 'rules' | None
    next_letter_idx = 0

    def flush():
        nonlocal current
        if current is not None:
            chapters.append(current)
            current = None

    for i in range(start, len(lines)):
        line = lines[i]

        # 子章标题 `## A.1 ...` 或 `## A.1.1 ...` → 表区域,截断当前 chapter
        if SUBCHAPTER_RE.match(line):
            flush()
            in_section = None
            continue

        # 显式章标题 `## A 章名`(单字母)
        m_ch = CHAPTER_TITLE_RE.match(line)
        if m_ch:
            flush()
            code = m_ch.group(1)
            title = _strip_h2_prefix(line).lstrip(code).strip()
            # 章标题里常带 "A 章名 (0104)" 这种括号后缀;保留原文
            current = {
                "code":        code,
                "title":       title,
                "description": "",
                "calc_rules":  "",
            }
            in_section = None
            continue

        # 章说明小节 `## 说明`
        if DESCRIPTION_TITLE_RE.match(line):
            if current is None:
                # 隐式章入口(CQ 章 A 无 `## A` 标题)
                if next_letter_idx >= len(CHAPTER_LETTERS):
                    # 字母表耗尽(理论不会发生);fallback 用 ? + 序号
                    code = "?"
                else:
                    code = CHAPTER_LETTERS[next_letter_idx]
                next_letter_idx += 1
                current = {
                    "code":        code,
                    "title":       "",
                    "description": "",
                    "calc_rules":  "",
                }
            in_section = "desc"
            continue

        # 工程量计算规则小节
        if CALC_RULES_TITLE_RE.match(line):
            if current is None:
                # 章标题缺失,但有 工程量计算规则 —— 视为隐式章入口
                if next_letter_idx >= len(CHAPTER_LETTERS):
                    code = "?"
                else:
                    code = CHAPTER_LETTERS[next_letter_idx]
                next_letter_idx += 1
                current = {
                    "code":        code,
                    "title":       "",
                    "description": "",
                    "calc_rules":  "",
                }
            in_section = "rules"
            continue

        # 普通二级标题(章内小节如 `## 一、一般说明` / `## (一)人工费`)——
        # 保留为 in_section 内容的一部分(不要丢大纲)
        if H2_RE.match(line):
            if current is not None and in_section is not None:
                _append_to_current(current, in_section, line)
            # 若 current is None 且行不属于上述特殊标题,跳过(罕见)
            continue

        # 普通文本行
        if current is not None and in_section is not None:
            _append_to_current(current, in_section, line)

    # 收尾:把最后一个 chapter 也 flush 出去
    flush()
    return chapters


def _append_to_current(current: dict, section: str, line: str) -> None:
    """把 line 累积到 current[section],自动避免多余空行。"""
    key = "description" if section == "desc" else "calc_rules"
    if current[key] == "":
        # 第一行,直接放
        current[key] = line.rstrip()
    else:
        current[key] = current[key] + "\n" + line.rstrip()


def _strip_h2_prefix(line: str) -> str:
    """去掉 `## ` 前缀。"""
    m = H2_RE.match(line)
    return m.group(1) if m else line


# ─────────────────────────────────────────────────────────────────────
# 前置判定(用于 stop_predicate)
# ─────────────────────────────────────────────────────────────────────

def _is_h2_preface_or_chapter_title(line: str, idx: int) -> bool:
    """判断行是否为 `## 总说明` / `## 册说明` / 显式章标题 / 子章标题。

    用作 front_preface 捕获的停止条件 —— 这些是 front matter 的边界。
    """
    return bool(
        PREFACE_TITLE_RE.match(line)
        or CHAPTER_TITLE_RE.match(line)
        or SUBCHAPTER_RE.match(line)
    )


# ─────────────────────────────────────────────────────────────────────
# preface 合成
# ─────────────────────────────────────────────────────────────────────

def _combine_preface(front: str, back: str) -> str:
    """把 front_preface 和 back_preface 合到一起;只有一边为空时不加分隔。"""
    front = front.strip()
    back = back.strip()
    if front and back:
        return front + "\n\n" + back
    return front or back