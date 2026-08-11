#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
baseline.py — 通用定额提取器 · 共用基准（SPEC §1 / §4 / §8 动作 4 骨架）

积木模型：
    baseline（本文件基础层，跨省一致）
      + B1-B7 feature（features.py 检测，FeatureContext 承载开关）
      + P1-P7 flag（profile_schema.FeatureFlagProfile）
    → 排列组合适配任意省份定额，无需为新省写新脚本。

分层依据（v0.4 从 5 省 extractor 实测）：
    - 基础层 = 5 省完全一致的函数：normalize_unit / clean_latex_name /
      strip_numeric_brackets / to_float / parse_table
    - B 类 = 多关键字 OR / 可选检测，同段代码支持多种写法且输出都正确（SPEC §1.5）
    - P 类 = 输出结构差异，必须按 profile 设值（SPEC §4.2）

本文件状态：骨架（§8 动作 4）。
    - 基础层 ✅ 完整
    - FeatureContext ✅（B1-B7 开关）
    - find_section_rows ✅（B1+B5 合并）
    - find_cost_rows    ✅（B4+B6 合并）
    - find_projects     ✅（P1 参数化）
    - parse_material_row ⏳ 骨架（P2 分派，实现待填充）
    - extract_table     ⏳ 骨架（P5/P6/P7 分派，实现待填充）
    - process_md_file   ⏳ 入口骨架
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from profile_schema import (
    CrossPageStrategy,
    FeatureFlagProfile,
    FeeEmitStrategy,
    MaterialFeeAutoEmit,
    MaterialHeaderLayout,
    SectionSystem,
)

# ─────────────────────────────────────────────────────────────────────
# 基础层 · 常量
# ─────────────────────────────────────────────────────────────────────

LATEX_MAP: list[tuple[str, str]] = [
    ("leqslant", "≤"), ("geqslant", "≥"),
    ("longrightarrow", "→"), ("longleftarrow", "←"),
    ("leftrightarrow", "↔"), ("rightarrow", "→"), ("to", "→"),
    ("leftarrow", "←"), ("downarrow", "↓"), ("uparrow", "↑"),
    ("emptyset", "∅"), ("subseteq", "⊆"), ("supseteq", "⊇"),
    ("leq", "≤"), ("le", "≤"), ("geq", "≥"), ("ge", "≥"),
    ("neq", "≠"), ("ne", "≠"), ("approx", "≈"),
    ("times", "×"), ("prime", "′"), ("nabla", "∇"),
    ("propto", "∝"), ("perp", "⊥"), ("parallel", "∥"),
    ("alpha", "α"), ("beta", "β"), ("gamma", "γ"),
    ("delta", "δ"), ("theta", "θ"), ("lambda", "λ"),
    ("sigma", "σ"), ("omega", "ω"), ("Omega", "Ω"),
    ("Gamma", "Γ"), ("Delta", "Δ"), ("Theta", "Θ"),
    ("Lambda", "Λ"), ("Sigma", "Σ"), ("Phi", "Φ"),
    ("phi", "φ"), ("pi", "π"), ("mu", "μ"),
    ("nu", "ν"), ("rho", "ρ"), ("tau", "τ"),
    ("chi", "χ"), ("psi", "ψ"), ("Psi", "Ψ"),
    ("xi", "ξ"), ("Xi", "Ξ"), ("eta", "η"),
    ("zeta", "ζ"), ("iota", "ι"), ("kappa", "κ"),
    ("epsilon", "ε"), ("varepsilon", "ε"),
    ("upsilon", "υ"), ("Upsilon", "Υ"),
    ("infty", "∞"), ("partial", "∂"), ("sum", "∑"),
    ("prod", "∏"), ("int", "∫"), ("sqrt", "√"),
    ("angle", "∠"), ("bullet", "•"), ("circ", "°"),
    ("ast", "*"), ("star", "*"), ("sim", "~"),
    ("pm", "±"), ("mp", "∓"), ("div", "÷"),
    ("cdot", "·"), ("in", "∈"), ("notin", "∉"),
    ("subset", "⊂"), ("supset", "⊃"), ("cup", "∪"),
    ("cap", "∩"), ("forall", "∀"), ("exists", "∃"),
]


# ─────────────────────────────────────────────────────────────────────
# 基础层 · 跨省一致函数（5 省实测完全相同，直接并入 baseline）
# ─────────────────────────────────────────────────────────────────────


def normalize_unit(s: str) -> str:
    """按 SPEC §5.1 归一单位。"""
    if not s:
        return ""
    s = s.strip()
    if re.fullmatch(r"[\-−–—]+", s):
        return ""
    # 先做一轮 LaTeX 清洗简化
    s_clean = re.sub(r"\\mathrm\s*\{\s*m\s*\}", "m", s)
    s_clean = re.sub(r"\{\s*\\mathrm\s*\{\s*m\s*\}\s*\}", "m", s_clean)
    s_clean = re.sub(r"\{+\s*m\s*\}+", "m", s_clean)  # {{m}} → m
    # 匹配时允许数字前缀（100、10、空字符串）
    pat_m3 = re.compile(r"\$\s*\d*\s*m\s*\^\s*\{?\s*\{?\s*3\s*\}?\s*\}?\s*\$")
    pat_m2 = re.compile(r"\$\s*\d*\s*m\s*\^\s*\{?\s*\{?\s*2\s*\}?\s*\}?\s*\$")
    pat_m = re.compile(r"\$\s*\d*\s*m\s*\$")
    if pat_m3.search(s_clean):
        m = re.search(r"\$\s*(\d+)", s_clean)
        prefix = m.group(1) if m else ""
        return f"{prefix}m3"
    if pat_m2.search(s_clean):
        m = re.search(r"\$\s*(\d+)", s_clean)
        prefix = m.group(1) if m else ""
        return f"{prefix}m2"
    if pat_m.search(s_clean):
        m = re.search(r"\$\s*(\d+)", s_clean)
        prefix = m.group(1) if m else ""
        return f"{prefix}m"
    s = s.strip()
    if s == "k":
        return "kg"
    if s == "g":
        return "g"
    return s


def clean_latex_name(s: str) -> str:
    """按 SPEC §5.2 清洗名称文本中的 LaTeX。"""
    if not s:
        return ""
    # 1. 去除 $ 包裹
    s = re.sub(r"\$\$(.*?)\$\$", r"\1", s)
    s = re.sub(r"\$(.*?)\$", r"\1", s)
    # 2. 去除字体/格式包装
    for cmd in ["mathrm", "text", "textbf", "textit", "textsf",
                "texttt", "textup", "textsl", "textsc", "mathbb",
                "mathcal", "mathfrak", "mathit", "mathbf", "mathsf",
                "mathtt", "t"]:
        s = re.sub(r"\\?" + cmd + r"\s*\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\relax", "", s)
    # 3. 去除无意义花括号
    for _ in range(3):
        s = re.sub(r"\{\{([^}]*)\}\}", r"{\1}", s)
    s = re.sub(r"\{([^}]*)\}", r"\1", s)
    # 4. 命令转字符：匹配可选反斜杠 + 命令名（长优先）
    for cmd, ch in sorted(LATEX_MAP, key=lambda x: len(x[0]), reverse=True):
        s = re.sub(r"\\?" + re.escape(cmd), ch, s)
    # 5. HTML 实体
    s = s.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    # 6. 收尾
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s


def strip_numeric_brackets(s: str) -> str:
    """按 SPEC §5.3 去除数值括号。"""
    if not s:
        return ""
    s = s.strip()
    m = re.match(r"^[（(]([\d,\.\s]+)[)）]$", s)
    if m:
        return m.group(1).strip()
    return s


