#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
features.py — B 类现象检测脚本（SPEC §6.2 自动检测阶段）

作用：扫 MD，检测"现象"（不判定语义）。语义冲突项由 profile 定值。
与 baseline 联动（单向数据流）：

    MD 前 N 页
      ↓ features.detect_features(md)     ← 本文件：规则脚本扫现象
      result = FeatureDetectResult
      ├─ detected    检测到的现象 + 证据（只报"出现了什么"）
      ├─ b_switches  检测自足 → 建议 B 开关值（现象出现即可定语义）
      └─ p_conflicts 语义冲突 → 提示，需 profile 定值（现象出现但各省解释不同）
      ↓ profile 定语义（p_conflicts 项）+ 采纳 b_switches
      ctx = FeatureContext(profile=..., b_switches=...)
      ↓
      baseline.process_md_file(md, profile, ctx)   ← baseline 消费参数

v0.4.2 已实现 4 个特征（SPEC §6.2）：

  1. bracket_qty（材料行数量带括号）
     - 现象：材料明细行数量 cell 是 "(10.500)" 括号数字
     - 语义冲突（§1.5 反例）：sc/hu 括号数量=比例行"配"；yn/ha 括号数量=未计价主材
       （预制桩等单价'-'，价值未计入基价）。同现象两种解释 → p_conflicts，由 profile 定：
         yn-2020=True / ha-2016=True（未计价） / sc-2018=False（比例行）

  2. labor_name_keywords（名称关键字 → 人工明细，不计验证）
     - 现象：综合工日行 **col0 无分类标签**（col0=名称直接起）、单价无效('-')、
       数量带括号（(10.37)）
     - 判别器：'综合工日' 行 + col0 不是 B3 分类标签 → 只有 ha 命中
       （yn col0='人工' 标签已由 baseline B3 处理；sc/gd/hu 无综合工日明细行）
     - 河南：综合工日=人工，无单价 → 人工费由费行承担（force_emit_labor_fee），
       明细行只保留消耗量参考、不计验证
     - 检测自足 → b_switches

  3. machine_unit_keywords（单位关键字 → 机械）
     - 现象：材料明细行单位 cell **精确等于 '台班'** 且 col0 无分类标签
     - 判别器：单位='台班' + col0 不是 B3 分类标签 → 只有 ha 命中
       （gd/hu/yn col0='机具'/'机械' 标签已由 B3 处理；sc 无台班单位）
     - 注意精确匹配：子串会误伤 gd 调整系数表'人工、机械台班费用调整系数'、
       hu 材料名'灰浆搅拌机(台班)'
     - 检测自足 → b_switches

  4. extra_cost_labels（费行 label 自成综行）
     - 现象：基价表'其中'区含 其他措施费/安文费/规费 费行，且**与 管理费/利润 平级同表**
     - 判别器：label 费行形态（<td>label(元)</td><td>数字</td>）+ 表内含 管理费/利润 →
       只有 ha 命中（yn 规费是人工费的子行：定额人工费+规费=人工费，非平级；
       hu 规费只在总说明文字）
     - 河南：9 项费用全部分开记综字段（人工费/材料费/机械费/其他措施费/安文费/
       管理费/利润/规费），baseline 默认只认 管理费/利润/费用/增值税/一般风险费
       五种综行 → 新费类必须走本特征。验证和=基价 需计入这些综行
     - 检测自足 → b_switches

分层判定（SPEC §1.5）：
  - detected：只报现象，不下结论
  - b_switches：现象出现即可定语义（多关键字 OR 之外的自足开关）→ baseline 直接采纳
  - p_conflicts：同现象多解释 → 必须由 profile 按省定值，不在 baseline 里耍聪明
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import baseline  # 复用 parse_table 等纯解析函数（features → baseline 单向依赖，无循环）

# 括号数字 cell（数量带括号）：(10.500) / （10.500） / ( 1.2 )
BRACKET_QTY_RE = re.compile(r"^[（(]\s*[\d.,]+\s*[)）]$")

# 河南综合工日（名称关键字 → 人工明细，不计验证）
LABOR_NAME_KEYWORDS = ("综合工日",)
# 河南机械判定（单位关键字 → 机械；材料明细区无 col0 分类标签）
MACHINE_UNIT_KEYWORDS = ("台班",)
# 河南特有费（费行 label → 综行名）
EXTRA_COST_LABELS = ("其他措施费", "安文费", "规费")


