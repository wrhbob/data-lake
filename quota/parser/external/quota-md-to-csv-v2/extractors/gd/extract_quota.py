#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_quota_cq.py — 重庆定额 Markdown → 10 列 CSV

按 SPEC.md (quota-md-to-csv) 实现，针对重庆 2018 版定额调整：
  输入: 单份 .md 文件（含 HTML table）
  输出: 10 列 CSV (类型|项目编码|名称|项目特征|计量单位|消耗量|基价/单价|验证|标准换算|标准换算来源)

与四川版本的关键差异（详细见 SPEC §11）：
  1. 识别 col0="人工" → category="工"（重庆 col0 含"人工"分组，四川没有）
  2. 上方"施工机具使用费"(老"机械费") → 永远不 emit 行（不论下方有没有机）
  3. 上方"人工费" → 下方有"人工"分组时不 emit（避免冗余）
  4. 上方"一般风险费" → emit "综" 行
  5. 重庆 MD 不含"未计价"标签（按用户要求；如有则按四川逻辑处理）

支持场景：
  - 普通料/配/机行（完整保留）
  - 未计价主材（识别 col0="未计价"，emit 类型=主材 + 1 个料-材料费自动行）
  - 隐含材料费（material_header 之下无任何 料/配/未计价 行，但其中-材料费(元)>0，
    仍 emit 1 个料-材料费自动行，让验证列加和 = 综合基价）
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

# ---------- 常量 ----------
# 段标题解析 (v0.13.3 自适应延长 + 容忍单空格)
# - 编号深度自适应: A.1 / A.1.1 / A.1.1.1 / A.1.1.1.1 都支持 (用户 2026-08-05 要求)
# - 编号内单空格容忍: "A .1.5" / "A.1 .5" / "A.1. 5" 都识别为 A.1.5 (OCR 抖动)
# - 标题 3 种形式:
#   1. SECTION_RE:       ## A.1.5 混凝土工程            (ID + 单/全角空格 + 标题)
#   2. SECTION_RE_COMPACT: ## A.1.5混凝土工程            (紧凑,无空格分隔,常见于目录行尾)
#   3. SECTION_RE_HEAD_ONLY: ## A.1.5                    (仅 ID, 标题在下一行; OCR 章节首页断行)
SECTION_RE = re.compile(
    r"^\s?##\s+([A-Z](?: ?\.? ?\d+)*)(?:\s+|[　])(.+?)\s*$"  # 二级标题（行首/编号内最多 1 空格）
)
SECTION_RE_COMPACT = re.compile(
    r"^\s?##\s+([A-Z](?: ?\.? ?\d+)*)([^\s#　\d.].*?)\s*$"  # 紧凑型: ID 后无空格直接跟标题 (常见 TOC)
)
SECTION_RE_HEAD_ONLY = re.compile(
    r"^\s?##\s+([A-Z](?: ?\.? ?\d+)*)\s*$"                  # 仅 ID, 标题在下一行 (章节首页 OCR 断行)
)
SECTION_RE2 = re.compile(
    r"^\s?([A-Z](?: ?\.? ?\d+)*)(?:\s+|[　])([^\s\-<>\[({].*?)\s*$"  # 纯文本编号行（行首/编号内最多 1 空格；标题不能以箭头/括号开头，避免 mermaid 'A --> B[...]' 误识别）
)
# gd 项目编码: {字母}{章}-{节}-{子}
# - 字母是 PDF 分册代号 (A=房屋建筑与装饰 / B=装饰 / C=机械设备安装 / ...)
#   v0.13 验证用的是 C 分册, 现扩展到任意字母 (A 分册房屋上册 v0.13.2 验证通过)
# - 容忍单空格 (OCR 偶尔在字母/数字间留空格, 如 "A 1-2-1" / "A1 -2-1")
# - m.group(0) 取完整串 (下游用 pid 作 key, 见 _discover_project_ids L239)
PROJECT_ID_RE = re.compile(r"^([A-Z])\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)$")

# TOC 行: 末尾是 ASCII 点 + 页码（OCR 把目录的 `....19` 写在节名尾部）
# 例: `C.1.1.1 桥式起重机.....19`、`C.1.4.4 小型杂货电梯.....143`
# 跳掉, 否则 SECTION_RE2 会把目录行也当成章节
TOC_PAGE_TAIL = re.compile(r"[.…]{2,}\s*\d+\s*$")

# LaTeX → Unicode 映射（长命令优先）
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
    ("ldots", "…"), ("dots", "…"), ("cdots", "…"),
    ("degree", "°"), ("gt", ">"), ("lt", "<"),
]


# ---------- 文本归一化 ----------
def normalize_unit(s: str) -> str:
    """按 SPEC §5.1 归一单位."""
    if not s:
        return ""
    s = s.strip()
    if re.fullmatch(r"[\-−–—]+", s):
        return ""
    # 先做一轮 LaTeX 清洗简化
    s_clean = re.sub(r"\\mathrm\s*\{\s*m\s*\}", "m", s)
    s_clean = re.sub(r"\{\s*\\mathrm\s*\{\s*m\s*\}\s*\}", "m", s_clean)
    s_clean = re.sub(r"\{+\s*m\s*\}+", "m", s_clean)  # {{m}} → m
    # 不再删除 $ 前的 100/10，保留数字前缀
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
    """按 SPEC §5.2 清洗名称文本中的 LaTeX."""
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
    """按 SPEC §5.3 去除数值括号."""
    if not s:
        return ""
    s = s.strip()
    m = re.match(r"^[（(]([\d,\.\s]+)[)）]$", s)
    if m:
        return m.group(1).strip()
    return s


