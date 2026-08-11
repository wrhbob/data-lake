#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quota_md_to_csv.py — 定额 Markdown → 10 列 CSV

按 SPEC.md (quota-md-to-csv) 实现：
  输入: 单份 .md 文件（含 HTML table）
  输出: 10 列 CSV (类型|项目编码|名称|项目特征|计量单位|消耗量|基价/单价|验证|标准换算|标准换算来源)

支持场景：
  - 普通料/配/机行（v2 既有行为，完整保留）
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
SECTION_RE = re.compile(
    r"^\s?##\s+([A-Z](?: ?\.? ?\d+)*)(?:\s+|[　])(.+?)\s*$"  # 二级标题（行首/编号内最多 1 空格）
)
SECTION_RE2 = re.compile(
    r"^\s?([A-Z](?: ?\.? ?\d+)*)(?:\s+|[　])(.+?)\s*$"        # 纯文本编号行（行首/编号内最多 1 空格）
)
PROJECT_ID_RE = re.compile(r"^([A-Z]{1,2}\d{4})$")

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
                pid = m.group(1)
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
            has_label = ("综合基价" in full_text or
                         "基 价 (元)" in full_text or
                         "基 价（元）" in full_text or
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
            if col0.startswith("名 称") or col0.startswith("名称"):
                sections["material_header"] = r

    return sections


def find_cost_rows(grid) -> dict:
    """识别 人工费 / 材料费 / 机械费 / 管理费 / 利润 行；兼容 OCR 切分（去全部空白后匹配）."""
    cost_rows: dict = {}
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
                and "机械费" not in full_norm
                and "管理费" not in full_norm
                and "利润" not in full_norm):
            cost_rows["material_fee"] = r
        if "machine" not in cost_rows and "机械费" in full_norm:
            cost_rows["machine"] = r
        if "management" not in cost_rows and "管理费" in full_norm and "利润" not in full_norm:
            cost_rows["management"] = r
        if "profit" not in cost_rows and "利" in full_norm and "管理" not in full_norm:
            cost_rows["profit"] = r
    return cost_rows