def to_float(s: str) -> float:
    """安全转 float，失败返回 0.0。"""
    if not s:
        return 0.0
    s = strip_numeric_brackets(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_table(html_text: str):
    """解析 <table>, 返回 (grid, total_cols, raw_rows, cell_cols)。

    grid: 2D 表（rowspan/colspan 已 forward-fill / 展开）
    raw_rows: 原始 cell（含 colspan/rowspan 元数据，B6 multi-label 需要）
    """
    soup = BeautifulSoup(html_text, "lxml")
    trs = soup.find_all("tr")
    if not trs:
        return [], 0, [], []

    raw_rows: list[list[dict]] = []
    for tr in trs:
        cells = []
        for td in tr.find_all("td"):
            txt = re.sub(r"\s+", " ", td.get_text(" ", strip=True))
            cells.append({
                "text": txt,
                "colspan": int(td.get("colspan", 1)),
                "rowspan": int(td.get("rowspan", 1)),
            })
        raw_rows.append(cells)

    total_cols = sum(c["colspan"] for c in raw_rows[0])
    if total_cols <= 0:
        return [], 0, raw_rows, []

    grid: list[list[str | None]] = [[None] * total_cols for _ in range(len(raw_rows))]
    cell_cols: list[list[int]] = [[] for _ in raw_rows]

    for r, cells in enumerate(raw_rows):
        col = 0
        for cell in cells:
            while col < total_cols and grid[r][col] is not None:
                col += 1
            if col >= total_cols:
                break
            cell_cols[r].append(col)
            grid[r][col] = cell["text"]
            for dc in range(1, cell["colspan"]):
                if col + dc < total_cols:
                    grid[r][col + dc] = cell["text"]
            if cell["rowspan"] > 1:
                for dr in range(1, cell["rowspan"]):
                    rr = r + dr
                    if rr < len(grid):
                        for dc in range(cell["colspan"]):
                            if col + dc < total_cols and grid[rr][col + dc] is None:
                                grid[rr][col + dc] = cell["text"]
            col += cell["colspan"]

    for r in range(len(grid)):
        for c in range(total_cols):
            if grid[r][c] is None:
                grid[r][c] = ""

    return grid, total_cols, raw_rows, cell_cols


# ─────────────────────────────────────────────────────────────────────
# FeatureContext · B 类 feature 开关（SPEC §4.1）
#
# B 类 = 多关键字 OR / 可选检测：开关开启后，同一段代码支持多种省写法，
# 输出在所有写法下都正确。开关值由 features.py 规则脚本检测或人工确认。
# ─────────────────────────────────────────────────────────────────────


@dataclass
class FeatureContext:
    """B1-B7 feature 开关 + 参数化 P flag 引用。

    B 类字段默认值 = 5 省已知写法的并集（多关键字 OR）；检测脚本可收紧。
    """

    # ---- P flag（来自 profile，见 profile_schema）----
    profile: FeatureFlagProfile

    # ---- B1 综合基价 label 关键字集合（多关键字 OR）----
    composite_labels: tuple[str, ...] = (
        "综合基价", "综合单价",
        "基价(元)", "基价（元）", "综 合 基 价", "基 价 (元)", "基 价（元）",
        "基 价(元)",  # 河南 OCR 空格变体（基+空格+价+半角括号），v0.4.2 河南实测
    )

    # ---- B1-hu：全费用 label（独立判定，B5 值检查用"全行任意数字"）----
    #   湖北 2024 的"全费用(元)"行可能只有 4 列（col3 是数字），不能用 col4+ 检查。
    #   独立出来避免混进常规 composite_labels 后漏判（hu fixture 实测反例）。
    full_cost_labels: tuple[str, ...] = ("全费用(元)", "全费用（元）", "全费用")

    # ---- B3 特殊材料分类关键字集合（col0 分类标签，parse_material_row 读取）----
    #   基础分类（材/料/机/机具/机械）内嵌 baseline；"未计价/附项/人工"等特殊
    #   分类从本字段扩展（默认覆盖 5 省；新省有新分类标签加进本字段）。
    material_categories: tuple[str, ...] = ("未计价", "附项", "人工")

    # ---- B4 特殊费类 label 集合（排除"全费用"——它是 B1 的 composite label）----
    special_cost_labels: tuple[str, ...] = ("利润", "管理费", "一般风险费", "增值税", "费用")

    # ---- B5 综合基价行要求 label + 数字同行（恒开启，无省例外）----
    composite_value_check: bool = True

    # ---- B6 multi-label 单 cell 拆分（gd A 分册 / hu 场景）----
    multi_label_split: bool = False


# ─────────────────────────────────────────────────────────────────────
# 参数化函数 · find_projects（P1 驱动）
# ─────────────────────────────────────────────────────────────────────


def find_projects(grid, total_cols: int, ctx: FeatureContext) -> list[dict]:
    """识别定额项目行（定额编号），返回 [{"id": ..., "col": ...}]。

    差异来源：各省编号格式不同 → P1 project_id_regex（profile 设值）。
    参考：sc L215 / hu L309 / bj L317（bj 特有：编号在 col0，纯数字 章-定额号）。
    """
    id_re = re.compile(ctx.profile.project_id_regex)
    seen: set[str] = set()
    projects: list[dict] = []
    # 全列扫描命中 regex；同 PID 只记录第一次（sc/bj 原版均有 seen 去重）。
    # 注意：取完整匹配串作为 id（bj 原版用 group(0)）——sc/cq 单 group 时
    # group(1)==完整串，等价。
    for r, row in enumerate(grid):
        for c in range(total_cols):
            txt = row[c].strip() if c < len(row) else ""
            if txt and id_re.fullmatch(txt):
                if txt not in seen:
                    seen.add(txt)
                    projects.append({"id": txt, "col": c, "row": r})
    return projects


# ─────────────────────────────────────────────────────────────────────
# 参数化函数 · find_section_rows（B1 + B5 合并）
# ─────────────────────────────────────────────────────────────────────


def find_section_rows(grid, total_cols: int, ctx: FeatureContext) -> dict:
    """识别 section 关键行：project_parent / composite / labor_start / 计量单位 / material_header。

    B1 composite_label_keywords：composite label 关键字集合（多关键字 OR）
    B5 composite_row_value_check：label + 数字同行才认定 composite 行
    """
    sections: dict = {}
    for r, row in enumerate(grid):
        col0 = row[0].strip()
        full_text = " ".join(c.strip() for c in row if c.strip())

        if col0 in ("项 目", "项目") and "project_parent" not in sections:
            # P1：col4 若是项目编号 → 该"项目"行是表头分组行，跳过（参考 sc L219）
            id_re = re.compile(ctx.profile.project_id_regex)
            if 4 < total_cols and id_re.fullmatch(grid[r][4].strip()):
                continue
            sections["project_parent"] = r

        if "composite" not in sections:
            # B1: 常规 composite label（综合基价/综合单价/基价）——B5 值检查用 col4+
            has_label = any(kw in full_text for kw in ctx.composite_labels)
            has_value = any(
                re.match(r"^[\d,.\s\u00a0]+$", c.strip())
                for c in row[4:] if c.strip()
            ) if ctx.composite_value_check else True
            # B1-hu: "全费用(元)" 独立判定——行内任意数字即认定（hu 行可能只有 4 列）
            has_full_cost = any(kw in full_text for kw in ctx.full_cost_labels)
            row_has_number = any(
                re.match(r"^[\d,.\s\u00a0]+$", c.strip())
                for c in row if c.strip()
            )
            if (has_label and has_value) or (has_full_cost and row_has_number):
                sections["composite"] = r

        if col0 in ("其 中", "其中") and "labor_start" not in sections:
            sections["labor_start"] = r

        if "计量单位" not in sections:
            if "计量单位" in full_text or "计 量 单 位" in full_text:
                sections["计量单位"] = r

        if "material_header" not in sections:
            # sc/hu 型：col0="名称"；cq 型：col0 空但 col2="名称"（多编码列）；
            # gd 型：col0="分类" 且 col2="名称"（P2=class-code 布局，对齐 hu L1008）；
            # bj 型：col0="工料机名称"（消耗量标准表头，对齐 extract_table_bj data_start）
            col2 = row[2].strip() if len(row) > 2 else ""
            col0_nospace = col0.replace(" ", "")
            if (col0.startswith("名 称") or col0.startswith("名称")
                    or col0_nospace.startswith("工料机名称")
                    or (col0 == "" and col2 == "名称")
                    or (col0 == "分类" and col2 == "名称")):
                sections["material_header"] = r

    return sections


# ─────────────────────────────────────────────────────────────────────
# 参数化函数 · find_cost_rows（B4 + B6 合并）
# ─────────────────────────────────────────────────────────────────────


def find_cost_rows(grid, raw_rows=None, ctx: FeatureContext = None) -> dict:
    """识别 人工费/材料费/机械费/管理费 + B4 特殊费类 行。

    B4 special_cost_rows：利润 / 管理费 / 一般风险费 / 增值税 / 费用
      （"费用"必须排除"全费用"——它是 B1 的 composite label，SPEC §5 B4 v0.3 修正）
    B6 multi_label_cell_split：gd A 分册"其中"块 4 label 挤进 1 个 <td>
      （LABEL_ORDER 顺序拆分到 r, r+1, ...，SPEC §5 B6）

    统一版内嵌所有省 label（多关键字 OR），某省 PDF 无该 label 则不触发 →
    输出与各省原版一致（SPEC §1.5 判定标准）。
    """
    if ctx is None:
        raise ValueError("find_cost_rows 需要 FeatureContext")

    cost_rows: dict = {}

    # ── B6 · multi-label 单 cell 拆分（gd A 分册 / hu 场景）────────────
    #   machine 用完整费行 label（机械费/机具费/施工机具使用费）——B6 场景是
    #   "其中"块费行 label cell，无"起重机具费"等材料名误伤风险（§4.4 只约束 Pass 2）
    if ctx.multi_label_split and raw_rows is not None:
        B6_ORDER: list[tuple[str, tuple[str, ...]]] = [
            ("labor", ("人工费",)),
            ("material_fee", ("材料费",)),
            ("machine", ("机械费", "机具费", "施工机具使用费")),
            ("management", ("管理费",)),
        ]
        for r, cells in enumerate(raw_rows):
            for cell in cells:
                cell_text_norm = re.sub(r"\s+", "", cell["text"])
                matched: list[str] = []
                for k, kws in B6_ORDER:
                    if any(kw in cell_text_norm for kw in kws):
                        matched.append(k)
                if len(matched) >= 2 and cell["rowspan"] >= len(matched):
                    for i, k in enumerate(matched):
                        if k not in cost_rows:
                            cost_rows[k] = r + i
                    break  # 一行只处理一次 multi-label cell

    # ── B4 · 逐行扫描特殊费类（多关键字 OR）───────────────────────────
    machine_labels = ctx.profile.machine_labels
    for r, row in enumerate(grid):
        full = " ".join(c.strip() for c in row if c.strip())
        full_norm = re.sub(r"\s+", "", full)

        if "labor" not in cost_rows and "人工费" in full_norm:
            cost_rows["labor"] = r

        # 材料费（排除含其他费字的行）
        if ("material_fee" not in cost_rows and "材料费" in full_norm
                and "人工费" not in full_norm
                and not any(kw in full_norm for kw in machine_labels)
                and "管理费" not in full_norm
                and "利润" not in full_norm
                and "一般风险费" not in full_norm):
            cost_rows["material_fee"] = r

        # 机械费（机械费/施工机具使用费/机具费 OR）
        if "machine" not in cost_rows:
            if any(kw in full_norm for kw in machine_labels):
                cost_rows["machine"] = r

        if "management" not in cost_rows and "管理费" in full_norm:
            cost_rows["management"] = r

        # B4：利润
        if ("profit" not in cost_rows and "利润" in ctx.special_cost_labels
                and "利" in full_norm and "管理" not in full_norm):
            cost_rows["profit"] = r

        # B4：一般风险费（重庆独有）
        if ("risk_fee" not in cost_rows and "一般风险费" in ctx.special_cost_labels
                and "一般风险费" in full_norm):
            cost_rows["risk_fee"] = r

        # B4：增值税（湖北独有）
        if "vat" not in cost_rows and "增值税" in ctx.special_cost_labels and "增值税" in full_norm:
            cost_rows["vat"] = r

        # B4：费用（湖北独有；必须排除"全费用"——B1 composite label）
        if ("fee" not in cost_rows and "费用" in ctx.special_cost_labels
                and "费用" in full_norm
                and "管理费" not in full_norm and "全费用" not in full_norm):
            cost_rows["fee"] = r

        # 河南特征（extra_cost_labels）：自定义费行 → cost_rows
        #   （其他措施费/安文费/规费等河南特有费，各自独立成综行。默认空 → 不影响 5 省/yn）
        for label in ctx.profile.extra_cost_labels:
            if label not in cost_rows and label in full_norm:
                cost_rows[label] = r

    return cost_rows


# ─────────────────────────────────────────────────────────────────────
# 骨架 · parse_material_row（P2 分派，实现待填充）
# ─────────────────────────────────────────────────────────────────────


def parse_material_row(cells, start_cols, project_cols, ctx: FeatureContext, *,
                       grid=None, grid_row_idx=None) -> dict | None:
    """解析材料明细行（P2 分派，SPEC §4.2 P2）。

    P2=class-code-name-unit-qty（bj）→ 无单价形态（量价分离）
    其他（sc/cq/gd/hu）→ 有单价通用形态（cq 超集，兼容 sc 布局）

    返回统一 dict：name / unit / price / quantities / category /
    is_other_material / is_appendix / is_proportion（bj 无单价 price=""，另带 code）。
    """
    if ctx.profile.material_header_layout == MaterialHeaderLayout.CLASS_CODE_NAME_UNIT_QTY:
        return _material_row_no_price(cells, start_cols, project_cols,
                                      grid=grid, grid_row_idx=grid_row_idx)
    return _material_row_with_price(cells, start_cols, project_cols, ctx,
                                    grid=grid, grid_row_idx=grid_row_idx)


# B3 基础分类标签（材/料/机/机具/机械，所有省公共）。
#   特殊分类（未计价/附项/人工）走 ctx.material_categories 扩展（见 parse_material_row）。
_BASE_MAT_CATEGORY_LABELS = (
    "材", "料", "机", "机具", "材 料", "材料", "机 具", "机 械", "料 料", "机械",
)


def _material_row_with_price(cells, start_cols, project_cols, ctx: FeatureContext, *,
                             grid=None, grid_row_idx=None) -> dict | None:
    """有单价通用形态（P2=name/code/class-code 型，sc/cq/gd/hu 共用）。

    cq/gd 增量（相对 sc）：
      - col0="人工" 单独分组（cq 2018）
      - 编码列（6-12 位数字）跳过（cq/gd）
      - "机具"/"附项" 分类标签 + 99xx 编码→机 + is_appendix（gd，hu 亦含）
    """
    cell_positions = []
    for cell, sc in zip(cells, start_cols):
        cell_positions.append({
            "text": cell["text"],
            "start_col": sc,
            "end_col": sc + cell["colspan"] - 1,
        })

    if not any(c["text"].strip() for c in cell_positions):
        return None

    # v3 修复（Bug1）+ v0.4.1 扩展：col0/前段列被 rowspan 继承时，从 grid 读实际值注入。
    #   补全 raw cell 起始列之前的所有非空 grid 列（含 rowspan fill 的分类/名称），
    #   并合并相邻同名 cell（rowspan fill 展开的多列，如 yn"上水"rowspan=2 colspan=2：
    #   raw[11]=[m3,5.94,0.155,0.155] 但 grid[11]=[材料,上水,上水,m3,...]，col1-2 的
    #   "上水"必须补进来，否则名称错取为"m3"）。
    if (grid is not None and grid_row_idx is not None
            and cell_positions and cell_positions[0]["start_col"] != 0
            and grid_row_idx < len(grid)):
        first_sc = cell_positions[0]["start_col"]
        for c in range(first_sc):
            txt = grid[grid_row_idx][c].strip()
            if txt:
                cell_positions.append({"text": txt, "start_col": c, "end_col": c})
        cell_positions.sort(key=lambda x: x["start_col"])
        # 合并相邻同名 cell（rowspan fill 展开的多列 → 还原为 colspan）
        merged: list[dict] = []
        for cp in cell_positions:
            if (merged and merged[-1]["text"] == cp["text"]
                    and merged[-1]["end_col"] + 1 == cp["start_col"]):
                merged[-1]["end_col"] = cp["end_col"]
            else:
                merged.append(cp)
        cell_positions = merged

    first_cell = next(c for c in cell_positions if c["text"].strip())
    raw_name = first_cell["text"].strip()

    category = "料"
    is_appendix = False
    # cq：col0="人工" 单独分组（cq 2018），列结构 col0=分类 col1=编码 col2=名 col3=单位 col4=单价 col5+=消耗量
    #   ⚠️ 仅 cq 布局启用——cq 人工行有编码列，专用列结构；yn（class-name-unit-price-qty，
    #   无编码）人工行与机械行同构，走下方 B3 通用分类分支（编码列不存在时 name_cell=nxt[0]）。
    if (raw_name == "人工"
            and ctx.profile.material_header_layout == MaterialHeaderLayout.CODE_NAME_UNIT_PRICE_QTY):
        nxt = sorted(
            (c for c in cell_positions
             if c["start_col"] > first_cell["end_col"] and c["text"].strip()),
            key=lambda x: x["start_col"],
        )
        if len(nxt) < 4:
            return None
        name = nxt[1]["text"].strip()                              # col2 = 名字
        unit = normalize_unit(nxt[2]["text"].strip())             # col3 = 单位
        price = strip_numeric_brackets(nxt[3]["text"].strip())    # col4 = 单价
        skip_end = nxt[3]["end_col"]
        quantities: dict[int, str] = {}
        for pc in project_cols:
            q_val = ""
            for c in cell_positions:
                if c["start_col"] > skip_end and c["start_col"] <= pc <= c["end_col"]:
                    q_val = strip_numeric_brackets(c["text"].strip())
                    if re.fullmatch(r"[\-−–—]+", q_val):
                        q_val = ""
                    break
            quantities[pc] = q_val
        return {
            "name": name, "unit": unit, "price": price, "quantities": quantities,
            "is_other_material": False, "is_appendix": False,
            "category": "工", "is_proportion": False,
        }
    # B3：col0 分类标签（基础分类 材/料/机/机具/机械 + 特殊分类 未计价/附项/人工）
    #   特殊分类集合来自 ctx.material_categories（去空格后匹配，覆盖 OCR 空格变体）
    elif (raw_name in _BASE_MAT_CATEGORY_LABELS
          or raw_name.replace(" ", "") in ctx.material_categories):
        raw_norm = raw_name.replace(" ", "")
        if raw_norm == "未计价":
            category = "未计价"
        elif raw_norm == "附项":
            category = "料"
            is_appendix = True
        elif raw_norm == "人工":
            category = "工"
        else:
            # 普通分类："附项" 不含"机"，自动落"料"；99xx 编码检查不会误转"机"
            category = "机" if "机" in raw_norm else "料"
        nxt = sorted(
            (c for c in cell_positions
             if c["start_col"] > first_cell["end_col"] and c["text"].strip()),
            key=lambda x: x["start_col"],
        )
        if not nxt:
            return None
        # gd：编码 99\d{7,8} → 机（广东编码规则 99xx=机械，材料→机具 中段误继承"材料"标签）
        if category == "料" and re.fullmatch(r"99\d{7,8}", nxt[0]["text"].strip()):
            category = "机"
        # cq/gd：col1 是冗余编码列（6-12 位数字），跳过；老格式无编码则当 name
        if len(nxt) >= 2 and re.fullmatch(r"\d{6,12}", nxt[0]["text"].strip()):
            name_cell = nxt[1]
        else:
            name_cell = nxt[0]
        name = name_cell["text"].strip()
        name_end_col = name_cell["end_col"]
    else:
        if re.match(r"^[机]\s+", raw_name):
            category = "机"
        elif re.match(r"^[材料]\s+", raw_name):
            category = "料"
        elif re.match(r"^[未计价]\s+", raw_name):
            # 兼容 "未计价 钢管" 这种带空格连写的形态
            category = "未计价"
        name = re.sub(r"^[材料机未计价]\s+", "", raw_name)
        name_end_col = first_cell["end_col"]

    if not name:
        return None

    # 河南特征（labor_name_keywords）：名称含关键字 → 工 + 不计验证
    #   综合工日无单价，emit 为工行只保留消耗量参考，验证以 force_emit_labor_fee 的费行为准。
    #   默认空 → 不影响 5 省/yn。
    if category == "料" and any(kw in name for kw in ctx.profile.labor_name_keywords):
        category = "工"
        is_proportion = True  # 无单价，不计验证（人工费由费行承担）

    unit_cell = next(
        (c for c in cell_positions
         if c["start_col"] > name_end_col and c["text"].strip()),
        None,
    )
    if unit_cell:
        unit = normalize_unit(unit_cell["text"].strip())
        unit_end_col = unit_cell["end_col"]
    else:
        unit = ""
        unit_end_col = name_end_col

    # 河南特征（machine_unit_keywords）：单位含关键字 → 机（无 col0 标签，靠单位区分）
    #   默认空 → 不影响有标签的省。
    if category == "料" and any(kw in unit for kw in ctx.profile.machine_unit_keywords):
        category = "机"

    # 找 price：unit 之后按 start_col 排序的第一个 cell（允许为空，不跳过空列）
    candidates = sorted(
        (c for c in cell_positions if c["start_col"] > unit_end_col),
        key=lambda x: x["start_col"],
    )
    if candidates:
        price_cell = candidates[0]
        price = strip_numeric_brackets(price_cell["text"].strip())
        price_end_col = price_cell["end_col"]
    else:
        price = ""
        price_end_col = unit_end_col

    quantities = {}
    is_proportion = False
    has_bracket_qty = False  # 任一数量 cell 带括号（云南未计价标记，见下方 B3 补充）
    for pc in project_cols:
        cell_for_pc = None
        for c in cell_positions:
            # yn（skip_price_col_in_qty=True）只找单价列之后的 cell：4-1-599 表 8 个 PID
            # 定额编号 colspan=4，单价列落在 col4=PID[0]，不跳过会把单价当数量。
            # 5 省默认 False 保留原版行为（sc HC0001 表 OCR 合并'单位单价(元)'列，数量列数
            # 与 PID 错位，sc 原版把单价当数量 → 匹配 orig 必须保持）。
            in_qty_zone = (not ctx.profile.skip_price_col_in_qty
                           or c["start_col"] > price_end_col)
            if in_qty_zone and c["start_col"] <= pc <= c["end_col"]:
                cell_for_pc = c
                break
        if cell_for_pc:
            raw_q = cell_for_pc["text"].strip()
            q = strip_numeric_brackets(raw_q)
            if re.match(r"^[（(]", raw_q):
                is_proportion = True
                has_bracket_qty = True
            if re.fullmatch(r"[\-−–—]+", q):
                q = ""
            quantities[pc] = q
        else:
            quantities[pc] = ""

    if len(project_cols) == 1:
        pc = project_cols[0]
        if not quantities.get(pc) and price_cell is not None:
            for c in cell_positions:
                if c["start_col"] <= price_cell["end_col"]:
                    continue
                v = c["text"].strip()
                v = strip_numeric_brackets(v)
                if v and re.match(r"^[\d,.\s]+$", v):
                    quantities[pc] = v
                    break

    # B2/其他材料费：金额行（sc/cq/gd，unit≠%）置 price=1.000；
    #   hu 2024 比例行（unit=%）price 置空（emit 时按 基数×%/100 算验证列）。
    is_other_material = "其他材料费" in name or "其它材料费" in name
    if is_other_material:
        if unit != "%" and (not price or re.fullmatch(r"[\-−–—]+", price)):
            price = "1.000"
        elif unit == "%":
            price = ""

    # 云南未计价主材（B3 补充，profile `bracket_qty_is_unpriced`）：col0=普通材料标签
    #   （材料/料）+ 单价无效（'-'/空）+ 数量带括号 (10.500)。云南规则："材料栏内未注明
    #   单价消耗量带'（）'的材料为'未计价材'，其价值未计入基价"。
    #   5 省的未计价用 col0="未计价" 标签（此处 category 已是"未计价"）；而 sc/hu 的括号
    #   数量是比例行（配，单价也常为 '-'）→ 语义相反，必须由 profile 区分（默认 False）。
    if (ctx.profile.bracket_qty_is_unpriced and category in ("料", "配")
            and has_bracket_qty
            and (not price or re.fullmatch(r"[\-−–—]+", price))):
        category = "未计价"
        is_proportion = False  # 未计价主材不是比例行（避免被 emit 为"配"）

    return {
        "name": name, "unit": unit, "price": price, "quantities": quantities,
        "is_other_material": is_other_material, "is_appendix": is_appendix,
        "category": category, "is_proportion": is_proportion,
    }


def _material_row_no_price(cells, start_cols, project_cols, *,
                           grid=None, grid_row_idx=None) -> dict | None:
    """无单价形态（P2=class-code-name-unit-qty，bj 消耗量标准，量价分离）。

    形态：col0=分类(人工/材料/机械) col1=编码(6-12 位数字) col2=名称 col3=单位
          col4..N=消耗量（N 个定额号 = N 列）。
    """
    cell_positions = []
    for cell, sc in zip(cells, start_cols):
        cell_positions.append({
            "text": cell["text"],
            "start_col": sc,
            "end_col": sc + cell["colspan"] - 1,
        })
    if not any(c["text"].strip() for c in cell_positions):
        return None
    if (grid is not None and grid_row_idx is not None
            and cell_positions and cell_positions[0]["start_col"] != 0
            and grid_row_idx < len(grid)):
        col0_text = grid[grid_row_idx][0].strip()
        if col0_text:
            cell_positions.insert(0, {"text": col0_text, "start_col": 0, "end_col": 0})

    col0_cell = next((c for c in cell_positions if c["start_col"] == 0), None)
    if col0_cell is None or not col0_cell["text"].strip():
        return None
    raw_norm = col0_cell["text"].strip().replace(" ", "")
    if raw_norm in ("人工",):
        category = "工"
    elif raw_norm in ("材料", "材", "料"):
        category = "料"
    elif raw_norm in ("机械", "机", "机具"):
        category = "机"
    else:
        return None  # 表头"工料机名称"等被 OCR 重复 emit 之类 → 跳过

    code_cell = None
    for c in cell_positions:
        if c["start_col"] > 0 and re.fullmatch(r"\d{6,12}", c["text"].strip()):
            code_cell = c
            break
    if code_cell is None:
        return None
    code = code_cell["text"].strip()

    name_cell = None
    for c in cell_positions:
        if c["start_col"] > code_cell["end_col"] and c["text"].strip():
            name_cell = c
            break
    if name_cell is None:
        return None
    name = name_cell["text"].strip()
    name_end_col = name_cell["end_col"]

    unit_cell = next(
        (c for c in cell_positions
         if c["start_col"] > name_end_col and c["text"].strip()),
        None,
    )
    if unit_cell is None:
        return None
    unit = normalize_unit(unit_cell["text"].strip())
    unit_end_col = unit_cell["end_col"]

    quantities = {}
    for pc in project_cols:
        q_val = ""
        for c in cell_positions:
            if c["start_col"] <= pc <= c["end_col"] and c["start_col"] >= unit_end_col:
                q_val = strip_numeric_brackets(c["text"].strip())
                if re.fullmatch(r"[\-−–—]+", q_val):
                    q_val = ""
                break
        quantities[pc] = q_val

    # 比例行识别：单位=% 必定是比例行；或名称含 其他材料费/其他机具费
    is_other_material = (unit == "%" or "其他材料费" in name or "其它材料费" in name)
    is_appendix = ("其他机具费" in name or "其它机具费" in name)
    if is_other_material and category == "工":
        is_other_material = is_appendix = False
    return {
        "name": name, "unit": unit, "price": "", "quantities": quantities,
        "is_other_material": is_other_material, "is_appendix": is_appendix,
        "category": category, "is_proportion": False, "code": code,
    }


# ─────────────────────────────────────────────────────────────────────
# 骨架 · extract_table（P5/P6/P7 分派，实现待填充）
# ─────────────────────────────────────────────────────────────────────


def find_value_in_row(row, start_col: int = 1) -> str:
    """从 start_col 起找第一个纯数值 cell（基础层，sc/cq/gd/hu/bj 同源）。

    正则 `[\d,.\s]` 的 `\s` 默认含 Unicode 空白（含 \\u00a0 与普通空格），
    覆盖 sc（显式 \\u00a0）与 bj（显式空格）两种写法。
    """
    for c in range(start_col, len(row)):
        v = row[c].strip()
        if v and re.match(r"^[\d,.\s]+$", v):
            return v
    return ""


def _grid_to_html(grid) -> str:
    """grid (list[list[str]]) → 简单 HTML 串。仅用于 B7 复合表拆分后子表重建。

    保留 colspan：grid 是 rowspan/colspan 展开后的平面表，相邻相同 cell 还原为
    colspan=N（v0.4.2 河南实测修复：ha 材料名称 `名称 colspan=2` 若不还原，子表重建
    后名称占 2 列 → 单位/单价整体错位一列）。
    **安全性**：合并只针对同行的相邻相同文本 cell；grid 往返（合并→parse_table 再
    展开）逐格值不变——数量列两项目恰好同值时，还原为 colspan=2 再展开仍两格同值。
    """
    rows = []
    for row in grid:
        # 相邻相同 cell 归组为 run；run 长 >1 → colspan=N
        runs: list[tuple[str, int]] = []
        for c in row:
            txt = (c or "").strip()
            if runs and runs[-1][0] == txt:
                runs[-1] = (txt, runs[-1][1] + 1)
            else:
                runs.append((txt, 1))
        cells_out = []
        for txt, n in runs:
            if n > 1:
                cells_out.append(f'<td colspan="{n}">{txt}</td>')
            else:
                cells_out.append(f"<td>{txt}</td>")
        rows.append("<tr>" + "".join(cells_out) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


def _fmt_v(v: float) -> str:
    """验证列格式化：两位小数去尾零（与各省 extract_table 一致）。"""
    s = f"{v:.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _extract_project_names(grid, total_cols: int, project_cols: list[int],
                           filter_numeric: bool = False):
    """提取项目名，返回 (names, last_lines)。

    names：col0="项目"/"项 目"（非"编"开头）或"子目名称"（gd 型）行区域拼接。
      - "子目名称" block（gd 型）不过滤纯数字（规格档位如 C1-4 的 2/3/4/5 是描述一部分）；
      - "项目" block 纯数字：sc 的规格档位（"300 g/m²"）保留；hu/cq 的 forward-fill
        污染（"80"）由 filter_numeric 开关过滤（行为分叉 → FeatureContext 开关）；
      - 跳过"定额编号"/"项目编码"标签行。

    last_lines：每个项目列的"项目/子目名称 block 最后一行非空 cell"——
      gd/bj 的"计量单位=见表"时用它覆盖单位列（规格档位如 gd 的"1000m3"、
      bj 的"对"/"个"）；hu/sc 计量单位非"见表"则不触发，无影响。
    """
    parent_rows = [r for r, row in enumerate(grid)
                   if (re.match(r"^(项目|项 目)(?!\s*编)", row[0].strip())
                       or row[0].strip() == "子目名称")]
    names: list[str] = []
    last_lines: list[str] = []
    for pc in project_cols:
        parts: list[str] = []
        last_line_text = ""
        for pr in parent_rows:
            if pc >= total_cols:
                continue
            txt = grid[pr][pc].strip()
            txt = re.sub(r"^(项目|项 目|子目名称)\s*", "", txt)
            is_zimiao = (grid[pr][0].strip() == "子目名称")
            if not txt:
                continue
            if not is_zimiao and filter_numeric and re.fullmatch(r"[\d.,\s]+", txt):
                continue  # hu/cq "项目"block 纯数字污染
            if txt in ("定额编号", "项目编码", "项目编 号"):
                continue
            last_line_text = txt  # 记录 block 最后一行（"见表"单位覆盖用）
            if not parts or txt != parts[-1]:
                parts.append(txt)
        names.append(" ".join(parts))
        last_lines.append(last_line_text)
    return names, last_lines


def extract_table(html_text: str, working_content: str, unit_fallback: str,
                  ctx: FeatureContext, section_id: str = "",
                  seen_pids: set[str] | None = None,
                  pid_has_machine=None, pid_has_material=None):
    """单表抽取，返回 (csv_rows, issue_dict | None)。emit 行为由 P5/P6 参数化。

    P5 fee_emit_strategy（SPEC §5 P5）：
      always（sc）          ：工/机行有就 emit
      three-correspondence  ：下方有对应分类则不 emit（cq/gd/hu）
      none（bj）            ：不 emit 任何费行（无价类信息，基价/验证恒空）
    P6 material_fee_auto_emit（SPEC §5 P6）：
      sc-style  ：has_unpriced OR (无已计价料行 AND 材料费>0)，计入 verify
      cq-style  ：材料费行存在 AND 无料分类 AND 值>0，不计 verify
      gd/hu-style：cq + pid_material_global 检查，不计 verify
      none（bj） ：不 emit
    综行（B4）：管理费/利润/费用/增值税/一般风险费 按 cost_rows 存在 emit。
    """
    grid, total_cols, raw_rows, cell_cols = parse_table(html_text)
    if not grid or total_cols < 4 or not raw_rows:
        return [], None

    # B7 复合表拆分（SPEC §4.1 B7，hu/gd v0.10 真实场景）：
    #   一个 <table> 内含多张定额表（col0="定额编号" 行 ≥2，OCR 把同页多表合并）。
    #   按"定额编号"行切割 grid 为多个 sub-grid，重建 HTML 后递归抽取。
    quota_id_rows = [r for r, row in enumerate(grid) if row[0].strip() == "定额编号"]
    if len(quota_id_rows) > 1:
        all_csv_rows: list[list[str]] = []
        first_issue = None
        for gi, sr in enumerate(quota_id_rows):
            end = quota_id_rows[gi + 1] if gi + 1 < len(quota_id_rows) else len(grid)
            sub_grid = grid[sr:end]
            sub_html = _grid_to_html(sub_grid)
            sub_rows, sub_issue = extract_table(
                sub_html, working_content, unit_fallback, ctx,
                section_id=section_id,
                pid_has_machine=pid_has_machine, pid_has_material=pid_has_material)
            if sub_issue is not None and first_issue is None:
                first_issue = sub_issue
            all_csv_rows.extend(sub_rows)
        return all_csv_rows, first_issue

    projects = find_projects(grid, total_cols, ctx)
    if not projects:
        return [], None
    n_projects = len(projects)
    project_cols = [p["col"] for p in projects]

    # P7 跨页续表检测（gd/hu join 场景；seen_pids 由 process_md_file 维护）。
    #   条件：seen_pids 非空 AND 当前表所有 pid 已见 AND grid 无基价行
    #   → 抽续表的料/机行 + 返回 continuation meta（process_md_file 拼接回主表）
    p7 = ctx.profile.cross_page_strategy
    if (p7 == CrossPageStrategy.JOIN
            and seen_pids is not None and seen_pids):
        all_seen = all(p["id"] in seen_pids for p in projects)
        if all_seen:
            has_base_row = False
            for row in grid:
                rowtxt = " ".join(c.strip() for c in row if c.strip())
                if ("基价" in rowtxt and ("元" in rowtxt or "(" in rowtxt or "（" in rowtxt)):
                    has_base_row = True
                    break
                if "综合基价" in rowtxt or "综合单价" in rowtxt:
                    has_base_row = True
                    break
            if not has_base_row:
                # 从第一个分类标签行开始抽续表材料行
                material_start: int | None = None
                for r, row in enumerate(grid):
                    if row[0].strip() in ("材料", "机具", "工", "未计价", "配"):
                        material_start = r
                        break
                if material_start is None:
                    return [], None
                cont_materials: list[dict] = []
                for ri in range(material_start, len(raw_rows)):
                    mat = parse_material_row(raw_rows[ri], cell_cols[ri], project_cols, ctx,
                                             grid=grid, grid_row_idx=ri)
                    if mat is not None:
                        cont_materials.append(mat)
                cont_rows: list[list[str]] = []
                cont_verify: dict[str, float] = {p["id"]: 0.0 for p in projects}
                for mat in cont_materials:
                    if mat["category"] == "未计价":
                        continue
                    is_proportion = mat.get("is_proportion", False)
                    is_other = mat["is_other_material"]
                    price = strip_numeric_brackets(mat["price"])
                    for j, pc in enumerate(project_cols):
                        q = strip_numeric_brackets(mat["quantities"].get(pc, ""))
                        qty_f, price_f = to_float(q), to_float(price)
                        if is_proportion:
                            v_str = ""
                        elif is_other:
                            v = qty_f
                            v_str = _fmt_v(v)
                        else:
                            if qty_f == 0.0:
                                v = 0.0
                            elif price_f == 0.0:
                                v = qty_f
                            else:
                                v = qty_f * price_f
                            v_str = _fmt_v(v)
                        if not is_proportion:
                            cont_verify[projects[j]["id"]] += to_float(v_str)
                        row_type = mat.get("category", "料")
                        if row_type == "料" and is_proportion:
                            row_type = "配"
                        # col 1 暂保留 PID（process_md_file 按 PID 分组后清空）
                        cont_rows.append([
                            row_type, projects[j]["id"],
                            clean_latex_name(mat["name"]), "",
                            mat["unit"], q, price, v_str, "", "",
                        ])
                return (cont_rows, {
                    "continuation": True,
                    "pids": [p["id"] for p in projects],
                    "verify_deltas": cont_verify,
                })

    sections = find_section_rows(grid, total_cols, ctx)
    cost_rows = find_cost_rows(grid, raw_rows, ctx)

    project_names, project_names_last_line = _extract_project_names(
        grid, total_cols, project_cols, filter_numeric=ctx.profile.filter_project_numeric)

    # 综合基价
    composite_values: list[str] = []
    if "composite" in sections:
        cr = sections["composite"]
        for i in range(n_projects):
            c = project_cols[i]
            v = grid[cr][c].strip() if c < total_cols else ""
            if not v and n_projects == 1:
                v = find_value_in_row(grid[cr], start_col=1)
            composite_values.append(strip_numeric_brackets(v))
    else:
        composite_values = ["" for _ in range(n_projects)]

    def get_cost_values(name: str) -> list[str]:
        if name not in cost_rows:
            return ["" for _ in range(n_projects)]
        cr = cost_rows[name]
        row = grid[cr]
        if n_projects == 1:
            v = strip_numeric_brackets(find_value_in_row(row, start_col=1))
            if re.fullmatch(r"[\-−–—]+", v):
                v = ""
            return [v]
        vals = []
        for i in range(n_projects):
            c = project_cols[i]
            v = row[c].strip() if c < len(row) else ""
            v = strip_numeric_brackets(v)
            if re.fullmatch(r"[\-−–—]+", v):
                v = ""
            vals.append(v)
        return vals

    labor_values = get_cost_values("labor")
    material_fee_values = get_cost_values("material_fee")
    machine_values = get_cost_values("machine")
    management_values = get_cost_values("management")
    profit_values = get_cost_values("profit")
    fee_values = get_cost_values("fee")
    vat_values = get_cost_values("vat")
    risk_fee_values = get_cost_values("risk_fee")

    # 项目单位（gd/bj："计量单位=见表"或 unit_fallback=见表 时，
    #   用 项目/子目名称 block 最后一行覆盖——规格档位如 gd "1000m3" / bj "对"；
    #   对齐 gd 原版：正则 见\s*表 匹配变体，含无计量单位行的 else 分支）
    project_units: list[str] = []
    if "计量单位" in sections:
        mr = sections["计量单位"]
        for i in range(n_projects):
            c = project_cols[i]
            u = grid[mr][c].strip() if c < total_cols else ""
            norm_u = normalize_unit(u)
            # gd/bj："计量单位=见表"时用 block 最后一行覆盖（正则 见\s*表 匹配变体）
            if (ctx.profile.unit_last_line_override and re.fullmatch(r"见\s*表", norm_u)
                    and i < len(project_names_last_line)):
                ll_norm = normalize_unit(project_names_last_line[i].strip())
                if ll_norm and not re.fullmatch(r"见\s*表", ll_norm):
                    norm_u = ll_norm
            project_units.append(norm_u)
    else:
        # gd/bj：OCR 表常无"计量单位"行，单位落在 md 前置"单位:见表"→ unit_fallback
        # sh（project_unit_last_line）：无"计量单位"行也无"见表"，项目单位 = 项目块最后一行（$m^3$）
        unit_fallback_norm = normalize_unit(unit_fallback)
        use_last_line = (ctx.profile.project_unit_last_line
                         or (ctx.profile.unit_last_line_override
                             and re.fullmatch(r"见\s*表", unit_fallback_norm)))
        if use_last_line:
            for i in range(n_projects):
                ll_norm = normalize_unit(
                    project_names_last_line[i].strip()) if i < len(project_names_last_line) else ""
                if ll_norm and not re.fullmatch(r"见\s*表", ll_norm):
                    project_units.append(ll_norm)
                else:
                    project_units.append(unit_fallback)
        else:
            project_units = [unit_fallback for _ in range(n_projects)]

    # 材料（P2 分派解析）
    materials: list[dict] = []
    mat_start: int | None = None
    if "material_header" in sections:
        mat_start = sections["material_header"] + 1
    elif ctx.profile.material_header_implicit:
        # 上海特征（material_header_implicit）：材料区无「名称」表头行，表直接从
        #   定额编号/项目/单位 块跳到 人工/机械/材料 分类行。
        #   材料起点 = 项目 块后第一个 col0 为 B3 分类标签（人工/材料/机械/材/料/机/机具）
        #   的行；col0='项目'/'单位' 的 rowspan 填充行天然被跳过。
        for ri in range(len(grid)):
            col0 = grid[ri][0].strip() if grid[ri] else ""
            # 覆盖 _material_row_no_price 接受的全部 col0 分类（人工/材料/机械/材/料/机/机具）
            if col0.replace(" ", "") in ("人工", "材料", "机械", "材", "料", "机", "机具"):
                mat_start = ri
                break
    if mat_start is not None:
        for ri in range(mat_start, len(raw_rows)):
            mat = parse_material_row(raw_rows[ri], cell_cols[ri], project_cols, ctx,
                                     grid=grid, grid_row_idx=ri)
            if mat is not None:
                materials.append(mat)

    # 料行校验
    issue_reason = ""
    for mat in materials:
        if mat["category"] == "未计价":
            if not mat["unit"]:
                issue_reason = f"主材行缺少单位: {mat['name']}"
                break
            continue
        if not mat["unit"]:
            issue_reason = f"料行缺少单位: {mat['name']}"
            break
        if len(mat["quantities"]) != n_projects:
            issue_reason = (f"料行数量列数不匹配(期望{n_projects}, 实际{len(mat['quantities'])}): "
                            f"{mat['name']}")
            break
    if issue_reason:
        return [], {
            "section_id": section_id,
            "project_id": projects[0]["id"] if projects else "-",
            "reason": issue_reason,
            "prefix": f"工作内容:{working_content} / 单位:{unit_fallback}",
            "html": html_text[:800],
        }

    p5 = ctx.profile.fee_emit_strategy
    p6 = ctx.profile.material_fee_auto_emit

    def _has_valid_material_fee(s: str) -> bool:
        if not s:
            return False
        try:
            return float(str(s).replace(",", "").strip()) > 0.0
        except (ValueError, TypeError):
            return False

    # emit
    csv_rows: list[list[str]] = []
    for j in range(n_projects):
        pid = projects[j]["id"]
        pname = clean_latex_name(project_names[j]) if j < len(project_names) else ""
        punit = project_units[j] if j < len(project_units) else unit_fallback
        pcomp = composite_values[j] if j < len(composite_values) else ""
        plabor = labor_values[j] if j < len(labor_values) else ""
        pmaterial_fee = material_fee_values[j] if j < len(material_fee_values) else ""
        pmachine = machine_values[j] if j < len(machine_values) else ""
        pmgmt = management_values[j] if j < len(management_values) else ""
        pprof = profit_values[j] if j < len(profit_values) else ""
        pfee = fee_values[j] if j < len(fee_values) else ""
        pvat = vat_values[j] if j < len(vat_values) else ""
        prisk = risk_fee_values[j] if j < len(risk_fee_values) else ""

        has_labor_below = any(m["category"] == "工" for m in materials)
        has_material_below = any(m["category"] in ("料", "配") for m in materials)
        has_machine_below = any(m["category"] == "机" for m in materials)
        has_unpriced = any(m["category"] == "未计价" for m in materials)
        has_priced_material = any(m["category"] in ("料", "配") for m in materials)

        sub_verifications: list[float] = []
        csv_rows.append(["定", pid, pname, working_content, punit, "", pcomp, "", "", ""])

        # ── 工行（P5 控制 emit，均计入 verify）────────────────────────
        if "labor" in cost_rows:
            # 河南特征（force_emit_labor_fee）：人工费行强制 emit——综合工日无单价，
            #   人工费行值与明细匹配不上，必须写费行（覆盖 three-correspondence 的
            #   "下方有人工明细则隐藏"）。材料/机械费行仍走 P5（明细有单价可匹配）。
            emit_labor = ctx.profile.force_emit_labor_fee
            if not emit_labor:
                if p5 == FeeEmitStrategy.ALWAYS:
                    emit_labor = True
                elif p5 == FeeEmitStrategy.THREE_CORRESPONDENCE and not has_labor_below:
                    emit_labor = True
            if emit_labor:
                v = to_float(plabor)
                sub_verifications.append(v)
                csv_rows.append(["工", "", "人工费", "", "元", plabor, "1.00", _fmt_v(v), "", ""])

        # ── 主材（未计价）────────────────────────────────────────────
        for mat in materials:
            if mat["category"] != "未计价":
                continue
            q = strip_numeric_brackets(mat["quantities"].get(project_cols[j], ""))
            csv_rows.append(["主材", "", clean_latex_name(mat["name"]), "", mat["unit"], q, "", "", "", ""])

        # ── 料-材料费自动行（P6 控制 emit + verify 计入）──────────────
        if p6 == MaterialFeeAutoEmit.SC:
            should = has_unpriced or (not has_priced_material and _has_valid_material_fee(pmaterial_fee))
            if should:
                v = to_float(pmaterial_fee)
                sub_verifications.append(v)
                csv_rows.append(["料", "", "材料费", "", "元", pmaterial_fee, "1.00", _fmt_v(v), "", ""])
        elif p6 in (MaterialFeeAutoEmit.CQ, MaterialFeeAutoEmit.GD, MaterialFeeAutoEmit.HU):
            should = ("material_fee" in cost_rows and not has_material_below
                      and to_float(pmaterial_fee) > 0)
            if p6 in (MaterialFeeAutoEmit.GD, MaterialFeeAutoEmit.HU):
                pid_material_global = (pid_has_material is not None
                                       and any(p["id"] in pid_has_material for p in projects))
                should = should and not pid_material_global
            if should:
                v = to_float(pmaterial_fee)
                # gd/hu：v_str 仅显示，不累加到 verify（避免与料明细重复）
                csv_rows.append(["料", "", "材料费", "", "元", pmaterial_fee, "1.00", _fmt_v(v), "", ""])
        # P6=none（bj）：不 emit

        # ── 材料排序（仅 gd/hu 附项语义启用；sc/cq/bj 按 PDF 行序）────
        if ctx.profile.material_sort:
            def _mat_emit_priority(m: dict) -> int:
                if m["category"] == "未计价":
                    return -1  # 主材单独循环，此处不会 emit
                if m.get("is_appendix"):
                    return 1  # 附项插在料末、机前
                if m["category"] == "机":
                    return 2
                return 0  # 普通料/配
            materials.sort(key=_mat_emit_priority)

        # 其他材料费基数（hu 比例行：Σ 料/配行验证，供 其他材料费 % 行使用）
        other_mat_basis = 0.0
        for mat in materials:
            if mat.get("is_other_material"):
                continue
            if mat["category"] not in ("料", "配"):
                continue
            if mat.get("is_proportion") or mat.get("is_appendix"):
                continue
            if "【机械】" in mat["name"] or "【机】" in mat["name"]:
                continue  # 燃料/动力属机械费，不计入其他材料费基数
            bq = strip_numeric_brackets(mat["quantities"].get(project_cols[j], ""))
            bp = strip_numeric_brackets(mat["price"])
            bqty_f, bprice_f = to_float(bq), to_float(bp)
            if bqty_f == 0.0:
                bv = 0.0
            elif bprice_f == 0.0:
                bv = bqty_f
            else:
                bv = bqty_f * bprice_f
            other_mat_basis += bv

        # ── 普通料/配/机行 ───────────────────────────────────────────
        for mat in materials:
            if mat["category"] == "未计价":
                continue
            q = strip_numeric_brackets(mat["quantities"].get(project_cols[j], ""))
            price = strip_numeric_brackets(mat["price"])
            qty_f, price_f = to_float(q), to_float(price)
            is_proportion = mat.get("is_proportion", False)
            if is_proportion:
                v_str = ""
            elif p5 == FeeEmitStrategy.NONE:
                v_str = ""  # bj 无价类，验证列恒空（对齐 extract_table_bj）
            elif mat["is_other_material"]:
                if mat["unit"] == "%":
                    # hu 2024 比例行：基数 × 百分比 / 100
                    v = other_mat_basis * qty_f / 100.0
                else:
                    v = qty_f
                v_str = _fmt_v(v)
            else:
                if qty_f == 0.0:
                    v = 0.0
                elif price_f == 0.0:
                    v = qty_f
                else:
                    v = qty_f * price_f
                v_str = _fmt_v(v)
            # 附项不参与定行总验证（工程量附加项，非基价构成）；P5=none 无价类也不计
            if not is_proportion and not mat.get("is_appendix") and p5 != FeeEmitStrategy.NONE:
                sub_verifications.append(to_float(v_str))
            row_type = mat.get("category", "料")
            if row_type == "料" and is_proportion:
                row_type = "配"
            csv_rows.append([row_type, "", clean_latex_name(mat["name"]), "",
                             mat["unit"], q, price, v_str, "", ""])

        # ── 机行（P5 控制 emit + verify 计入）────────────────────────
        if "machine" in cost_rows:
            emit_machine = False
            count_verify = True
            if p5 == FeeEmitStrategy.ALWAYS:
                emit_machine = True
            elif p5 == FeeEmitStrategy.THREE_CORRESPONDENCE:
                emit_machine = (not has_machine_below and to_float(pmachine) > 0)
                if emit_machine and p6 in (MaterialFeeAutoEmit.GD, MaterialFeeAutoEmit.HU):
                    pid_machine_global = (pid_has_machine is not None
                                          and any(p["id"] in pid_has_machine for p in projects))
                    emit_machine = emit_machine and not pid_machine_global
                count_verify = False  # gd/hu：机械费 v_str 仅显示，不累加到 verify
            if emit_machine:
                v = to_float(pmachine)
                if count_verify:
                    sub_verifications.append(v)
                csv_rows.append(["机", "", "机械费", "", "元", pmachine, "1.00", _fmt_v(v), "", ""])

        # ── 综行（B4：按 cost_rows 存在 emit，均计入 verify）──────────
        if "management" in cost_rows:
            v = to_float(pmgmt)
            sub_verifications.append(v)
            csv_rows.append(["综", "", "企业管理费", "", "元", "100.00", pmgmt, _fmt_v(v), "", ""])
        if "profit" in cost_rows:
            v = to_float(pprof)
            sub_verifications.append(v)
            csv_rows.append(["综", "", "利润", "", "元", "100.00", pprof, _fmt_v(v), "", ""])
        if "fee" in cost_rows:
            v = to_float(pfee)
            sub_verifications.append(v)
            csv_rows.append(["综", "", "费用", "", "元", "100.00", pfee, _fmt_v(v), "", ""])
        if "vat" in cost_rows:
            v = to_float(pvat)
            sub_verifications.append(v)
            csv_rows.append(["综", "", "增值税", "", "元", "100.00", pvat, _fmt_v(v), "", ""])
        if "risk_fee" in cost_rows:
            v = to_float(prisk)
            sub_verifications.append(v)
            csv_rows.append(["综", "", "一般风险费", "", "元", "100.00", prisk, _fmt_v(v), "", ""])

        # 河南特征（extra_cost_labels）：自定义综行（其他措施费/安文费/规费等）
        #   find_cost_rows 已把这些 label 写进 cost_rows；此处各自独立 emit 综行并计入
        #   verify（河南基价构成含这些费，实测 2-6 基价=人工费+材料费+机械费+其他措施费+
        #   安文费+管理费+利润+规费 全和得上）。baseline 默认只认 管理费/利润/费用/增值税/
        #   一般风险费 五种综行，新费类必须走本字段。
        for label, display in ctx.profile.extra_cost_labels.items():
            if label in cost_rows:
                vals = get_cost_values(label)
                v_raw = vals[j] if j < len(vals) else ""
                v = to_float(v_raw)
                sub_verifications.append(v)
                csv_rows.append(["综", "", display, "", "元", "100.00", v_raw, _fmt_v(v), "", ""])

        # ── 定行验证列回填（从后往前找本项目的定行）──────────────────
        # P5=none（bj 无价类）：verify 恒空，不回填（extract_table_bj 恒 ''）
        if p5 != FeeEmitStrategy.NONE:
            total_v = sum(sub_verifications)
            v_str = _fmt_v(total_v)
            for idx in range(len(csv_rows) - 1, -1, -1):
                if csv_rows[idx][0] == "定":
                    csv_rows[idx][7] = v_str
                    break

        csv_rows.append(["", "", "", "", "", "", "", "", "", ""])

    return csv_rows, None


# ─────────────────────────────────────────────────────────────────────
# 骨架 · 入口（process_md_file / main）
# ─────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────
# 段行 · extract_sections_before（P3 section_system / P4 depth 分派）
# ─────────────────────────────────────────────────────────────────────

SECTION_RE = re.compile(
    r"^\s?##\s+([A-Z](?: ?\.? ?\d+)*)(?:\s+|[　])(.+?)\s*$"  # 标准型（alphanumeric）
)
SECTION_RE_COMPACT = re.compile(
    r"^\s?##\s+([A-Z](?: ?\.? ?\d+)*)([^\s#　\d.].*?)\s*$"  # 紧凑型：ID 后无空格直接跟标题（gd TOC 行尾）
)
SECTION_RE_HEAD_ONLY = re.compile(
    r"^\s?##\s+([A-Z](?: ?\.? ?\d+)*)\s*$"                  # 孤标型：仅 ID，标题在下一行（OCR 断行）
)
SECTION_RE2_LOOSE = re.compile(
    r"^\s?([A-Z](?: ?\.? ?\d+)*)(?:\s+|[　])(.+?)\s*$"  # sc 宽松版：编号可无 .N 段
)
SECTION_RE2_STRICT = re.compile(
    r"^\s?([A-Z](?: ?\.? ?\d+)+)(?:\s+|[　])([^\s\-<>\[({].*?)\s*$"
    # gd 严格版：编号至少 1 个 .N 段；标题不能以 空格/箭头/括号 开头
    #   （拒 mermaid 'A --> B[...]' 等。gd fixture 实测宽松版误识别 mermaid）
)


def extract_sections_before(text: str, ctx: FeatureContext, volume: str = "",
                            cur_chapter=None, cur_section=None, cur_subsection=None,
                            cur_vol: int | None = None):
    """提取段行（P3 section_system 分派）。

    alphanumeric（sc/cq/gd）：SECTION_RE/SECTION_RE2 简单版，P4 depth=1（只 emit 章）。
    chinese4（hu）：4 层中文段行（章/节/小节/小小节，含验证/TOC 去重/stateful）——
      从 hu extract_sections_before 完整移植（v0.4）。
    zh-ce（sh）：3 层 册→章→节（第X册/第X章/N．），cur_vol 跨表持久册号。
    mixed-zh（bj）：TODO(下一步)——bj "第X节"3 层变体，与 chinese4 层级语义不同。
    """
    p3 = ctx.profile.section_system
    strict = ctx.profile.strict_section_downstream
    if p3 == SectionSystem.ALPHANUMERIC:
        return _extract_sections_alphanumeric(text, ctx), cur_vol
    if p3 == SectionSystem.CHINESE4:
        return (_extract_sections_chinese(text, volume, cur_chapter, cur_section,
                                          cur_subsection, strict=strict), cur_vol)
    if p3 == SectionSystem.ZH_CE:
        return _extract_sections_zh_ce(text, cur_chapter, cur_section, cur_vol)
    # mixed-zh（bj）：临时用 chinese4 兜底（下一轮实现 bj "第X节"3 层）
    return (_extract_sections_chinese(text, volume, cur_chapter, cur_section,
                                      cur_subsection, strict=strict), cur_vol)


def _extract_sections_alphanumeric(text: str, ctx: FeatureContext) -> list[tuple[str, str]]:
    """alphanumeric 段行（对齐 gd v0.13.3 4-Pass，sc/cq 兼容）。

    Pass1 标准型 → Pass2 紧凑型 → Pass3 孤标型(peek 下一行标题) → Pass4 纯文本编号行。
    Pass4 正则按 strict_section_re2 开关选择：sc 宽松 / gd 严格（拒 mermaid）。
    """
    re2 = SECTION_RE2_STRICT if ctx.profile.strict_section_re2 else SECTION_RE2_LOOSE
    sections: list[tuple[str, str]] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i].strip()

        # 跳过目录行（节名尾部带 `....19` 页码）
        if TOC_PAGE_TAIL.search(line):
            i += 1
            continue

        matched = False
        m = SECTION_RE.match(line)
        if m:
            sections.append((re.sub(r"\s+", "", m.group(1)), m.group(2).strip()))
            matched = True
        else:
            m = SECTION_RE_COMPACT.match(line)
            if m:
                title = m.group(2).strip()
                if title:  # 避免空字符串误识别
                    sections.append((re.sub(r"\s+", "", m.group(1)), title))
                    matched = True
            if not matched:
                m = SECTION_RE_HEAD_ONLY.match(line)
                if m:
                    # peek 下一行第一个非空非 `##` 行作为 title
                    j = i + 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n:
                        next_line = lines[j].strip()
                        if (next_line and not next_line.startswith("#")
                                and not TOC_PAGE_TAIL.search(next_line)):
                            sections.append((re.sub(r"\s+", "", m.group(1)), next_line))
                            matched = True
                            i = j  # 跳过标题行

        if not matched:
            m = re2.match(line)
            if m:
                sections.append((re.sub(r"\s+", "", m.group(1)), m.group(2).strip()))

        i += 1
    return sections