@dataclass
class FeatureDetectResult:
    """检测结果：只含"现象"，语义由 profile 定。"""

    detected: dict[str, Any] = field(default_factory=dict)  # 现象名 → {证据}
    b_switches: dict[str, bool] = field(default_factory=dict)  # 检测自足 → 建议 B 开关
    p_conflicts: dict[str, str] = field(default_factory=dict)  # 语义冲突 → 提示，需 profile 定


def detect_features(md_path: str | Path) -> FeatureDetectResult:
    """扫 MD，检测 B 类现象。

    只做现象检测（二元判定，无模型、无信心度，SPEC §6.2）。语义判定交给 profile。
    """
    content = Path(md_path).read_text(encoding="utf-8")
    tables = list(re.finditer(r"<table[^>]*>.*?</table>", content, flags=re.S))
    result = FeatureDetectResult()

    # ─────────────────────────────────────────────────────────────
    # 特征 1：bracket_qty — 材料行数量带括号（未计价 vs 比例，语义冲突）
    # ─────────────────────────────────────────────────────────────
    bracket_examples: list[str] = []
    bracket_count = 0
    for m in tables:
        grid, _total_cols, _raw, _cell_cols = baseline.parse_table(m.group(0))
        for row in grid:
            for cell in row:
                if cell and BRACKET_QTY_RE.match(cell.strip()):
                    bracket_count += 1
                    if len(bracket_examples) < 3:
                        bracket_examples.append(cell.strip())
    if bracket_count:
        result.detected["bracket_qty"] = {
            "cells": bracket_count,
            "examples": bracket_examples,
            "note": "材料行数量带括号。语义冲突（§1.5）：sc/hu 括号数量=比例行'配'，"
                    "yn/ha 括号数量=未计价主材（单价无效+括号）。",
        }
        result.p_conflicts["bracket_qty_is_unpriced"] = (
            f"检测到 {bracket_count} 个括号数字 cell（例：{bracket_examples[0]}）。"
            f"语义冲突：sc/hu 括号数量=比例行'配'；yn/ha 括号数量=未计价主材"
            f"（预制桩等单价'-'，价值未计入基价）。"
            f"需 profile 定 bracket_qty_is_unpriced（yn-2020/ha-2016=True / sc-2018=False）。"
        )
    else:
        result.detected["bracket_qty"] = {"cells": 0}
        result.b_switches["bracket_qty_is_unpriced"] = False  # 无现象 → 不需设置

    # ─────────────────────────────────────────────────────────────
    # 特征 2：labor_name_keywords — 名称关键字表人工明细（河南综合工日）
    #   现象：综合工日行 **col0 无分类标签**（col0=名称直接起）。
    #   判别器：'综合工日' 行且 col0 不是 B3 分类标签。河南 col0=名称（无标签）→ 命中；
    #   yn col0='人工' 标签（B3 已处理，不依赖关键字）→ 不命中；
    #   sc/gd/hu 无综合工日明细行 → 不命中。检测自足 → b_switches。
    # ─────────────────────────────────────────────────────────────
    labor_row_count = 0
    labor_names: list[str] = []
    for m in tables:
        grid, _tc, _raw, _cc = baseline.parse_table(m.group(0))
        for row in grid:
            cells = [c.strip() if c else "" for c in row]
            if not any(cells):
                continue
            col0 = cells[0]
            col0_is_label = (col0 in baseline._BASE_MAT_CATEGORY_LABELS
                             or col0.replace(" ", "") in ("未计价", "附项", "人工"))
            if col0_is_label:
                continue  # 有 col0 标签 → B3 路径，不需要关键字
            name_cell = ""
            for c in cells[1:4]:
                if c and not BRACKET_QTY_RE.match(c):
                    name_cell = c
                    break
            if name_cell and any(kw in name_cell for kw in LABOR_NAME_KEYWORDS):
                labor_row_count += 1
                if len(labor_names) < 3:
                    labor_names.append(name_cell)
    if labor_row_count:
        result.detected["labor_name_keywords"] = {
            "keywords": LABOR_NAME_KEYWORDS,
            "rows": labor_row_count,
            "examples": labor_names,
            "note": "综合工日行 col0 无分类标签 → 人工明细（无单价，不计验证，"
                    "人工费由费行承担）。河南专属，检测自足。",
        }
        # 检测自足：无标签 + 综合工日 → 无歧义 → B 开关
        result.b_switches["labor_name_keywords"] = True

    # ─────────────────────────────────────────────────────────────
    # 特征 3：machine_unit_keywords — 单位关键字判机械（河南台班）
    #   现象：材料明细行单位='台班' 且 **col0 无分类标签**。
    #   判别器：'台班' 行且 col0 不是 B3 分类标签。河南 col0=名称（无标签）→ 命中；
    #   gd/hu/yn col0='机具'/'机械' 标签（B3 已处理）→ 不命中；sc 无台班单位 → 不命中。
    # ─────────────────────────────────────────────────────────────
    machine_unit_rows: list[str] = []
    for m in tables:
        grid, _tc, _raw, _cc = baseline.parse_table(m.group(0))
        for row in grid:
            cells = [c.strip() if c else "" for c in row]
            if not any(cells):
                continue
            col0 = cells[0]
            col0_is_label = (col0 in baseline._BASE_MAT_CATEGORY_LABELS
                             or col0.replace(" ", "") in ("未计价", "附项", "人工"))
            if col0_is_label:
                continue  # 有 col0 标签 → B3 路径，不需要关键字
            # 单位 cell **精确等于** '台班'（子串会误伤 gd 调整系数表 / hu 材料名
            # '灰浆搅拌机(台班)'）——台班在名称/标题里不算单位
            if any(c == kw for c in cells for kw in MACHINE_UNIT_KEYWORDS):
                name_cell = ""
                for c2 in cells[1:4]:
                    if c2 and not BRACKET_QTY_RE.match(c2):
                        name_cell = c2
                        break
                machine_unit_rows.append(name_cell or col0)
    if machine_unit_rows:
        result.detected["machine_unit_keywords"] = {
            "keywords": MACHINE_UNIT_KEYWORDS,
            "rows": len(machine_unit_rows),
            "examples": machine_unit_rows[:3],
            "note": "单位='台班' 且 col0 无分类标签 → 机械。河南材料区无标签，"
                    "靠单位区分料/机，检测自足。",
        }
        result.b_switches["machine_unit_keywords"] = True

    # ─────────────────────────────────────────────────────────────
    # 特征 4：extra_cost_labels — 费行 label 自成综行（河南 其他措施费/安文费/规费）
    #   现象：基价表'其中'区含 其他措施费/安文费/规费 费行，且**与 管理费/利润 同表平级**。
    #   判别器：label 出现在含 管理费(元)/利润(元) 费行的表里。河南 9 费平级 → 命中；
    #   yn 规费是人工费的子行（定额人工费+规费=人工费，非平级费行）→ 不命中；
    #   hu 规费只在总说明文字（取消规费项目单列）→ 不命中。检测自足 → b_switches。
    # ─────────────────────────────────────────────────────────────
    found_extra: list[str] = []
    for m in tables:
        tbl_text = m.group(0)
        has_mgmt_profit = ("管理费" in tbl_text or "利润" in tbl_text)
        if not has_mgmt_profit:
            continue
        for label in EXTRA_COST_LABELS:
            # 只认费行形态：label 在 td 里，后跟值 td（<td>规费(元)</td><td>145.33</td>），
            # 排除纯文字（总说明"取消规费"）。td 之间允许任意 HTML。
            if re.search(re.escape(label) + r"[（(]?\s*元?\s*[)）]?</td>\s*<td[^>]*>\s*[\d.,]+", tbl_text):
                if label not in found_extra:
                    found_extra.append(label)
    if found_extra:
        result.detected["extra_cost_labels"] = {
            "labels": found_extra,
            "note": "费行 label（其他措施费/安文费/规费）与 管理费/利润 平级同表 → "
                    "各自独立成综行（验证和=基价 需计入）。河南专属，检测自足。",
        }
        result.b_switches["extra_cost_labels"] = True

    return result


def apply_detected(
    profile: Any,
    result: FeatureDetectResult,
) -> dict[str, bool]:
    """把检测结果落到 profile 上：确认语义冲突项是否已有答案。

    p_conflicts 项（语义冲突）→ 检查 profile 是否已设值；未设则提示需人工。
    b_switches 项（检测自足）→ 直接建议，调用方构造 FeatureContext 时采纳。
    返回：仍需人工确认的冲突项。
    """
    unresolved: dict[str, bool] = {}
    for key, hint in result.p_conflicts.items():
        val = getattr(profile, key, None)
        if val is None:
            unresolved[key] = False
            print(f"[feature] ⚠️ {key}: {hint}")
        else:
            print(f"[feature] ✓ {key}: profile {profile.name} = {val}")
    return unresolved