def to_float(s: str) -> float:
    """安全转 float，失败返回 0.0."""
    if not s:
        return 0.0
    s = strip_numeric_brackets(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


# ---------- HTML 解析 ----------
def parse_table(html_text: str):
    """解析 <table>, 返回 (grid, total_cols, raw_rows, cell_cols)."""
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


def find_projects(grid, total_cols: int) -> list[dict]:
    seen: set[str] = set()
    projects: list[dict] = []
    for r, row in enumerate(grid):
        for c in range(total_cols):
            txt = row[c].strip()
            m = PROJECT_ID_RE.fullmatch(txt)
            if m:
                # gd 编码 {字母}{章}-{节}-{子} 用 m.group(0) 取完整串
                # (PROJECT_ID_RE 含 4 个 capture group: 字母/章/节/子, 不能只取 group(1))
                pid = m.group(0)
                if pid not in seen:
                    seen.add(pid)
                    projects.append({"id": pid, "row": r, "col": c})
    return projects


def find_section_rows(grid, total_cols: int) -> dict:
    sections: dict = {}
    for r, row in enumerate(grid):
        col0 = row[0].strip()
        full_text = " ".join(c.strip() for c in row if c.strip())

        if col0 in ("项 目", "项目") and "project_parent" not in sections:
            if 4 < total_cols and PROJECT_ID_RE.fullmatch(grid[r][4].strip()):
                continue
            sections["project_parent"] = r

        if "composite" not in sections:
            # cq: 增加 "综合单价" 识别（重庆 2018 版用"综合单价"，四川用"综合基价"）
            # gd: 广东 2018 用无空格 "基价(元)" / "基价（元）"（OCR 误把 "基价" 排整齐）
            #     没有 "综合" 前缀；这一行是表中"基价(元)"独立行，不在"其中"块下
            has_label = ("综合基价" in full_text or
                         "综合单价" in full_text or
                         "基 价 (元)" in full_text or
                         "基 价（元）" in full_text or
                         "基价(元)" in full_text or
                         "基价（元）" in full_text or
                         "综 合 基 价" in full_text)
            has_value = any(
                re.match(r"^[\d,.\s ]+$", c.strip())
                for c in row[4:] if c.strip()
            )
            if has_label and has_value:
                sections["composite"] = r

        if col0 in ("其 中", "其中") and "labor_start" not in sections:
            sections["labor_start"] = r

        if "计量单位" not in sections:
            if "计量单位" in full_text or "计 量 单 位" in full_text:
                sections["计量单位"] = r

        if "material_header" not in sections:
            # cq: 重庆表头多了"编码"列，结构为 [col0=空, "编码", "名称", "单位", "单价(元)", "消耗量"]
            # 兼容：col0 是 "名 称" / "名称"（四川型），或 col0 空但 col2 = "名称"（重庆型）
            # gd: 广东 2018 表头多了"分类"列 (col0="分类" / col1="编码" / col2="名称" / col3="单位" / col4="单价(元)" / col5+="消耗量")
            col2 = row[2].strip() if len(row) > 2 else ""
            if (col0.startswith("名 称") or col0.startswith("名称")
                    or (col0 == "" and col2 == "名称")
                    or (col0 == "分类" and col2 == "名称")):
                sections["material_header"] = r

    return sections


def find_cost_rows(grid, raw_rows=None) -> dict:
    """识别 人工费 / 材料费 / 施工机具使用费(机械费) / 管理费 / 利润 / 一般风险费 行；
    兼容 OCR 切分（去全部空白后匹配）.

    与四川版本的差异：
      - "机械费" 或 "施工机具使用费" 都识别为 machine（重庆 2018 版用新命名）
      - 增加 "一般风险费" → risk_fee 识别（重庆独有）

    v0.13.2 新增：multi-label 单 cell 拆分
    - 广东 2018 PDF A 分册"其中"块的 4 个 label (人工费/材料费/机具费/管理费)
      经常被 OCR 合并到 1 个 <td rowspan="4" colspan="4"> 细胞
      (例 A1-1-119: "人工费(元)材料费(元)机具费(元)管理费(元)").
    - 原逻辑把 4 个 label 全指到 row=r (即 row4), get_cost_values 读到的值是人工费
      → 4 个 cost 行重复, management=人工费值 (典型错误).
    - 修复: 检测单 cell 含 ≥2 label + cell.rowspan ≥ label 数, 按 LABEL_ORDER 顺序
      拆到 r, r+1, r+2, ... (顺序: 人工费 → 材料费 → 机具费 → 管理费, 与 cell 文本顺序一致).
    - C 分册每个 label 独立 <td>, 单 cell 仅 1 label, 不触发 multi-label pass,
      Pass 2 fallback 行为不变 (C 分册回归 39732 行 不变).
    """
    LABEL_ORDER = [("labor", "人工费"), ("material_fee", "材料费"),
                   ("machine", "机具费"), ("management", "管理费")]

    cost_rows: dict = {}

    # ── Pass 1 · multi-label 单 cell 拆分 ───────────────────────────────
    # 广东 A 分册场景: OCR 把"人工费(元)材料费(元)机具费(元)管理费(元)"塞进 1 个
    # rowspan=4 colspan=4 cell, 该 cell 所在行 r 的 grid 里 r..r+3 行 col1..col4
    # 都被 forward-fill 成同一段文字. 若不拆分, 4 个 cost_row 都指 r,
    # get_cost_values 都读到 r 行的"人工费"列值 → 输出重复.
    # 修复: 检测到此场景, 按 LABEL_ORDER 顺序把 4 label 分配到 r, r+1, r+2, r+3.
    if raw_rows is not None:
        for r, cells in enumerate(raw_rows):
            for cell in cells:
                cell_text_norm = re.sub(r"\s+", "", cell["text"])
                matched = [(k, kw) for k, kw in LABEL_ORDER if kw in cell_text_norm]
                if len(matched) >= 2 and cell["rowspan"] >= len(matched):
                    for i, (k, _kw) in enumerate(matched):
                        if k not in cost_rows:
                            cost_rows[k] = r + i
                    break  # 一行只处理一次 multi-label cell

    # ── Pass 2 · 原 fallback (label 在各自独立 row 的场景, 含 C 分册回归) ──
    for r, row in enumerate(grid):
        full = " ".join(c.strip() for c in row if c.strip())
        full_norm = re.sub(r"\s+", "", full)
        if "labor" not in cost_rows and "人工费" in full_norm:
            cost_rows["labor"] = r
        # v3 新增（Bug2 修复）：识别"材料费"行。仅取值用于"料-材料费"自动行的验证列；
        # v2 现状"不 emit 料 材料费(其中)"行 的规则仍保留——v3 不输出这一行。
        if ("material_fee" not in cost_rows
                and "材料费" in full_norm
                and "人工费" not in full_norm
                and "机具费" not in full_norm
                and "管理费" not in full_norm):
            cost_rows["material_fee"] = r
        # gd: 机器识别 "机具费" (cq 是 "机械费/施工机具使用费", 广东 2018 用新命名"机具费")
        if "machine" not in cost_rows and "机具费" in full_norm:
            cost_rows["machine"] = r
        if "management" not in cost_rows and "管理费" in full_norm:
            cost_rows["management"] = r
        # gd 不识别 "利润" 字段 (cq 有, 但广东 2018 综合基价不含利润项)
        # gd 不识别 "一般风险费" 字段 (cq 有, 但广东 PDF 0 次出现)
    return cost_rows


def find_value_in_row(row, start_col: int = 1) -> str:
    for c in range(start_col, len(row)):
        v = row[c].strip()
        if v and re.match(r"^[\d,.\s ]+$", v):
            return v
    return ""


def _grid_to_html(grid: list[list[str]]) -> str:
    """grid (list[list[str]]) → 简单 HTML 串. 仅用于复合表拆分后子表重建.

    不保留 rowspan/colspan (子表内无跨 sub-grid 的 rowspan, 安全).
    """
    rows = []
    for row in grid:
        cells = [f"<td>{(c or '').strip()}</td>" for c in row]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


# ---------- 料行解析 ----------
def parse_material_row(cells: list[dict], start_cols: list[int], project_cols: list[int],
                       *, grid: list[list[str]] | None = None, grid_row_idx: int | None = None) -> dict | None:
    cell_positions = []
    for cell, sc in zip(cells, start_cols):
        cell_positions.append({
            "text": cell["text"],
            "start_col": sc,
            "end_col": sc + cell["colspan"] - 1,
        })

    if not any(c["text"].strip() for c in cell_positions):
        return None

    # v3 修复（Bug1）：当 raw_row[0].start_col != 0 时，说明 col0 被前一行 rowspan
    # 继承（如"未计价"跨多行的场景）。从 grid 中读取实际 col0 文本并注入到
    # cell_positions 最前面，确保 col0 分组标签（"未计价"/"材料"/"机械"）能正确识别。
    if (grid is not None and grid_row_idx is not None
            and cell_positions and cell_positions[0]["start_col"] != 0
            and grid_row_idx < len(grid)):
        col0_text = grid[grid_row_idx][0].strip()
        if col0_text:
            cell_positions.insert(0, {"text": col0_text, "start_col": 0, "end_col": 0})

    first_cell = next(c for c in cell_positions if c["text"].strip())
    raw_name = first_cell["text"].strip()

    category = "料"
    # cq 特有：col0="人工" 单独作为分组标签（重庆 2018 版），列结构：
    #   col0=分类(人工) col1=编码 col2=名字 col3=单位 col4=单价 col5=消耗量
    # 与料/机行的列结构（col0=名字 col1=单位 col2=单价 col3=消耗量）不同，
    # 直接走专用解析，不走后面 unit_cell/price_cell 通用逻辑。
    if raw_name == "人工":
        nxt = sorted(
            (c for c in cell_positions
             if c["start_col"] > first_cell["end_col"] and c["text"].strip()),
            key=lambda x: x["start_col"],
        )
        # 期望至少 4 个 cell: 编码, 名字, 单位, 单价
        if len(nxt) < 4:
            return None
        name = nxt[1]["text"].strip()                              # col2 = 名字
        unit = normalize_unit(nxt[2]["text"].strip())             # col3 = 单位
        price = strip_numeric_brackets(nxt[3]["text"].strip())    # col4 = 单价
        # 消耗量：从 col5+ 起按 project_cols 取
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
            "name": name,
            "unit": unit,
            "price": price,
            "quantities": quantities,
            "is_other_material": False,
            "category": "工",
            "is_proportion": False,
        }
    # v3 新增："未计价"作为 col0 分组标签（与"材料"/"机械"同级），识别后 category="未计价"
    # gd 2018: "机具" 也是分组标签 (广东 PDF 用「机具」二字, cq 只有「机」「机械」)
    elif raw_name in ("材", "料", "机", "机具", "材 料", "材料", "机 具", "机 械", "料 料", "机械",
                    "未计价", "未 计 价", "未 计价", "未计 价"):
        raw_norm = raw_name.replace(" ", "")
        if raw_norm == "未计价":
            category = "未计价"
        else:
            category = "机" if "机" in raw_norm else "料"
        nxt = sorted(
            (c for c in cell_positions
             if c["start_col"] > first_cell["end_col"] and c["text"].strip()),
            key=lambda x: x["start_col"],
        )
        # gd 2018: OCR 用 <td rowspan="N">材料</td> 跨多行, 但实际表中段切换分类
        # (材料 → 机具) 时, 后段行 col0 仍继承"材料"分组标签, 会被误归类成"料"。
        # 识别: 编码以 "990" 开头且 9-10 位 → 机具 (广东编码规则 99xx = 机械).
        # 仅 gd 启用, cq 自身 col0 不会继承"材料", 此分支无副作用.
        if category == "料" and nxt and re.fullmatch(r"99\d{7,8}", nxt[0]["text"].strip()):
            category = "机"
        if not nxt:
            return None
        # 重庆 2018 表结构: col0=分类(材料/机械/未计价), col1=编码(990101015 等),
        #                   col2=名字, col3=单位, col4=单价, col5+=消耗量
        # col1 是冗余"编码"列, 需要跳过——但要兜底:
        # 如果 nxt[0] 不是 6-12 位数字(老格式无编码), 就当 name
        if (len(nxt) >= 2
                and re.fullmatch(r"\d{6,12}", nxt[0]["text"].strip())):
            name_cell = nxt[1]   # 跳过编码列
        else:
            name_cell = nxt[0]   # 老格式 / 兜底
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

    # 找 price：取 unit 之后按 start_col 排序的第一个 cell（允许为空，不跳过空列）
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

    quantities: dict[int, str] = {}
    is_proportion = False
    for pc in project_cols:
        cell_for_pc = None
        for c in cell_positions:
            if c["start_col"] <= pc <= c["end_col"]:
                cell_for_pc = c
                break
        if cell_for_pc:
            raw_q = cell_for_pc["text"].strip()
            q = strip_numeric_brackets(raw_q)
            if re.match(r"^[（(]", raw_q):
                is_proportion = True
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
                if v and re.match(r"^[\d,.\s ]+$", v):
                    quantities[pc] = v
                    break

    # 其他材料费特殊处理
    is_other_material = "其他材料费" in name or "其它材料费" in name
    if is_other_material and (not price or re.fullmatch(r"[\-−–—]+", price)):
        price = "1.000"

    return {
        "name": name,
        "unit": unit,
        "price": price,
        "quantities": quantities,
        "is_other_material": is_other_material,
        "category": category,
        "is_proportion": is_proportion,
    }