# ─────────────────────────────────────────────────────────────────────
# chinese4 段行（P3=chinese4，从 hu extract_sections_before 完整移植）
# ─────────────────────────────────────────────────────────────────────

_SECTION_PREFIX = r"^\s*#{0,6}\s*"
SECTION_ZH_CHAPTER_RE = re.compile(_SECTION_PREFIX + r"第\s*([一-龥]{1,3})\s*章\s*(.*?)\s*$")
SECTION_ZH_SECTION_RE = re.compile(_SECTION_PREFIX + r"([一-龥]{1,3})\s*[、,]\s*(.+?)\s*$")
SECTION_ZH_SUBSECTION_RE = re.compile(_SECTION_PREFIX + r"(\d{1,3})\s*[\.．]\s*([^\.]+?)\s*$")
# 小小节: group(1) = 数字（可能为空 → 编号固定 _NUM_MISSING "X"），group(2) = 名称
SECTION_ZH_SUBSUBSECTION_RE = re.compile(
    _SECTION_PREFIX + r"[\(【（]\s*(\d*)\s*[\)】）]\s*(.+?)\s*$"
)
SECTION_ZH_SUBSUBSECTION_EMPTY_RE = re.compile(
    _SECTION_PREFIX + r"[\(【（]\s*[\)】）]\s*(.+?)\s*$"
)
TOC_PAGE_TAIL = re.compile(r"[.…]{2,}\s*\d+\s*$")