def find_value_in_row(row, start_col: int = 1) -> str:
    for c in range(start_col, len(row)):
        v = row[c].strip()
        if v and re.match(r"^[\d,.\s ]+$", v):
            return v
    return ""


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
    # v3 新增："未计价"作为 col0 分组标签（与"材料"/"机械"同级），识别后 category="未计价"
    if raw_name in ("材", "料", "机", "材 料", "材料", "机 械", "料 料", "机械",
                    "未计价", "未 计 价", "未 计价", "未计 价"):
        raw_norm = raw_name.replace(" ", "")
        if raw_norm == "未计价":
            category = "未计价"
        else:
            category = "机" if "机" in raw_norm else "料"
        nxt = [c for c in cell_positions
               if c["start_col"] > first_cell["end_col"] and c["text"].strip()]
        if not nxt:
            return None
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
                  section_id: str = "") -> tuple[list[list[str]], dict | None]:
    """抽取单个 table，返回 (csv_rows, issue_dict | None)."""
    grid, total_cols, raw_rows, cell_cols = parse_table(html_text)
    if not grid or total_cols < 4 or not raw_rows:
        return [], None

    projects = find_projects(grid, total_cols)
    if not projects:
        return [], None
    n_projects = len(projects)
    project_cols = [p["col"] for p in projects]

    sections = find_section_rows(grid, total_cols)
    cost_rows = find_cost_rows(grid)

    # 项目名：找到 col0 为 "项目" / "项 目"（精确或后接非"编"字符）的行区域
    project_names: list[str] = []
    parent_rows = [r for r, row in enumerate(grid)
                   if re.match(r"^(项目|项 目)(?!\s*编)", row[0].strip())]
    for j, pc in enumerate(project_cols):
        parts: list[str] = []
        for pr in parent_rows:
            txt = grid[pr][pc].strip() if pc < total_cols else ""
            # 去掉可能存在的 "项目" / "项 目" 前缀
            txt = re.sub(r"^(项目|项 目)\s*", "", txt)
            if txt and (not parts or txt != parts[-1]):
                parts.append(txt)
        # 去重相邻重复
        deduped: list[str] = []
        for p in parts:
            if not deduped or p != deduped[-1]:
                deduped.append(p)
        project_names.append(" ".join(deduped))

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

    # 项目单位
    project_units: list[str] = []
    if "计量单位" in sections:
        mr = sections["计量单位"]
        for i in range(n_projects):
            c = project_cols[i]
            u = grid[mr][c].strip() if c < total_cols else ""
            project_units.append(normalize_unit(u))
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

        # 定行
        sub_verifications: list[float] = []

        csv_rows.append(["定", pid, pname, working_content, punit, "", pcomp, "", "", ""])

        # 工行（若存在）
        if "labor" in cost_rows:
            v = to_float(plabor)
            sub_verifications.append(v)
            csv_rows.append(["工", "", "人工费", "", "元", plabor, "1.00", f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}", "", ""])

        # 料行（若存在）
        # v3 调整（按用户要求）：所有未计价主材放在一起 → 料-材料费自动行
        # 紧随其后 → 再 emit 其他料行（普通料/配/机分类下的料）。
        has_unpriced = any(mat["category"] == "未计价" for mat in materials)
        # 是否还有任何已计价材料行（料/配）→ 若是，料行已各自贡献 verify 列，材料费不再叠加。
        has_priced_material = any(mat["category"] in ("料", "配") for mat in materials)

        # 第一遍：emit 所有未计价主材（§3.3.7）
        for mat in materials:
            if mat["category"] != "未计价":
                continue
            q = mat["quantities"].get(project_cols[j], "")
            q = strip_numeric_brackets(q)
            csv_rows.append([
                "主材", "", clean_latex_name(mat["name"]), "",
                mat["unit"], q, "", "", "", "",
            ])

        # 料-材料费 自动行（§3.3.8）：以下任一条件满足 → emit 1 次
        #   A. 表里有未计价主材（has_unpriced）：与主材配对的料-材料费
        #   B. 表里无任何已计价材料行（!has_priced_material）但其中-材料费(元) > 0：
        #      例如表只有机械行（DE0341）或完全无材料区（DE0159），仍有一笔隐含
        #      "其他材料费"需要写入。
        # 消耗量=pmaterial_fee；单价=1.00；验证=pmaterial_fee。
        # 该值参与定行验证求和。
        def _has_valid_material_fee(s: str) -> bool:
            if not s:
                return False
            try:
                v = float(str(s).replace(",", "").strip())
                return v > 0.0
            except (ValueError, TypeError):
                return False

        should_emit_material_fee = has_unpriced or (
            not has_priced_material and _has_valid_material_fee(pmaterial_fee)
        )
        if should_emit_material_fee:
            csv_rows.append([
                "料", "", "材料费", "",
                "元", pmaterial_fee, "1.00", pmaterial_fee, "", "",
            ])
            sub_verifications.append(to_float(pmaterial_fee))

        # 第二遍：emit 其他料行（普通料/配/机分类下的料）
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

        # 机行（若存在）
        if "machine" in cost_rows:
            v = to_float(pmachine)
            sub_verifications.append(v)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["机", "", "机械费", "", "元", pmachine, "1.00", v_str, "", ""])

        # 综行（按实际存在）
        if "management" in cost_rows:
            v = to_float(pmgmt)
            sub_verifications.append(v)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["综", "", "企业管理费", "", "元", "100.00", pmgmt, v_str, "", ""])
        if "profit" in cost_rows:
            v = to_float(pprof)
            sub_verifications.append(v)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["综", "", "利润", "", "元", "100.00", pprof, v_str, "", ""])

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
    """从文本中提取段标记，返回 [(编号, 名称), ...]."""
    sections: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        m = SECTION_RE.match(line)
        if m:
            # 编号内的空格（来自 OCR 抖动如 "L. 8"）写入时标准化为无空格
            sections.append((re.sub(r"\s+", "", m.group(1)), m.group(2).strip()))
            continue
        m = SECTION_RE2.match(line)
        if m:
            sections.append((re.sub(r"\s+", "", m.group(1)), m.group(2).strip()))
    return sections


# ---------- 单文件处理 ----------
def process_md_file(md_path: Path) -> tuple[list[list[str]], list[dict]]:
    content = md_path.read_text(encoding="utf-8")

    # 找到所有 table 的位置
    table_matches = list(re.finditer(r"<table[^>]*>.*?</table>", content, flags=re.S))

    all_rows: list[list[str]] = []
    issues: list[dict] = []
    last_section_id = ""
    prev_end = 0

    for m in table_matches:
        table_html = m.group(0)
        table_start = m.start()
        prefix = content[prev_end:table_start]

        # 只提取本段 prefix 中的段标记（相邻 table 之间）
        secs = extract_sections_before(prefix)
        for sec_id, sec_name in secs:
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

        rows, issue = extract_table(table_html, wc_text, unit_fallback, section_id=last_section_id)
        if issue:
            issues.append(issue)
        all_rows.extend(rows)

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