# ---------- 单表抽取 ----------
def extract_table(html_text: str, working_content: str, unit_fallback: str,
                  section_id: str = "",
                  seen_pids: set[str] | None = None,
                  pid_has_machine: set[str] | None = None,
                  pid_has_material: set[str] | None = None) -> tuple[list[list[str]], dict | None]:
    """抽一张表的所有行.

    v0.9 新增 seen_pids: process_md_file 维护的跨表已 emit 项目集合.
    续表识别 (gd 2018 真实场景):
      条件: seen_pids 非空 AND 当前表所有 pid 都在 seen_pids 里
            AND grid 内没有"基价(元)"/"综合基价"/"综合单价"行 (即无总基价 = 非主表)
      → 视为 OCR 把大表拆到下一页/下段后的续表, 整张跳过 (材料数据原本就属于前一张表,
        续表里的料/机行属于 OCR 拆表错位, 丢了无副作用).
      注: 仅 gd 启用 (cq/sc 当前未观测到此现象).

    v0.12 新增 pid_has_machine / pid_has_material: process_md_file 预扫描的全局集合.
      - 主表 emit "机具费"自动行时: 如果 PID 在 pid_has_machine (任意表有"机"分类明细),
        跳过 emit (避免与续表机具明细累加重复).
      - 主表 emit "材料费"自动行时: 同理, 如果 PID 在 pid_has_material, 跳过 emit.
      - 续表行 col 1 (项目编码) 填空 (不重复主表编码, 让续表行看起来是主表的接续明细).
    """
    """抽取单个 table，返回 (csv_rows, issue_dict | None)."""
    grid, total_cols, raw_rows, cell_cols = parse_table(html_text)
    if not grid or total_cols < 4 or not raw_rows:
        return [], None

    # v0.10 复合表检测 (gd 2018 真实场景): 一个 <table> 内含多张定额表
    # (OCR 把同一页的 C1-5-13..16 和 C1-5-17..20 合并成一个 <table>, 中间无 </table>)
    # 特征: grid 中 col0="定额编号" 的行出现 ≥2 次, 每次后续 col 有 C1-X-X 格式 PID
    # 处理: 按"定额编号"行切割 grid 为多个 sub-grid, 各 sub-grid 重建 HTML 后递归
    quota_id_rows = [r for r, row in enumerate(grid) if row[0].strip() == "定额编号"]
    if len(quota_id_rows) > 1:
        all_csv_rows: list[list[str]] = []
        first_issue = None
        for gi, sr in enumerate(quota_id_rows):
            end = quota_id_rows[gi + 1] if gi + 1 < len(quota_id_rows) else len(grid)
            sub_grid = grid[sr:end]
            sub_html = _grid_to_html(sub_grid)
            # v0.12 复合表递归: 透传 pid_has_machine/material
            sub_rows, sub_issue = extract_table(sub_html, working_content, unit_fallback,
                                                section_id=section_id, seen_pids=seen_pids,
                                                pid_has_machine=pid_has_machine,
                                                pid_has_material=pid_has_material)
            all_csv_rows.extend(sub_rows)
            if sub_issue and first_issue is None:
                first_issue = sub_issue
        return all_csv_rows, first_issue

    projects = find_projects(grid, total_cols)
    if not projects:
        return [], None
    n_projects = len(projects)
    project_cols = [p["col"] for p in projects]

    # 续表检测 (仅 gd): seen_pids 非空 + 所有 pid 已见 + grid 无基价行
    # 续表的特征是 OCR 把大表切到下一页/下段, 续表 <table> 含重复的定额编号 +
    # 子目名称 block, 但没有"基价(元)"行/没有 material_header, 只有材料/机具明细
    # 接续部分. 应把续表的料/机行 emit 到主表对应 PID 的明细下方, 累加 verify 列.
    if seen_pids is not None and seen_pids:
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
                # 续表: 从第一个材料/机具/工/未计价/配标签行开始抽明细
                material_start: int | None = None
                for r, row in enumerate(grid):
                    if row[0].strip() in ("材料", "机具", "工", "未计价", "配"):
                        material_start = r
                        break
                if material_start is None:
                    return [], None
                # 抽所有材料/机具行 (含 forward-fill 分类标签)
                cont_materials: list[dict] = []
                for ri in range(material_start, len(raw_rows)):
                    mat = parse_material_row(raw_rows[ri], cell_cols[ri], project_cols,
                                             grid=grid, grid_row_idx=ri)
                    if mat is not None:
                        cont_materials.append(mat)
                # emit 续表的料/机行, 每 PID 一行带 col 1=pID; 同时累计 verify delta
                cont_rows: list[list[str]] = []
                cont_verify: dict[str, float] = {p["id"]: 0.0 for p in projects}
                for mat in cont_materials:
                    if mat["category"] == "未计价":
                        continue
                    is_proportion = mat.get("is_proportion", False)
                    is_other = mat["is_other_material"]
                    price = strip_numeric_brackets(mat["price"])
                    for j, pc in enumerate(project_cols):
                        q = mat["quantities"].get(pc, "")
                        q = strip_numeric_brackets(q)
                        qty_f = to_float(q)
                        price_f = to_float(price)
                        if is_proportion:
                            v_str = ""
                        elif is_other:
                            v = qty_f
                            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
                        else:
                            if qty_f == 0.0:
                                v = 0.0
                            elif price_f == 0.0:
                                v = qty_f
                            else:
                                v = qty_f * price_f
                            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
                        if not is_proportion:
                            cont_verify[projects[j]["id"]] += to_float(v_str)
                        row_type = mat.get("category", "料")
                        if row_type == "料" and is_proportion:
                            row_type = "配"
                        # v0.12 续表行 col 1 暂保留 PID (用于 process_md_file 按 PID 分组),
                        # 物理插入到主表 [综] 之前后, 由 process_md_file 清空 col 1
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

    sections = find_section_rows(grid, total_cols)
    # v0.13.2: 透传 raw_rows 让 find_cost_rows 能检测 multi-label 单 cell 拆分
    # (广东 A 分册"其中"块的 4 label 塞 1 cell 场景)
    cost_rows = find_cost_rows(grid, raw_rows=raw_rows)

    # 项目名：找到 col0 为 "项目" / "项 目"（四川/重庆型）或 "子目名称"（广东型）的行区域
    project_names: list[str] = []
    # v0.13 新增: 子目名称 block (广东型 col0="子目名称") 的"最后一行非空文本"备份.
    # 当计量单位=见 表/见表/见表/见表 时, 用这个 last_line 覆盖单位列.
    project_names_last_line: list[str] = []
    parent_rows = [r for r, row in enumerate(grid)
                   if (re.match(r"^(项目|项 目)(?!\s*编)", row[0].strip())
                       or row[0].strip() == "子目名称")]
    for j, pc in enumerate(project_cols):
        parts: list[str] = []
        # v0.13 新增: 记录最后一个非空的"子目名称 block cell" 用于"见表"覆盖
        last_line_text: str = ""
        for pr in parent_rows:
            if pc >= total_cols:
                continue
            txt = grid[pr][pc].strip()
            # 去掉可能存在的 "项目" / "项 目" / "子目名称" 前缀
            txt = re.sub(r"^(项目|项 目|子目名称)\s*", "", txt)
            # 子目名称 block (广东型, col0="子目名称") 的所有内容都是项目描述的一部分
            # 包括第三行的纯数字规格档位 (如 C1-4 的 "2/3/4/5" = 层数具体值),
            # 不能用纯数字过滤跳过. 但 cq 的 "项 目" block 如果真有 forward-fill 数字污染
            # 仍需过滤, 故仅对 "子目名称" block 例外.
            is_zimiao = (grid[pr][0].strip() == "子目名称")
            if not txt:
                continue
            if not is_zimiao and re.fullmatch(r"[\d.,\s]+", txt):
                continue
            # 跳过"定额编号"/"项目编码"标签（这些不是项目名）
            if txt in ("定额编号", "项目编码", "项目编 号"):
                continue
            # v0.13: 子目名称 block 的每一行都是描述, 全部记录 (last_line 只取最后一个非空)
            if is_zimiao:
                last_line_text = txt
            if not parts or txt != parts[-1]:
                parts.append(txt)
        project_names.append(" ".join(parts))
        project_names_last_line.append(last_line_text)

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
    # v3 新增（Bug2 修复）：取"其中-材料费(元)"值，用于"料-材料费"自动行的验证列。
    material_fee_values = get_cost_values("material_fee")
    machine_values = get_cost_values("machine")
    management_values = get_cost_values("management")
    profit_values = get_cost_values("profit")
    # cq: 取"一般风险费"值
    risk_fee_values = get_cost_values("risk_fee")

    # 项目单位
    project_units: list[str] = []
    if "计量单位" in sections:
        mr = sections["计量单位"]
        for i in range(n_projects):
            c = project_cols[i]
            u = grid[mr][c].strip() if c < total_cols else ""
            norm_u = normalize_unit(u)
            # v0.13 新增: 广东型"计量单位"行=见 表/见表 时,
            # 实际单位藏在"子目名称"block 的最后一行 (如 "部"、"10m"、"套").
            # 兜底: last_line 经 normalize_unit 后非空且不是"见 表"系列才覆盖.
            if (re.fullmatch(r"见\s*表", norm_u)
                    and i < len(project_names_last_line)):
                ll = project_names_last_line[i].strip()
                ll_norm = normalize_unit(ll)
                if ll_norm and not re.fullmatch(r"见\s*表", ll_norm):
                    norm_u = ll_norm
            project_units.append(norm_u)
    else:
        # v0.13 新增: 广东型 OCR 表常无 "计量单位" 行, 单位来自 <table> 前 md 文本里的 "单位:见 表",
        # 已经落到 unit_fallback. 如果 unit_fallback=见 表 系列, 用"子目名称"最后一行覆盖.
        unit_fallback_norm = normalize_unit(unit_fallback)
        if re.fullmatch(r"见\s*表", unit_fallback_norm):
            project_units = []
            for i in range(n_projects):
                ll = project_names_last_line[i].strip() if i < len(project_names_last_line) else ""
                ll_norm = normalize_unit(ll)
                if ll_norm and not re.fullmatch(r"见\s*表", ll_norm):
                    project_units.append(ll_norm)
                else:
                    project_units.append(unit_fallback)
        else:
            project_units = [unit_fallback for _ in range(n_projects)]

    # 料行（v3：未计价行的 category="未计价"，识别后由 emit 阶段决定主材分支）
    materials: list[dict] = []
    if "material_header" in sections:
        mh = sections["material_header"]
        for ri, raw_row in enumerate(raw_rows[mh + 1:]):
            actual_r = mh + 1 + ri
            # v3 修复（Bug1）：传 grid + grid_row_idx 让 parse_material_row 能读
            # 被前一行 rowspan 继承下来的 col0（如"未计价"跨多行场景）。
            mat = parse_material_row(raw_row, cell_cols[actual_r], project_cols,
                                     grid=grid, grid_row_idx=actual_r)
            if mat is not None:
                materials.append(mat)

    # 料行严格列数校验（放宽：普通材料允许单价为空；category="未计价" 不参与 §4.5 校验，按 §10.7 独立校验）
    issue_reason = ""
    for mat in materials:
        if mat["category"] == "未计价":
            # 主材行仅做单位非空检查（按 §10.7）
            if not mat["unit"]:
                issue_reason = f"主材行缺少单位: {mat['name']}"
                break
            continue
        if not mat["unit"]:
            issue_reason = f"料行缺少单位: {mat['name']}"
            break
        if len(mat["quantities"]) != n_projects:
            issue_reason = f"料行数量列数不匹配(期望{n_projects}, 实际{len(mat['quantities'])}): {mat['name']}"
            break
    if issue_reason:
        issue = {
            "section_id": section_id,
            "project_id": projects[0]["id"] if projects else "-",
            "reason": issue_reason,
            "prefix": f"工作内容:{working_content} / 单位:{unit_fallback}",
            "html": html_text[:800],
        }
        return [], issue

    # 生成 CSV 行
    csv_rows: list[list[str]] = []
    for j in range(n_projects):
        pid = projects[j]["id"]
        pname = clean_latex_name(project_names[j]) if j < len(project_names) else ""
        punit = project_units[j] if j < len(project_units) else unit_fallback
        pcomp = composite_values[j] if j < len(composite_values) else ""
        plabor = labor_values[j] if j < len(labor_values) else ""
        # v3 新增：每个定额条目 1 个"料-材料费"自动行，验证列=其中-材料费(元)值。
        pmaterial_fee = material_fee_values[j] if j < len(material_fee_values) else ""
        pmachine = machine_values[j] if j < len(machine_values) else ""
        pmgmt = management_values[j] if j < len(management_values) else ""
        pprof = profit_values[j] if j < len(profit_values) else ""
        # cq: 一般风险费
        prisk = risk_fee_values[j] if j < len(risk_fee_values) else ""

        # cq 三对应"费-类"决策：
        #   下方有"人工"分类  → 不 emit 上方"人工费"
        #   下方有"料"/"配"分类 → 不 emit 上方"材料费"
        #   下方有"机"分类   → 不 emit 上方"施工机具使用费"
        has_labor_below = any(mat["category"] == "工" for mat in materials)
        has_material_below = any(mat["category"] in ("料", "配") for mat in materials)
        has_machine_below = any(mat["category"] == "机" for mat in materials)

        # 定行
        sub_verifications: list[float] = []

        csv_rows.append(["定", pid, pname, working_content, punit, "", pcomp, "", "", ""])

        # 工行（人工费）— cq: 下方有人工分类则不 emit
        if "labor" in cost_rows and not has_labor_below:
            v = to_float(plabor)
            sub_verifications.append(v)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["工", "", "人工费", "", "元", plabor, "1.00", v_str, "", ""])

        # 主材（按 v3 既有逻辑；cq 暂未发现"未计价"标签，但支持识别）
        for mat in materials:
            if mat["category"] != "未计价":
                continue
            q = mat["quantities"].get(project_cols[j], "")
            q = strip_numeric_brackets(q)
            csv_rows.append([
                "主材", "", clean_latex_name(mat["name"]), "",
                mat["unit"], q, "", "", "", "",
            ])

        # 料行（材料费）— cq: 下方有材料分类则不 emit；否则 emit（若值 > 0）
        # gd 用户指示: "材料费"自动行的 v_str 仅做显示, 不累加到 verify（避免和下方料明细/续表料明细重复）
        # 管理费仍累加（下方没有管理费分类的明细）
        # v0.12 全局判断: 如果 PID 在 pid_has_material (任意表有"材料"分类明细), 隐藏"材料费"自动行
        pid_material_global = (pid_has_material is not None
                               and any(p["id"] in pid_has_material for p in projects))
        if ("material_fee" in cost_rows and not has_material_below
                and not pid_material_global and to_float(pmaterial_fee) > 0):
            v = to_float(pmaterial_fee)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["料", "", "材料费", "", "元", pmaterial_fee, "1.00", v_str, "", ""])

        # 下方料/配/机/工行（普通材料、配、机械、人工）
        for mat in materials:
            if mat["category"] == "未计价":
                continue
            q = mat["quantities"].get(project_cols[j], "")
            q = strip_numeric_brackets(q)
            price = strip_numeric_brackets(mat["price"])
            qty_f = to_float(q)
            price_f = to_float(price)
            is_proportion = mat.get("is_proportion", False)
            if is_proportion:
                v_str = ""
            elif mat["is_other_material"]:
                v = qty_f
                v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            else:
                if qty_f == 0.0:
                    v = 0.0
                elif price_f == 0.0:
                    v = qty_f
                else:
                    v = qty_f * price_f
                v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            if not is_proportion:
                sub_verifications.append(to_float(v_str))
            row_type = mat.get("category", "料")
            if row_type == "料" and is_proportion:
                row_type = "配"
            csv_rows.append([row_type, "", clean_latex_name(mat["name"]), "", mat["unit"], q, price, v_str, "", ""])

        # 机行（施工机具使用费/机械费）— cq: 下方有机分类则不 emit；否则 emit（若值 > 0）
        # gd 用户指示: "机具费"自动行的 v_str 仅做显示, 不累加到 verify（避免和续表机具明细重复）
        # v0.12 全局判断: 如果 PID 在 pid_has_machine (任意表有"机"分类明细), 隐藏"机具费"自动行
        pid_machine_global = (pid_has_machine is not None
                              and any(p["id"] in pid_has_machine for p in projects))
        if ("machine" in cost_rows and not has_machine_below
                and not pid_machine_global and to_float(pmachine) > 0):
            v = to_float(pmachine)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["机", "", "机具费", "", "元", pmachine, "1.00", v_str, "", ""])

        # 综行（按实际存在；cq 多一项"一般风险费"，同企业管理费/利润一致计入 verify）
        if "management" in cost_rows:
            v = to_float(pmgmt)
            sub_verifications.append(v)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["综", "", "企业管理费", "", "元", "100.00", pmgmt, v_str, "", ""])
        if "risk_fee" in cost_rows:
            v = to_float(prisk)
            sub_verifications.append(v)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["综", "", "一般风险费", "", "元", "100.00", prisk, v_str, "", ""])

        # 定行验证列回填
        total_v = sum(sub_verifications)
        v_str = f"{total_v:.2f}".rstrip("0").rstrip(".") if "." in f"{total_v:.2f}" else f"{total_v:.2f}"
        # 找到本项目的定行（刚添加的第一行）
        # 从后往前找当前项目的定行
        for idx in range(len(csv_rows) - 1, -1, -1):
            if csv_rows[idx][0] == "定":
                csv_rows[idx][7] = v_str
                break

        csv_rows.append(["", "", "", "", "", "", "", "", "", ""])

    return csv_rows, None