WORK_CONTENT_PREFIX = "工作内容"
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM_MISSING = "X"
_WORK_CONTENT_RE = re.compile(r"^\s*工作内容")
_VALIDATE_SUBSECTION_RE = SECTION_ZH_SUBSECTION_RE
_VALIDATE_SUBSUBSECTION_RE = SECTION_ZH_SUBSUBSECTION_RE
_VALIDATE_SUBSUBSECTION_EMPTY_RE = SECTION_ZH_SUBSUBSECTION_EMPTY_RE
_SENTENCE_END_RE = re.compile(r"[。；，、：:．·]$")


def _cn_to_int(s: str) -> int | None:
    """中文数字转 int。一→1, 十→10, 二十一→21。失败返回 None。"""
    if not s:
        return None
    s = s.strip()
    if s == "十":
        return 10
    if s.startswith("十") and len(s) > 1:  # 十一~十九
        rest = s[1:]
        return 10 + _CN_DIGIT[rest] if rest in _CN_DIGIT else None
    if s.endswith("十"):  # 二十/三十/.../九十
        head = s[:-1]
        return _CN_DIGIT[head] * 10 if head in _CN_DIGIT else None
    if "十" in s:  # 二十一~九十九
        head, _, tail = s.partition("十")
        if head in _CN_DIGIT and tail in _CN_DIGIT:
            return _CN_DIGIT[head] * 10 + _CN_DIGIT[tail]
        return None
    return _CN_DIGIT[s] if s in _CN_DIGIT else None


def _next_nonempty_line(lines: list[str], start: int) -> str | None:
    n = len(lines)
    j = start + 1
    while j < n:
        s = lines[j].strip()
        if s:
            return s
        j += 1
    return None


def _validate_section_downstream(lines, start, allowed_next) -> bool:
    """段行验证：下一个非空行是否属于 allowed_next 正则之一。"""
    nxt = _next_nonempty_line(lines, start)
    if nxt is None:
        return False
    for pat in allowed_next:
        if pat.match(nxt):
            return True
    return False


def _has_work_content_within(lines, start, max_nonempty=5, strict=False) -> bool:
    """节验证：从 start+1 往下扫最多 max_nonempty 个非空行，有"工作内容"开头 → True。

    strict=True（profile `strict_section_downstream`，yn）：扫描中若先碰到**同级/更高级
    段行**（章/节）→ 立即 False。yn 工程量计算规则条目是"一、…/十三、…"格式，下方
    5 行内常混入"十四、""十五、"等规则条目 + 定额的"工作内容"（prefix#3 实测），不
    提前终止会误 emit 规则条目、并污染 cur_chapter（"十三、"被当节后，"一、绿地整理"
    错编码为 4.13.1）。真实节标题（"## 一、绿地整理"）下方只允许小节/小小节/工作内容。
    strict=False（hu）：不检查同级节——hu 说明条目（"十四、运输车辆…"等）原版 emit
    为段行，保持宽松行为。
    """
    count = 0
    n = len(lines)
    j = start + 1
    while j < n:
        s = lines[j].strip()
        if s:
            if strict and (SECTION_ZH_CHAPTER_RE.match(s) or SECTION_ZH_SECTION_RE.match(s)):
                return False  # 先碰到同级/更高级段行 → 该候选不是直接控制后续定额的节标题
            count += 1
            if _WORK_CONTENT_RE.match(s):
                return True
            if count >= max_nonempty:
                return False
        j += 1
    return False