# ---------- 段行提取 ----------
def extract_sections_before(text: str) -> list[tuple[str, str]]:
    """从文本中提取段标记，返回 [(编号, 名称), ...].

    v0.13.3 新增 3 种形式的标题识别（应对广东 OCR 抖动）:
      - 标准型: ## A.1.5 混凝土工程         (SECTION_RE)
      - 紧凑型: ## A.1.5混凝土工程          (SECTION_RE_COMPACT, 常见于 TOC 行尾)
      - 孤标型: ## A.1.5 + 下一行标题        (SECTION_RE_HEAD_ONLY, 章节首页 OCR 断行)
    顺序: 先试标准型, 再试紧凑型, 最后试孤标型(peek 下一行).
    """
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
        # Pass 1: 标准型 ## A.X.Y 标题
        m = SECTION_RE.match(line)
        if m:
            sections.append((re.sub(r"\s+", "", m.group(1)), m.group(2).strip()))
            matched = True
        else:
            # Pass 2: 紧凑型 ## A.X.Y标题 (无空格分隔)
            m = SECTION_RE_COMPACT.match(line)
            if m:
                title = m.group(2).strip()
                # title 非空才算 (避免 ## A.1.6 后是空字符串被误识别)
                if title:
                    sections.append((re.sub(r"\s+", "", m.group(1)), title))
                    matched = True
            if not matched:
                # Pass 3: 孤标型 ## A.X.Y (标题在下一行)
                m = SECTION_RE_HEAD_ONLY.match(line)
                if m:
                    # peek 下一行, 找第一个非空且非 `##` 开头的行作为 title
                    j = i + 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n:
                        next_line = lines[j].strip()
                        if next_line and not next_line.startswith("#"):
                            # 排除 TOC 行 (标题里含页码)
                            if not TOC_PAGE_TAIL.search(next_line):
                                sections.append((re.sub(r"\s+", "", m.group(1)), next_line))
                                matched = True
                                i = j  # 跳过标题行,避免被下一轮当独立章节

        if not matched:
            # Pass 4: 纯文本编号行 (SECTION_RE2)
            m = SECTION_RE2.match(line)
            if m:
                sections.append((re.sub(r"\s+", "", m.group(1)), m.group(2).strip()))

        i += 1
    return sections