def _is_plausible_subsection_title(s: str) -> bool:
    """是否像"被 OCR 吃掉编号的小节标题"（保守，避免误认正文/说明）。"""
    if not (0 < len(s) <= 30):
        return False
    if _SENTENCE_END_RE.search(s):
        return False
    if "…" in s or "<table" in s or "计量单位" in s:
        return False
    if s.startswith("工作内容"):
        return False
    for pat in (SECTION_ZH_CHAPTER_RE, SECTION_ZH_SECTION_RE,
                SECTION_ZH_SUBSECTION_RE, SECTION_ZH_SUBSUBSECTION_RE,
                SECTION_ZH_SUBSUBSECTION_EMPTY_RE):
        if pat.match(s):
            return False
    return True


def _find_eaten_subsection_title(lines, start) -> str | None:
    j = start - 1
    while j >= 0:
        s = lines[j].strip()
        if s:
            s = re.sub(_SECTION_PREFIX, "", s)  # 去 # 前缀，与 line_disp 一致
            return s if _is_plausible_subsection_title(s) else None
        j -= 1
    return None


def _extract_sections_chinese(text, volume, cur_chapter, cur_section, cur_subsection, *,
                              strict=False):
    """chinese4 段行识别（hu v0.14.3 完整移植）：
    4 种标记 第X章→volume.X / 一、→volume.X.Y / 1.→volume.X.Y.Z / (1)→volume.X.Y.Z.W，
    含章节验证（节下方 5 行内工作内容、小节/小小节下一非空行校验）、TOC 去重、stateful 层级。

    strict=profile `strict_section_downstream`（yn）：节验证遇同级节提前终止（拒绝规则条目）。
    """
    sections: list[tuple[str, str]] = []
    lines = text.splitlines()
    n = len(lines)

    def _classify_line(line: str) -> int:
        if SECTION_ZH_CHAPTER_RE.match(line):
            return 1
        if SECTION_ZH_SECTION_RE.match(line):
            return 2
        if SECTION_ZH_SUBSECTION_RE.match(line):
            return 3
        if SECTION_ZH_SUBSUBSECTION_RE.match(line) or SECTION_ZH_SUBSUBSECTION_EMPTY_RE.match(line):
            return 4
        return 0

    line_level: list[int] = [_classify_line(lines[k].strip()) for k in range(n)]

    def _is_chapter_in_toc(idx: int) -> bool:
        """章标题是否为 TOC 目录条目：下一个章前无 <table> → TOC skip。"""
        next_chapter_idx = n
        for j in range(idx + 1, n):
            if line_level[j] == 1:
                next_chapter_idx = j
                break
        if next_chapter_idx == n:
            return False  # 最后一个章 → 表在 prefix 外，必然 emit
        for j in range(idx + 1, next_chapter_idx):
            if "<table" in lines[j]:
                return False
        return True

    i = 0
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # 去 markdown # 前缀，保留数字/括号标记（供人工核对原始编号）
        line_disp = re.sub(_SECTION_PREFIX, "", line)

        if TOC_PAGE_TAIL.search(line):
            i += 1
            continue

        # 1. 第X章（最高优先级，重置下游层级）
        m = SECTION_ZH_CHAPTER_RE.match(line)
        if m:
            cn_num, title = m.group(1), m.group(2).strip()
            n_int = _cn_to_int(cn_num)
            if n_int is not None and title and not _is_chapter_in_toc(i):
                sections.append((f"{volume}.{n_int}", title))
                cur_chapter = n_int
                cur_section = None
                cur_subsection = None
            i += 1
            continue

        # 2. 一、节名称（下方 5 个非空行内有"工作内容"才 emit）
        m = SECTION_ZH_SECTION_RE.match(line)
        if m:
            cn_num, title = m.group(1), line_disp
            n_int = _cn_to_int(cn_num)
            if n_int is not None and title and _has_work_content_within(lines, i, strict=strict):
                if cur_chapter is None:
                    sections.append((f"{volume}.{n_int}", title))
                    cur_chapter = n_int
                else:
                    sections.append((f"{volume}.{cur_chapter}.{n_int}", title))
                    cur_section = n_int
                    cur_subsection = None
            i += 1
            continue

        # 3. 1.小节名称（下一非空行是小节/小小节/工作内容；无节上下文拒绝）
        m = SECTION_ZH_SUBSECTION_RE.match(line)
        if m:
            num, title = m.group(1), line_disp
            if num.isdigit() and title and cur_section is not None:
                if _validate_section_downstream(
                    lines, i,
                    allowed_next=(_VALIDATE_SUBSECTION_RE,
                                  _VALIDATE_SUBSUBSECTION_RE,
                                  _VALIDATE_SUBSUBSECTION_EMPTY_RE,
                                  _WORK_CONTENT_RE),
                ):
                    sections.append((f"{volume}.{cur_chapter}.{cur_section}.{int(num)}", title))
                    cur_subsection = int(num)
            i += 1
            continue

        # 4. (1)小小节名称 / ()小小节名称（下一非空行必须是工作内容）
        m = SECTION_ZH_SUBSUBSECTION_RE.match(line)
        if m:
            num_str, title = m.group(1), line_disp
            if title:
                if _validate_section_downstream(lines, i, allowed_next=(_WORK_CONTENT_RE,)):
                    n_int = _NUM_MISSING if (num_str == "" or not num_str.isdigit()) else int(num_str)
                    if cur_subsection is None:
                        # 方案 A：向上认被吃小节裸行
                        if cur_section is None:
                            i += 1
                            continue
                        eaten = _find_eaten_subsection_title(lines, i)
                        if eaten is None:
                            i += 1
                            continue
                        sections.append(
                            (f"{volume}.{cur_chapter}.{cur_section}.{_NUM_MISSING}", eaten))
                        cur_subsection = _NUM_MISSING
                    n_disp = _NUM_MISSING if cur_subsection == _NUM_MISSING else n_int
                    code = f"{volume}.{cur_chapter}.{cur_section}.{cur_subsection}.{n_disp}"
                    sections.append((code, title))
            i += 1
            continue

        # 5. 被吃小节裸行直接跟定额（方案 A 补充）
        if cur_section is not None and _is_plausible_subsection_title(line_disp):
            if _validate_section_downstream(lines, i, allowed_next=(_WORK_CONTENT_RE,)):
                sections.append(
                    (f"{volume}.{cur_chapter}.{cur_section}.{_NUM_MISSING}", line_disp))
                cur_subsection = _NUM_MISSING

        i += 1

    return sections


def _extract_sections_zh_ce(text, cur_chapter, cur_section, cur_vol):
    """上海（P3=zh-ce）段行：3 层 第X册 → 第X章 → N．节。

    - 册：`第一册`/`第 一 册` → cur_vol 更新（跨表持久，进程内返回给 process_md_file）
    - 章：`第一章` → {vol}.{章}
    - 节：`1．`/`1.` → {vol}.{章}.{节}（数字节直接挂在章下，跳过 chinese4 的 一、 级）
    - 跳过：TOC（点线页码尾）、一、二、规则条目（工程量计算规则/册说明正文）、重复节头
    - 返回 (sections, cur_vol)
    """
    sections: list[tuple[str, str]] = []
    lines = text.splitlines()
    vol = cur_vol or 0
    chapter = cur_chapter
    section = cur_section
    seen: set[str] = set()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if TOC_PAGE_TAIL.search(line):
            continue
        # TOC 条目：点线页码（OCR 可能拆行导致 TOC_PAGE_TAIL 漏网）→ 含 2+ 连续点即跳
        if re.search(r"[.…]{2,}", line):
            continue
        disp = re.sub(_SECTION_PREFIX, "", line).strip()

        # 册：第X册 / 第 一 册（TOC 带页码行已被 TOC_PAGE_TAIL 跳过）
        m = re.match(r"^第\s*([一-龥]{1,3})\s*册\b", disp)
        if m:
            v = _cn_to_int(m.group(1))
            if v:
                vol = v
            continue

        # 章：第一章（含 第 一 章 空格变体）
        m = SECTION_ZH_CHAPTER_RE.match(line)
        if m:
            n_int = _cn_to_int(m.group(1))
            title = m.group(2).strip()
            if n_int is not None and title:
                code = f"{vol}.{n_int}"
                if code not in seen:
                    seen.add(code)
                    sections.append((code, title))
                chapter = n_int
                section = None
            continue

        # 节：N．一般土方（数字节，直接挂章下）
        m = SECTION_ZH_SUBSECTION_RE.match(line)
        if m and m.group(1).isdigit():
            num = int(m.group(1))
            title = disp
            if chapter is not None and title:
                code = f"{vol}.{chapter}.{num}"
                if code not in seen:
                    seen.add(code)
                    sections.append((code, title))
                section = num
            continue
        # 一、二、三、 → 规则条目/说明正文，跳过（上海节用数字标记）

    return sections, vol