# ---------- 单文件处理 ----------
def process_md_file(md_path: Path) -> tuple[list[list[str]], list[dict]]:
    content = md_path.read_text(encoding="utf-8")

    # 找到所有 table 的位置
    table_matches = list(re.finditer(r"<table[^>]*>.*?</table>", content, flags=re.S))

    # v0.12 预扫描: 扫完全部 table, 建立 pid_has_machine / pid_has_material 集合
    #   - 用于主表 emit "机具费"/"材料费"自动行时, 全局判断是否隐藏
    #   - 集合包含任意表 (主表 OR 续表) 内有"机"/"材料"分类明细行的 PID
    #   - 例如: C1-4-1 主表下方没有"机"分类, 但续表有"机"明细 → PID 加入集合 →
    #     主表 emit 时知道下游有机明细, 隐藏"机具费"自动行 (避免与续表机具明细累加重复)
    pid_has_machine: set[str] = set()
    pid_has_material: set[str] = set()
    for m in table_matches:
        grid, total_cols, raw_rows, cell_cols = parse_table(m.group(0))
        if not grid or total_cols < 4 or not raw_rows:
            continue
        projects_scan = find_projects(grid, total_cols)
        if not projects_scan:
            continue
        # v0.12 不依赖 material_header (续表没有 material_header, 但仍有 "材料"/"机具"分类行)
        # 直接扫整张 grid 的 col0 分类标签
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
    seen_pids: set[str] = set()  # v0.9 跨表已 emit 的 project_id, 用于续表检测
    # v0.11 物理接续: 记录每个 PID 主表 emit 段的 [blank] 行索引, 续表行插入到 [blank] 之前
    pid_blank_idx: dict[str, int] = {}
    # v0.13.4: TOC 标题库 + body 已 emit 集合
    #   - toc_titles: 从第 1 个 prefix (TOC area) 收集的 id→title, 用于 body 缺父段时 carry-forward
    #   - body_emitted: body 阶段已 emit 的段 id (子段触发时, 走父链补缺)
    toc_titles: dict[str, str] = {}
    body_emitted: set[str] = set()

    for table_idx, m in enumerate(table_matches):
        table_html = m.group(0)
        table_start = m.start()
        prefix = content[prev_end:table_start]
        is_body = (table_idx >= 1)  # v0.13.4: 第 1 张表 = TOC, 之后 = body

        # 只提取本段 prefix 中的段标记（相邻 table 之间）
        secs = extract_sections_before(prefix)
        # v0.13.4: body 阶段, 子段触发时, 补缺其未 emit 的祖先段 (从 toc_titles 取标题)
        expanded_secs: list[tuple[str, str]] = []
        for sec_id, sec_name in secs:
            if is_body:
                # 走父链: A.1.3.1 -> A.1.3 -> A.1
                ancestors: list[tuple[str, str]] = []
                parts = sec_id.split(".")
                # 累积父链: A.1.3.1 -> ['A.1', 'A.1.3'] -> ['A', 'A.1', 'A.1.3']
                cur_parts: list[str] = []
                for p in parts[:-1]:  # 跳过最后一段 (本身)
                    cur_parts.append(p)
                    ancestor_id = ".".join(cur_parts)
                    if ancestor_id not in body_emitted:
                        # 用 toc_titles 的标题; 没记录就用空标题 (兜底)
                        anc_title = toc_titles.get(ancestor_id, "")
                        ancestors.append((ancestor_id, anc_title))
                # 按从外到内 emit (A.1 在前, A.1.3 在后)
                for a_id, a_title in ancestors:
                    if a_id not in body_emitted:
                        expanded_secs.append((a_id, a_title))
                        body_emitted.add(a_id)
            expanded_secs.append((sec_id, sec_name))
            # v0.13.4: 仅在 body 阶段 add 到 body_emitted, 否则 TOC 阶段会把所有父段都标 '已 emit',
            #          导致 body 阶段 A.1.3.1 触发时, A.1.3 误判为 '已 emit' 而跳过祖先补缺
            if is_body:
                body_emitted.add(sec_id)

        # v0.13.4: TOC 阶段收集 toc_titles (从已 emit 的条目; 仅保留 '干净' 标题, 即不是 page-tail 衍生)
        if not is_body:
            for sec_id, sec_name in secs:
                # 仅在 toc_titles 里没记录时写入 (避免 TOC area 里页码尾行覆盖正确标题)
                if sec_id not in toc_titles and sec_name and not TOC_PAGE_TAIL.search(sec_name):
                    toc_titles[sec_id] = sec_name

        for sec_id, sec_name in expanded_secs:
            last_section_id = sec_id
            all_rows.append(["段", sec_id, clean_latex_name(sec_name), "", "", "", "", "", "", ""])

        # 提取工作内容
        # 提取工作内容（支持多行，结束于"单位："或<table）
        wc_match = None
        for wc_m in re.finditer(r"工作内容[：:]\s*", prefix):
            wc_match = wc_m

        if wc_match:
            rest = prefix[wc_match.end():]
            end_match = re.search(r"单位[：:]|<table", rest)
            wc_text = rest[:end_match.start()] if end_match else rest
            wc_text = wc_text.strip()
            wc_text = re.sub(r"\s+", " ", wc_text)
        else:
            wc_text = ""

        # 提取单位 fallback
        unit_matches = list(re.finditer(r"单位[：:]\s*([^\n]+)", prefix))
        unit_text = unit_matches[-1].group(1).strip() if unit_matches else ""
        unit_fallback = normalize_unit(unit_text)

        # v0.12 调 extract_table 时传 pid_has_machine/material (用于"机具费/材料费"自动行全局隐藏判断)
        rows, meta = extract_table(table_html, wc_text, unit_fallback,
                                   section_id=last_section_id, seen_pids=seen_pids,
                                   pid_has_machine=pid_has_machine,
                                   pid_has_material=pid_has_material)
        if meta is not None and meta.get("continuation"):
            # 续表: 把 rows 按 PID 物理插入到主表对应 PID 的 [blank] 行之前,
            # 同时按 verify_deltas 累加到对应定行 verify 列
            verify_deltas = meta.get("verify_deltas", {})
            pids = meta.get("pids", [])
            # v0.12 按 meta.pids 顺序初始化分组 (不读 col 1, 因为插入后会清空 col 1)
            cont_by_pid: dict[str, list[list[str]]] = {pid: [] for pid in pids}
            for r in rows:
                pid = r[1]
                if pid in cont_by_pid:
                    cont_by_pid[pid].append(r)
            # 按 pids 顺序逐个 PID 处理 (从后往前插, 索引不冲突)
            for pid in reversed(pids):
                insert_rows = cont_by_pid.get(pid, [])
                if not insert_rows:
                    continue
                if pid not in pid_blank_idx:
                    # 主表没 emit 过这个 PID, 续表无法接续 → append 末尾
                    for r in insert_rows:
                        r[1] = ""  # v0.12 清空 PID (避免看起来像新定额条目)
                        all_rows.append(r)
                    continue
                blank_idx = pid_blank_idx[pid]
                # v0.12: 续表插入位置 = 主表 emit 段的 [综 企业管理费] 自动行之前
                #   兜底: 如果该 PID 主表 emit 段没有 [综] 自动行, 则用 [blank] 行
                #   原因: 用户指示 "续表的料要放在综字段前面", 让 "明细 → 管理费" 的逻辑顺序清晰
                insert_idx = blank_idx  # 兜底
                for i in range(blank_idx - 1, -1, -1):
                    if all_rows[i][0] == "综" and all_rows[i][2] == "企业管理费":
                        insert_idx = i
                        break
                # 在 insert_idx 处插入 N 行; insert_idx 之后的所有行都后移 N 位
                all_rows[insert_idx:insert_idx] = insert_rows
                # v0.12 清空已插入续表行的 col 1 (避免看起来像新定额条目)
                for r in insert_rows:
                    r[1] = ""
                inserted = len(insert_rows)
                # 更新所有 PID 的 blank_idx (>= insert_idx 的都后移 inserted)
                for k, v in pid_blank_idx.items():
                    if v >= insert_idx:
                        pid_blank_idx[k] = v + inserted
                # 累加 verify delta 到该 PID 的定行 (在 insert_idx 之前找)
                delta = verify_deltas.get(pid, 0.0)
                if delta:
                    for idx in range(insert_idx - 1, -1, -1):
                        row = all_rows[idx]
                        if row[0] == "定" and row[1] == pid:
                            old_v = to_float(row[7])
                            new_v = old_v + delta
                            v_str = (f"{new_v:.2f}".rstrip("0").rstrip(".")
                                     if "." in f"{new_v:.2f}" else f"{new_v:.2f}")
                            row[7] = v_str
                            break
            # 续表的 PID 已在主表 emit 过, 不更新 seen_pids
        elif meta is not None and "reason" in meta:
            # 异常表
            issues.append(meta)
        else:
            # 普通表
            base_len = len(all_rows)
            all_rows.extend(rows)
            # 扫描新 emit 的 rows 部分, 找每个 PID 的 [blank] 行索引
            # rows 内部: 每个 PID 段以 [blank] 结尾, 最后一个 cell 是空白
            for i in range(len(rows) - 1, -1, -1):
                if rows[i][0] == "":
                    # 这个 [blank] 之前的定行就是对应 PID
                    for j in range(i - 1, -1, -1):
                        if rows[j][0] == "定" and rows[j][1]:
                            pid_blank_idx[rows[j][1]] = base_len + i
                            break
            # 把本次新 emit 的 project_id 加入 seen_pids, 给后续续表检测用
            for r in rows:
                if r[0] == "定" and r[1]:
                    seen_pids.add(r[1])

        prev_end = m.end()

    return all_rows, issues