# ─────────────────────────────────────────────────────────────────────
# 入口 · process_md_file（P7 跨页 join + 段行 + 预扫描）
# ─────────────────────────────────────────────────────────────────────


def _extract_work_content(prefix: str) -> str:
    """提取"工作内容：xxx"（支持多行，结束于"单位："或 <table）。"""
    wc_match = None
    for wc_m in re.finditer(r"工作内容[：:]\s*", prefix):
        wc_match = wc_m
    if wc_match:
        rest = prefix[wc_match.end():]
        end_match = re.search(r"单位[：:]|<table", rest)
        wc_text = rest[:end_match.start()] if end_match else rest
        return re.sub(r"\s+", " ", wc_text.strip())
    return ""


def _extract_unit_fallback(prefix: str) -> str:
    """提取单位 fallback（"单位：xx" 行，取最后一个）。"""
    unit_matches = list(re.finditer(r"单位[：:]\s*([^\n]+)", prefix))
    unit_text = unit_matches[-1].group(1).strip() if unit_matches else ""
    return normalize_unit(unit_text)


def _detect_volume(table_matches, ctx: FeatureContext) -> str:
    """P3=chinese4/mixed 时提取册号（hu 从首表定额编号首字母；bj 固定 "BJ" 占位）。"""
    if ctx.profile.section_system == SectionSystem.MIXED_ZH:
        return "BJ"
    for m in table_matches:
        grid, total_cols, raw_rows, cell_cols = parse_table(m.group(0))
        if not grid or total_cols < 4 or not raw_rows:
            continue
        projects = find_projects(grid, total_cols, ctx)
        if projects:
            return projects[0]["id"][0]
    return ""


def process_md_file(md_path: Path, profile: FeatureFlagProfile,
                    ctx: FeatureContext | None = None):
    """MD → (all_rows, issues) 主入口。替换各省 process_md_file。

    - profile（P flag）由档案元数据（省+年份）自动匹配（SPEC §3.3）
    - ctx（B 开关）由 features.py 检测或人工确认
    - P7 跨页：extract_table 检测续表返回 continuation meta → 本函数 join 回主表
      （gd/hu 实际都是 join；sc/cq/bj 无跨页 seen_pids 保持空）
    """
    if ctx is None:
        ctx = FeatureContext(profile=profile)
    content = md_path.read_text(encoding="utf-8")
    table_matches = list(re.finditer(r"<table[^>]*>.*?</table>", content, flags=re.S))

    p3 = profile.section_system
    p7 = profile.cross_page_strategy
    p6 = profile.material_fee_auto_emit

    # volume（chinese4/mixed 段行需要；alphanumeric 不需要）
    volume = _detect_volume(table_matches, ctx) if p3 != SectionSystem.ALPHANUMERIC else ""

    # 预扫描 pid_has_machine/material（仅 P6 gd/hu style 需要，用于费自动行全局隐藏）
    pid_has_machine: set[str] = set()
    pid_has_material: set[str] = set()
    if p6 in (MaterialFeeAutoEmit.GD, MaterialFeeAutoEmit.HU):
        for m in table_matches:
            grid, total_cols, raw_rows, cell_cols = parse_table(m.group(0))
            if not grid or total_cols < 4 or not raw_rows:
                continue
            projects_scan = find_projects(grid, total_cols, ctx)
            if not projects_scan:
                continue
            for ri in range(len(grid)):
                col0 = grid[ri][0].strip()
                if col0 in ("机", "机具", "机 具"):
                    for p in projects_scan:
                        pid_has_machine.add(p["id"])
                elif col0 in ("材", "料", "材料", "材 料"):
                    for p in projects_scan:
                        pid_has_material.add(p["id"])

    all_rows: list[list[str]] = []
    issues: list[dict] = []
    last_section_id = ""
    prev_end = 0
    seen_pids: set[str] = set()          # 跨表已 emit 的 PID（续表检测用）
    pid_blank_idx: dict[str, int] = {}   # PID → 主表 emit 段的 [blank] 行索引（续表插入点）
    # chinese4 stateful 段行（跨 table prefix 维护，hu v3 fix）
    cur_chapter: int | None = None
    cur_section: int | None = None
    cur_subsection: int | str | None = None
    # zh-ce（sh）：册号跨表持久
    cur_vol: int | None = None
    seen_sections: set[str] = set()  # 段行 sec_id 全局去重（OCR 重复节头）

    for m in table_matches:
        table_html = m.group(0)
        prefix = content[prev_end:m.start()]

        # 段行（每个 table 的 prefix 单独 emit，段行出现在对应 table 前面）
        secs, cur_vol = extract_sections_before(prefix, ctx, volume=volume,
                                                cur_chapter=cur_chapter,
                                                cur_section=cur_section,
                                                cur_subsection=cur_subsection,
                                                cur_vol=cur_vol)
        for sec_id, sec_name in secs:
            # 段行去重（profile `dedup_section_ids`，上海 zh-ce）：OCR 每页重复节头
            #   `## 1． 一般土方` → 段行重复 4-6 遍。仅当该省段行 code 全局唯一才可开
            #   （zh-ce 册.章.节天然唯一；sc/hu 段行 code 跨卷合法重复，开去重会误删）。
            if ctx.profile.dedup_section_ids:
                if sec_id in seen_sections:
                    continue
                seen_sections.add(sec_id)
            # gd 特有：跳过目录条目（名称含点线页码 "·····99"）。
            #   gd 原版用 toc_titles 两阶段更精确（TOC 收集 + body 去重 + carry-fwd）；
            #   本统一版用宽松启发式（名称含 2+ 连续点）。仅 gd 开启——sc 段行名
            #   可含省略号（"砌体拆除……(9)"），不能过滤。
            if (ctx.profile.filter_toc_dotted
                    and ("…" in sec_name or re.search(r"\.{2,}", sec_name))):
                continue
            all_rows.append(["段", sec_id, clean_latex_name(sec_name), "", "", "", "", "", "", ""])
            last_section_id = sec_id
            # 同步累加 cur_chapter/section/subsection（用于下个段行兜底）
            parts = sec_id.split(".")
            if len(parts) == 2 and sec_id != volume:  # volume.X = 章
                cur_chapter = int(parts[1])
                cur_section = None
                cur_subsection = None
            elif len(parts) == 3:  # volume.X.Y = 节
                cur_section = int(parts[2])
                cur_subsection = None
            elif len(parts) == 4:  # volume.X.Y.Z = 小节
                cur_subsection = parts[3] if parts[3].isdigit() else _NUM_MISSING

        wc_text = _extract_work_content(prefix)
        unit_fallback = _extract_unit_fallback(prefix)

        rows, meta = extract_table(table_html, wc_text, unit_fallback, ctx,
                                   section_id=last_section_id, seen_pids=seen_pids,
                                   pid_has_machine=pid_has_machine,
                                   pid_has_material=pid_has_material)

        # ── P7 跨页 join（gd/hu continuation 场景）────────────────────
        if meta is not None and meta.get("continuation"):
            verify_deltas = meta.get("verify_deltas", {})
            pids = meta.get("pids", [])
            cont_by_pid: dict[str, list[list[str]]] = {pid: [] for pid in pids}
            for r in rows:
                pid = r[1]
                if pid in cont_by_pid:
                    cont_by_pid[pid].append(r)
            for pid in reversed(pids):
                insert_rows = cont_by_pid.get(pid, [])
                if not insert_rows:
                    continue
                if pid not in pid_blank_idx:
                    # 主表没 emit 过 → append 末尾（清空 PID）
                    for r in insert_rows:
                        r[1] = ""
                        all_rows.append(r)
                    continue
                blank_idx = pid_blank_idx[pid]
                # 插入位置 = 该 PID 主表段 [综 企业管理费] 之前（兜底 [blank] 行）
                insert_idx = blank_idx
                for i in range(blank_idx - 1, -1, -1):
                    if all_rows[i][0] == "综" and all_rows[i][2] == "企业管理费":
                        insert_idx = i
                        break
                all_rows[insert_idx:insert_idx] = insert_rows
                for r in insert_rows:
                    r[1] = ""
                inserted = len(insert_rows)
                for k, v in pid_blank_idx.items():
                    if v >= insert_idx:
                        pid_blank_idx[k] = v + inserted
                delta = verify_deltas.get(pid, 0.0)
                if delta:
                    for idx in range(insert_idx - 1, -1, -1):
                        row = all_rows[idx]
                        if row[0] == "定" and row[1] == pid:
                            row[7] = _fmt_v(to_float(row[7]) + delta)
                            break
        elif meta is not None and "reason" in meta:
            issues.append(meta)
        else:
            base_len = len(all_rows)
            all_rows.extend(rows)
            # 记录新 emit 的每个 PID 的 [blank] 行索引（跨页插入锚点）
            for i in range(len(rows) - 1, -1, -1):
                if rows[i][0] == "":
                    for j in range(i - 1, -1, -1):
                        if rows[j][0] == "定" and rows[j][1]:
                            pid_blank_idx[rows[j][1]] = base_len + i
                            break
            # 更新 seen_pids（续表检测用）
            for r in rows:
                if r[0] == "定" and r[1]:
                    seen_pids.add(r[1])

        prev_end = m.end()

    return all_rows, issues


def main(argv=None):
    import sys

    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print("用法: baseline.py <md路径> <profile名>")
        print("  profile 预置: sc-2018 cq-2018 gd-2018 hu-2018 bj-2021")
        return 2
    from profile_schema import get_preset_profile

    profile = get_preset_profile(argv[1])
    ctx = FeatureContext(profile=profile)
    rows = process_md_file(Path(argv[0]), profile, ctx)
    print(f"[baseline] {argv[0]} → {len(rows)} rows (profile={profile.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