# ---------- main ----------
def main():
    if len(sys.argv) < 2:
        print("Usage: quota_md_to_csv.py <input.md> [output.csv]")
        sys.exit(1)

    md_path = Path(sys.argv[1])
    if not md_path.exists():
        print(f"[ERROR] 文件不存在: {md_path}")
        sys.exit(1)

    if len(sys.argv) > 2:
        output_csv = Path(sys.argv[2])
    else:
        # 默认输出 CSV：MD 同目录、同 stem，**加 `_数值待审核` 后缀**标记未经人工核对
        # （人工核对通过后可去掉该后缀，或直接传给 quota-csv-finalize）
        output_csv = md_path.with_name(md_path.stem + "_数值待审核.csv")

    rows, issues = process_md_file(md_path)

    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)

    n_sec = sum(1 for r in rows if r[0] == "段")
    n_proj = sum(1 for r in rows if r[0] == "定")

    if issues:
        issues_md = output_csv.with_name(output_csv.stem + "_issues.md")
        with issues_md.open("w", encoding="utf-8") as f:
            f.write("# 解析问题报告\n\n")
            f.write(f"> 来源: {md_path.name}\n")
            f.write(f"> 生成时间: {datetime.now(timezone.utc).isoformat()}\n\n")
            f.write("---\n\n")
            for i, issue in enumerate(issues, 1):
                f.write(f"## 异常表 {i}\n\n")
                f.write(f"- **章节定位**: `{issue['section_id'] or '-'}'`\n")
                f.write(f"- **项目编码**: `{issue['project_id']}`\n")
                f.write(f"- **失败原因**: {issue['reason']}\n")
                f.write(f"- **原始表前文本**: {issue['prefix']}\n")
                f.write(f"- **原始 HTML 摘要**:\n  ```html\n  {issue['html'][:500]}\n  ```\n\n")
        print(f"[OK] 解析完成: {output_csv}")
        print(f"[OK] 段行: {n_sec}")
        print(f"[OK] 定额条目: {n_proj}")
        print(f"[OK] 异常表: {len(issues)} 个（详见 {issues_md.name}）")
        print(f"[OK] 总行数: {len(rows)}")
    else:
        print(f"[OK] 解析完成: {output_csv}")
        print(f"[OK] 段行: {n_sec}")
        print(f"[OK] 定额条目: {n_proj}")
        print(f"[OK] 无异常表")
        print(f"[OK] 总行数: {len(rows)}")


if __name__ == "__main__":
    main()