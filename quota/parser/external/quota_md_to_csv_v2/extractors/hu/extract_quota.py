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
    r"^\s?([A-Z](?: ?\.? ?\d+)+)(?:\s+|[　])([^\s\-<>\[({].*?)\s*$"  # 纯文本编号行（v0.13.5: 组1 至少 1 个 .N 段, 拒 'W 单=L×B×H×R' 等单字母变量定义; 标题不能以箭头/括号开头, 拒 mermaid 'A --> B[...]'）
)
# hu 项目编码: {字母}{章}-{定额号}
# - 字母是 PDF 整册代号 (G=土石方 / ...), 整册固定一个字母
# - 仅 1 个 '-' 分隔, 因为定额编号本身不带「节」概念
#   例: G1-1 = 第 G 册 第 1 章 第 1 个定额; G2-113 = 第 G 册 第 2 章 第 113 个定额
# - 段行编码的章号 X 直接从定额编号的章号段提取 (节/小节/小小节 由段行文字识别 + 累加)
# - 容忍单空格 (OCR 抖动, 如 "G 1-1" / "G1 -1")
PROJECT_ID_RE = re.compile(r"^([A-Z])\s*(\d+)\s*-\s*(\d+)$")

# hu 段行识别 (4 种标记, 全部在 <table> 之前的 md 文本里)
# 1. 第X章:        "## 第一章  土石方工程" / "第一章 土石方工程" / "第一章　土石方工程"
# 2. 节:           "一、xx" / "一、 xx"   (编号和顿号之间允许空格, 含全角括号容忍)
# 3. 小节:         "1.挖土方" / "1. 挖土方" (允许半角/全角点和空格)
# 4. 小小节:       "(1)人工挖一般土方" / "（1）人工挖一般土方" / "()人工挖一般土方" / "（ ）人工挖一般土方"
#                  (空括号 → 编号固定为 _NUM_MISSING("X"), 留人工审核, 无末尾点)
# v0.14.3: 不再以 markdown # 前缀为匹配依据 — OCR 对 # 输出极不一致, 实测真实定额体
#   大部分节/小节/小小节行不带 # (节 71/115、小节 156/171、小小节 102/115 无 #)。
#   统一按内容匹配; # 只做可选容忍 (0-6 个), 噪音靠下游验证规则过滤.
_SECTION_PREFIX = r"^\s*#{0,6}\s*"

SECTION_ZH_CHAPTER_RE = re.compile(_SECTION_PREFIX + r"第\s*([一-龥]{1,3})\s*章\s*(.*?)\s*$")
SECTION_ZH_SECTION_RE = re.compile(_SECTION_PREFIX + r"([一-龥]{1,3})\s*[、,]\s*(.+?)\s*$")
SECTION_ZH_SUBSECTION_RE = re.compile(_SECTION_PREFIX + r"(\d{1,3})\s*[\.．]\s*([^\.]+?)\s*$")
# 小小节: group(1) = 数字 (可能为空字符串 → 编号固定 _NUM_MISSING "X"), group(2) = 名称
SECTION_ZH_SUBSUBSECTION_RE = re.compile(
    _SECTION_PREFIX + r"[\(【（]\s*(\d*)\s*[\)】）]\s*(.+?)\s*$"
)
# 残括号型: ()xx / （）xx / ( )xx / (  )xx (用户要求: 括号里无数字 → 编号固定 X)
SECTION_ZH_SUBSUBSECTION_EMPTY_RE = re.compile(
    _SECTION_PREFIX + r"[\(【（]\s*[\)】）]\s*(.+?)\s*$"
)
# 残括号 + 数字: 没有最外的括号, 但有数字紧跟上下文 (兜底极少见)
# 不实现, 走空括号型即可

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


# ---------- hu 湖北专用: 中文数字转阿拉伯 ----------
# hu 段行标记: 「第X章」「一、」「1.」「(1)」 中的数字位都是中文数字
# 范围: 一~九十九 (湖北定额实际章节深度一般 ≤ 30)
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s: str) -> int | None:
    """中文数字转 int. 一→1, 十→10, 二十→20, 二十一→21, 三十→30.

    失败返回 None (调用方决定 skip/warning).
    """
    if not s:
        return None
    s = s.strip()
    if s == "十":
        return 10
    if s.startswith("十") and len(s) > 1:  # 十一~十九
        rest = s[1:]
        if rest in _CN_DIGIT:
            return 10 + _CN_DIGIT[rest]
        return None
    if s.endswith("十"):  # 二十/三十/.../九十
        head = s[:-1]
        if head in _CN_DIGIT:
            return _CN_DIGIT[head] * 10
        return None
    if "十" in s:  # 二十一/三十二/.../九十九
        head, _, tail = s.partition("十")
        if head in _CN_DIGIT and tail in _CN_DIGIT:
            return _CN_DIGIT[head] * 10 + _CN_DIGIT[tail]
        return None
    # 单字符 一~九
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    return None


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
            # hu 湖北 2024: 综合基价行叫 "全费用(元)" (label 独特, 行内只要 ≥1 数字即认定)
            #   多列子目表: <td colspan="4">全费用(元)</td><td>v1</td>...
            #   单列子目表: <td colspan="3">全费用(元)</td><td>v1</td>
            #   行内无数字的描述行 (如说明文字) 不匹配, 避免误命中
            is_full_cost = ("全费用(元)" in full_text or
                            "全费用（元）" in full_text or
                            "全费用" in full_text)
            row_has_number = any(
                re.match(r"^[\d,.\s]+$", c.strip())
                for c in row if c.strip()
            )
            has_value = any(
                re.match(r"^[\d,.\s]+$", c.strip())
                for c in row[4:] if c.strip()
            )
            if (has_label and has_value) or (is_full_cost and row_has_number):
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
                   ("machine", "机械费"), ("management", "管理费")]

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
                and "机械费" not in full_norm
                and "管理费" not in full_norm):
            cost_rows["material_fee"] = r
        # hu: 机器识别 "机械费" (湖北 2018 用新命名"机械费", cq 是 "机械费/施工机具使用费", 广东 2018 用"机具费")
        if "machine" not in cost_rows and "机械费" in full_norm:
            cost_rows["machine"] = r
        if "management" not in cost_rows and "管理费" in full_norm:
            cost_rows["management"] = r
        # hu 湖北 2018: 识别 "费用" 行 (不是玻璃里的"管理费"或"利润", 而是"费用"独立行)
        #   例: "费用(元) 3385.17" 出现在"机械费"和"增值税"之间
        #   排除词: "管理费"/"利润" 已含 "费" 字, 但这些字段另外有完整词
        #   v0.14.4: 必须排除 "全费用(元)" 行 —— "全费用" 字面含 "费用",
        #     否则 cost_rows["fee"] 错指 全费用行, 真实"费用"值丢失
        #     (全费用 已在 find_section_rows 识别为 composite = 定额总价)
        if ("fee" not in cost_rows and "费用" in full_norm
                and "管理费" not in full_norm and "全费用" not in full_norm):
            cost_rows["fee"] = r
        # hu 湖北 2018: 识别 "增值税" 行
        if "vat" not in cost_rows and "增值税" in full_norm:
            cost_rows["vat"] = r
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
    is_appendix = False  # v0.13.12: 附项标记 — 排序时插在「料」末、「机」前
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
    # gd 2018: "附项" 也是分组标签 (园林绿化工程 PDF, 例: 04090135 余土外运 m³);
    #         按"料"归类 + 计入 verify (余土外运是工程量附加项, 与主材类似消耗 m³)
    elif raw_name in ("材", "料", "机", "机具", "材 料", "材料", "机 具", "机 械", "料 料", "机械",
                    "未计价", "未 计 价", "未 计价", "未计 价",
                    "附项", "附 项", "附  项"):
        raw_norm = raw_name.replace(" ", "")
        if raw_norm == "未计价":
            category = "未计价"
        else:
            # "附项" 不含"机", 自动落到"料"; 后续 L479 99xx 编码检查不会误转"机"
            category = "机" if "机" in raw_norm else "料"
        # v0.13.12: "附项" emit 时插在「料」末、「机」前 (PDF 视觉顺序: 材料 → 机具 → 附项)
        is_appendix = (raw_norm == "附项")
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

    # 其他材料费特殊处理 (湖北 2024: 单位=% 的比例行, 无单价)
    is_other_material = "其他材料费" in name or "其它材料费" in name
    if is_other_material:
        # v0.14.5: 基价不填任何数值 (百分比行, 验证列在 emit 循环单独按
        #   Σ(其他料行验证列) × 百分比/100 计算, 见 L1042 预计算)
        price = ""

    return {
        "name": name,
        "unit": unit,
        "price": price,
        "quantities": quantities,
        "is_other_material": is_other_material,
        "category": category,
        "is_proportion": is_proportion,
        "is_appendix": is_appendix,  # v0.13.12: 附项位置标记
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
    # hu 湖北 2024: 取"费用"和"增值税"两个值，生成 2 个独立综行
    fee_values = get_cost_values("fee")
    vat_values = get_cost_values("vat")
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
        # hu 湖北 2024: 费用 + 增值税
        pfee = fee_values[j] if j < len(fee_values) else ""
        pvat = vat_values[j] if j < len(vat_values) else ""
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

        # v0.13.12: 附项位置排序 — 料(0) → 附项(1) → 机(2)
        #   PDF 视觉顺序: 材料 → 机具 → 附项, 期望 emit 顺序: 材料 → 附项 → 机具
        #   stable sort 同 priority 保持原顺序; "未计价"/"工"/"综" 不在此循环内
        def _mat_emit_priority(m: dict) -> int:
            if m["category"] == "未计价":
                return -1  # 主材单独循环 (L910), 此处不会 emit
            if m.get("is_appendix"):
                return 1   # 附项
            if m["category"] == "机":
                return 2   # 机具
            return 0       # 普通料/配
        materials.sort(key=_mat_emit_priority)

        # v0.14.6: 其他材料费(湖北 2024 单位=%)是比例行, 不是金额行.
        #   验证列 = Σ(材料分类中其他料/配行的验证列) × 其他材料费百分比/100
        #   预计算本子目其他料行验证列之和, 供 其他材料费 emit 使用.
        #   范围: category ∈ {料, 配} 且非 其他材料费 自身 (机/工/综/主材 不算,
        #   附项 is_appendix 也不计入 — 附项不是材料费构成).
        #   名称含 "【机械】" 后缀(如 "电【机械】"/"柴油【机械】")虽然是 category=料,
        #   但语义上是机械的燃料/动力消耗, 归入机械费而非材料费, 不计入基数.
        other_mat_basis = 0.0
        for mat in materials:
            if mat.get("is_other_material"):
                continue
            if mat["category"] not in ("料", "配"):
                continue
            if mat.get("is_proportion") or mat.get("is_appendix"):
                # 比例行(配, 如黄土 price=—)验证列为空、不参与 sub_verifications,
                #   不能把数量当验证值计入其他材料费的基数
                continue
            if "【机械】" in mat["name"] or "【机】" in mat["name"]:
                # 燃料/动力消耗, 属机械费, 不计入其他材料费基数
                continue
            bq = strip_numeric_brackets(mat["quantities"].get(project_cols[j], ""))
            bp = strip_numeric_brackets(mat["price"])
            bqty_f = to_float(bq)
            bprice_f = to_float(bp)
            if bqty_f == 0.0:
                bv = 0.0
            elif bprice_f == 0.0:
                bv = bqty_f
            else:
                bv = bqty_f * bprice_f
            other_mat_basis += bv

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
                # v0.14.5: 其他材料费验证列 = 其他料行验证列之和 × 百分比/100
                #   基价已在 parse_material_row 置空 (price="")
                v = other_mat_basis * qty_f / 100.0
                v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            else:
                if qty_f == 0.0:
                    v = 0.0
                elif price_f == 0.0:
                    v = qty_f
                else:
                    v = qty_f * price_f
                v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            # v0.13.13: 附项不参与定行总验证 (不 append 到 sub_verifications)
            #   例: 余土外运 m³ 是工程量附加项, 不属于定额子目基价构成
            #   v_str 仍按 qty × price 计算 (保留 col 8 显示), 但定行 verify 不累加
            if not is_proportion and not mat.get("is_appendix"):
                sub_verifications.append(to_float(v_str))
            row_type = mat.get("category", "料")
            if row_type == "料" and is_proportion:
                row_type = "配"
            csv_rows.append([row_type, "", clean_latex_name(mat["name"]), "", mat["unit"], q, price, v_str, "", ""])

        # 机行（机械费）— hu: 下方有机分类则不 emit; 否则 emit（若值 > 0）
        # gd 用户指示: "机械费"自动行的 v_str 仅做显示, 不累加到 verify（避免和续表机具明细重复）
        # v0.12 全局判断: 如果 PID 在 pid_has_machine (任意表有"机"分类明细), 隐藏"机械费"自动行
        pid_machine_global = (pid_has_machine is not None
                              and any(p["id"] in pid_has_machine for p in projects))
        if ("machine" in cost_rows and not has_machine_below
                and not pid_machine_global and to_float(pmachine) > 0):
            v = to_float(pmachine)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["机", "", "机械费", "", "元", pmachine, "1.00", v_str, "", ""])

        # 综行（按实际存在；cq 多一项"一般风险费"，同企业管理费/利润一致计入 verify）
        if "management" in cost_rows:
            v = to_float(pmgmt)
            sub_verifications.append(v)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["综", "", "企业管理费", "", "元", "100.00", pmgmt, v_str, "", ""])
        # hu 湖北 2024: 2 个综行 (费用 + 增值税), 费用在前, 增值税在后
        #   - 湖北定额"费用"和"增值税"是全费用基价表专有, 不会在材料/机械分类下重复出现
        #   - 所以不需要 has_fee_below / has_vat_below 之类的判断, 直接 emit
        if "fee" in cost_rows:
            v = to_float(pfee)
            sub_verifications.append(v)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["综", "", "费用", "", "元", "100.00", pfee, v_str, "", ""])
        if "vat" in cost_rows:
            v = to_float(pvat)
            sub_verifications.append(v)
            v_str = f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            csv_rows.append(["综", "", "增值税", "", "元", "100.00", pvat, v_str, "", ""])
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
# 工作内容判定 (hu md 里定额前的固定标志, OCR 实测 100% 命中)
WORK_CONTENT_PREFIX = "工作内容"

def _is_work_content(line: str) -> bool:
    """判断一行是否以"工作内容"开头 (hu 定额前的固定标志).

    容忍空格 / 全半角冒号: `工作内容：` / `工作内容:` / `工作内容 :`
    """
    s = line.strip()
    return s.startswith(WORK_CONTENT_PREFIX)


def _next_nonempty_line(lines: list[str], start: int) -> str | None:
    """返回从 start 起的下一个非空行 (不含 start 行本身); 走完未找到 → None.

    用于"段行下方界定": 验证一个段行是否合法, 看它往下到下一个非空行是什么。
    """
    n = len(lines)
    j = start + 1
    while j < n:
        s = lines[j].strip()
        if s:
            return s
        j += 1
    return None


def _validate_section_downstream(lines: list[str], start: int,
                                  allowed_next: tuple[str, ...]) -> bool:
    """段行验证 (v0.14): 看下一个非空行是否属于 allowed_next.

    语义: 节/小节/小小节正则命中后, 必须验证"下一个非空行"是合法后续, 否则忽略.
        - 节:     allowed_next = (小节正则, 工作内容)
        - 小节:   allowed_next = (小小节正则, 工作内容)
        - 小小节: allowed_next = (工作内容,)

    返回:
      True  → 下一个非空行符合 allowed_next 之一 → emit
      False → 下一个非空行不是 allowed_next 中任何一类 (或没有下一个非空行) → 忽略

    注: 调用方传入 start (当前 emit 行 idx), 本函数从 start+1 开始找下一个非空行.
    """
    nxt = _next_nonempty_line(lines, start)
    if nxt is None:
        return False
    for pat in allowed_next:
        if pat.match(nxt):
            return True
    return False


# 段行验证用到的预编译正则: 小节 / 小小节 / 工作内容
_VALIDATE_SUBSECTION_RE = SECTION_ZH_SUBSECTION_RE
_VALIDATE_SUBSUBSECTION_RE = SECTION_ZH_SUBSUBSECTION_RE
_VALIDATE_SUBSUBSECTION_EMPTY_RE = SECTION_ZH_SUBSUBSECTION_EMPTY_RE

_WORK_CONTENT_RE = re.compile(r"^\s*工作内容")

# v0.14.3 空括号小小节 / 被吃编号的小节: 编号不可知 → 固定占位 X, 留人工审核
_NUM_MISSING = "X"

# 句读结尾: 正文/说明句子以句读收尾; 标题行一般不收尾
_SENTENCE_END_RE = re.compile(r"[。；，、：:．·]$")


def _has_work_content_within(lines: list[str], start: int,
                              max_nonempty: int = 5) -> bool:
    """v0.14.3 节验证: 从 start+1 往下扫最多 max_nonempty 个非空行,
    只要有以"工作内容"开头的行 → True; 否则 False.

    用户明确: 节 = 中文数字+顿号, 为防误识别, 只认"下方 5 个非空行内有工作内容"
    的才归为节; 没有的忽略. 窗口内不足 5 个非空行走完即止.
    """
    count = 0
    n = len(lines)
    j = start + 1
    while j < n:
        s = lines[j].strip()
        if s:
            count += 1
            if _WORK_CONTENT_RE.match(s):
                return True
            if count >= max_nonempty:
                return False
        j += 1
    return False


def _is_plausible_subsection_title(s: str) -> bool:
    """v0.14.3 判据: 一行是否像"被 OCR 吃掉编号的小节标题".

    保守, 避免把正文/说明误认成小节:
      - 不是任何段行正则 (章/节/小节/小小节)
      - 不以"工作内容"开头
      - 不含 <table / "计量单位" (这些行在小节之下, 不是小节本身)
      - 不以句读结尾 (正文/说明句子以句读收尾, 标题一般不收尾)
      - 不含 TOC 点线 (…)
      - 长度 0 < len ≤ 30
    """
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


def _find_eaten_subsection_title(lines: list[str], start: int) -> str | None:
    """v0.14.3 方案 A: 小小节要 emit 但没有小节上下文 (cur_subsection is None) 时,
    向上找最近一行非空行, 若它是"被 OCR 吃掉编号的小节标题"(如 `预制钢筋混凝土方桩`,
    原本是 `1. 预制钢筋混凝土方桩`) → 返回该标题; 调用方把它 emit 成小节(号=_NUM_MISSING).

    判据见 _is_plausible_subsection_title; 不满足 → 返回 None (调用方拒绝此小小节).
    """
    j = start - 1
    while j >= 0:
        s = lines[j].strip()
        if s:
            s = re.sub(_SECTION_PREFIX, "", s)  # 去 # 前缀, 与 line_disp 一致
            return s if _is_plausible_subsection_title(s) else None
        j -= 1
    return None


def extract_sections_before(text: str, volume: str,
                              cur_chapter: int | None = None,
                              cur_section: int | None = None,
                              cur_subsection: int | str | None = None
                              ) -> list[tuple[str, str]]:
    """hu 专用: 从文本中提取段标记, 返回 [(段行编码, 名称), ...].

    4 种标记 (按优先级匹配):
      1. "第X章  章名称"        → (volume.X, 章名称)
      2. "一、节名称"           → (volume.X.Y, 节名称)
      3. "1.小节名称"           → (volume.X.Y.Z, 小节名称)
      4. "(1)小小节名称" / "()小小节名称" (空括号 → 编号固定 _NUM_MISSING "X")

    数字规则:
      - 章号/节号: 中文数字 → 阿拉伯 (_cn_to_int)
      - 小节号/小小节号: ASCII 数字 (int())
      - 空括号 () / （） → 编号固定 "X" (G.X.Y.Z.X) 留作人工审核, 无末尾点
      - 转换失败 / 名称空 → 跳过该行

    下游验证 (防线 — 防止 OCR 把说明文字误识别成段行):
      - 章:     硬匹配 "第X章 章名称", 不验证 (本身识别度高, 兜底层级重置)
      - 节:     下方 5 个非空行内有以"工作内容"开头的行 → emit (v0.14.3 用户 v3)
      - 小节:   下方 (下一个非空行) 必须是小节 / 小小节 / 工作内容才 emit;
                **方案 B**: 无节上下文 (cur_section is None) → 拒绝, 不降级为节
      - 小小节: 下方 (下一个非空行) 必须是工作内容才 emit;
                无小节上下文时 (方案 A) 向上认"被吃编号的小节裸行"为小节,
                找不到 → 拒绝 (不落到节/章层)

    章节层级:
      - 4 层可缺, 缺时跳过该层
      - stateful 状态: 调用方传入 cur_chapter/cur_section/cur_subsection 作为初始值
        (跨 table prefix 调用时, 上一段的 state 应传入, 否则兜底会丢失层级)
      - 小节/被吃小节号可能是 "X" (字符串占位), 用 f-string 拼接, 不做 int()

    v3 fix: cur_chapter/cur_section/cur_subsection 由调用方传入 (process_md_file 维护),
            避免每次 table prefix 调用都从 None 开始, 跨 prefix 的小节 emit 编码错位
            (如 '## 10．地基注浆' 在第二次调用时 cur_section 丢成 None, emit 成 G.10 而非 G.2.2.10)
    """
    sections: list[tuple[str, str]] = []
    lines = text.splitlines()
    n = len(lines)

    # v0.13.x 智能去重: 章后紧跟另一个章 (中间无节/小节/小小节) → 当前章是 TOC 目录条目, skip.
    #   - TOC 列表: ## 第一章 …  ## 第二章 …  ## … (章紧挨着章, 没有节/小节/小小节)
    #   - 真章节: ## 第一章 …  ## 一、xx (节) 或 ## 1．xx (小节) 或 ## (1)xx (小小节)
    #   实现: 先扫一遍 lines, 标记每行的"层级级别" (章=1 / 节=2 / 小节=3 / 小小节=4),
    #         emit 章时, 如果该行 +1 到 +5 行都是"章", 则视为 TOC 条目 skip.
    def _classify_line(line: str) -> int:
        if SECTION_ZH_CHAPTER_RE.match(line):
            return 1
        if SECTION_ZH_SECTION_RE.match(line):
            return 2
        if SECTION_ZH_SUBSECTION_RE.match(line):
            return 3
        if SECTION_ZH_SUBSUBSECTION_RE.match(line) or SECTION_ZH_SUBSUBSECTION_EMPTY_RE.match(line):
            return 4
        return 0  # 普通行 (含 目录 heading, ## 目 录 等)

    line_level: list[int] = [_classify_line(lines[k].strip()) for k in range(n)]

    def _is_chapter_in_toc(idx: int) -> bool:
        """判断 idx 处的章标题是否为目录条目 (TOC entry).
        启发 (v0.13.x 修正): TOC 章节列表中, 下一个章出现前**没有任何 <table>** (目录条目不带表);
        正文真章节中, 下一个章出现前**必然有 <table>** (因为本章节有定额表).
        边角 (v0.13.x+): 如果 idx 是 prefix 里的**最后一个**章 → 它指向的表在 prefix 之外,
          一定不能当 TOC skip, 无条件 emit. (否则会误把真章 G.1 漏掉)
        因此: 找下一个章 idx2; 若 idx 是最后一个章 → 返回 False (emit);
              否则查 idx+1 ~ idx2-1 是否有 <table → 是: 真章节 (emit); 否: TOC (skip).
        """
        # 找下一个章 (或文末)
        next_chapter_idx = n
        for j in range(idx + 1, n):
            if line_level[j] == 1:
                next_chapter_idx = j
                break

        # 边角: idx 是 prefix 里最后一个章 → 表在 prefix 之外, 必然 emit
        if next_chapter_idx == n:
            return False

        # 在 [idx+1, next_chapter_idx) 范围内查找 <table>
        for j in range(idx + 1, next_chapter_idx):
            if "<table" in lines[j]:
                return False  # 中间有表 → 真章节
        return True  # 中间无表 → TOC 条目

    i = 0
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # 显示标题 (v0.14.3): 去掉行首 markdown # 前缀 (OCR 对 # 输出不一致, 不算内容),
        # 但**保留数字/括号标记** — 小节 `3．xxx` / 小小节 `（1）xxx` 原样保留,
        #   供人工核对原始编号 (空括号 `（ ）` 必须可见).
        line_disp = re.sub(_SECTION_PREFIX, "", line)

        # 0. 跳过 TOC 尾行 (带页码点) — 已在 4 个正则之前
        if TOC_PAGE_TAIL.search(line):
            i += 1
            continue

        # === 1. 第X章 (最高优先级, 重置下游层级) ===
        m = SECTION_ZH_CHAPTER_RE.match(line)
        if m:
            cn_num = m.group(1)
            title = m.group(2).strip()
            n_int = _cn_to_int(cn_num)
            if n_int is not None and title:
                if not _is_chapter_in_toc(i):  # 跳过 TOC 条目
                    sections.append((f"{volume}.{n_int}", title))
                    cur_chapter = n_int
                    cur_section = None
                    cur_subsection = None
            i += 1
            continue

        # === 2. 一、节名称 ===
        # v0.14.3 验证 (用户分层方案 v3): 下方 5 个非空行内有以"工作内容"开头
        #   的行 → emit 节; 没有 → 忽略 (防 OCR 把说明文字误识别成节).
        #   实测: 计算规则里的假节 `## 一、打桩。` 下方 5 行内无工作内容 → 被拒.
        #   真节 `## 一、打桩` 下方第 3 个非空行就是 工作内容 → emit.
        m = SECTION_ZH_SECTION_RE.match(line)
        if m:
            cn_num = m.group(1)
            title = line_disp
            n_int = _cn_to_int(cn_num)
            if n_int is not None and title:
                if _has_work_content_within(lines, i):
                    if cur_chapter is None:
                        # 兜底: 没识别到章, 把此节号当章号
                        sections.append((f"{volume}.{n_int}", title))
                        cur_chapter = n_int
                    else:
                        sections.append((f"{volume}.{cur_chapter}.{n_int}", title))
                        cur_section = n_int
                        cur_subsection = None
            i += 1
            continue

        # === 3. 1.小节名称 ===
        # v0.14.3 验证 (用户分层方案 v3): 下一个非空行必须是"小节标题"(同级)
        #   "小小节标题"(降一级) 或 "工作内容"(定额, 小节可能直接跟定额) 才 emit.
        #   不能接节 (节是上一层级, 跨章已处理).
        #   **方案 B**: cur_section is None (没有节上下文) → 拒绝, 不降级为节.
        #   (修复 v0.14.2 G.3.4 误判: `## 4．钢管桩` 曾因节被拒而降级成节 G.3.4)
        m = SECTION_ZH_SUBSECTION_RE.match(line)
        if m:
            num = m.group(1)
            title = line_disp
            if num.isdigit() and title:
                if _validate_section_downstream(
                    lines, i,
                    allowed_next=(_VALIDATE_SUBSECTION_RE,
                                  _VALIDATE_SUBSUBSECTION_RE,
                                  _VALIDATE_SUBSUBSECTION_EMPTY_RE,
                                  _WORK_CONTENT_RE),
                ):
                    if cur_section is None:
                        i += 1
                        continue
                    n_int = int(num)
                    sections.append((f"{volume}.{cur_chapter}.{cur_section}.{n_int}", title))
                    cur_subsection = n_int
            i += 1
            continue

        # === 4. (1)小小节名称 / ()小小节名称 ===
        # v0.14.3 验证: 下一个非空行必须是"工作内容"才 emit
        # 编码: 正常 → G.X.Y.Z.W; 空括号 → W = _NUM_MISSING("X")
        # 方案 A: cur_subsection is None (没有小节上下文) 时, 向上找"被 OCR 吃掉
        #   编号的小节裸行"认作小节 (号 = X), 再 emit 此小小节; 找不到 → 拒绝
        #   (小小节永远 4 层, 不落到节/章层).
        # 继承: 父小节号是 X (被吃小节) → 小小节号也固定 X (编号不可靠, 统一留审).
        m = SECTION_ZH_SUBSUBSECTION_RE.match(line)
        if m:
            num_str = m.group(1)
            title = line_disp
            if title:
                if _validate_section_downstream(
                    lines, i,
                    allowed_next=(_WORK_CONTENT_RE,),
                ):
                    if num_str == "" or not num_str.isdigit():
                        n_int: int | str = _NUM_MISSING
                    else:
                        n_int = int(num_str)

                    if cur_subsection is None:
                        # 方案 A: 向上认被吃小节裸行
                        if cur_section is None:
                            # 无节上下文 → 拒绝 (方案 B 同理, 不落到节/章层)
                            i += 1
                            continue
                        eaten = _find_eaten_subsection_title(lines, i)
                        if eaten is None:
                            i += 1
                            continue
                        sections.append(
                            (f"{volume}.{cur_chapter}.{cur_section}.{_NUM_MISSING}",
                             eaten))
                        cur_subsection = _NUM_MISSING

                    n_disp = _NUM_MISSING if cur_subsection == _NUM_MISSING else n_int
                    code = f"{volume}.{cur_chapter}.{cur_section}.{cur_subsection}.{n_disp}"
                    sections.append((code, title))
            i += 1
            continue

        # === 5. 被吃小节裸行直接跟定额 (v0.14.3 方案 A 补充) ===
        #   例: `预制钢筋混凝土板桩` 原本是 `2. 预制钢筋混凝土板桩`, 编号被 OCR 吃掉,
        #   下面直接是 工作内容 + 定额 (没有小小节). 编码: G.X.Y.X.
        #   需 cur_section 已存在 (方案 B); 下一非空行必须是 工作内容;
        #   且本行满足 _is_plausible_subsection_title (保守, 不误认说明文字).
        if cur_section is not None and _is_plausible_subsection_title(line_disp):
            if _validate_section_downstream(
                lines, i,
                allowed_next=(_WORK_CONTENT_RE,),
            ):
                sections.append(
                    (f"{volume}.{cur_chapter}.{cur_section}.{_NUM_MISSING}",
                     line_disp))
                cur_subsection = _NUM_MISSING

        i += 1

    return sections


# ---------- 单文件处理 ----------
def process_md_file(md_path: Path) -> tuple[list[list[str]], list[dict]]:
    content = md_path.read_text(encoding="utf-8")

    # 找到所有 table 的位置
    table_matches = list(re.finditer(r"<table[^>]*>.*?</table>", content, flags=re.S))

    # hu 专用: 从首张 table 提取册号 (定额编号的字母部分, 本册固定)
    volume = ""
    for m in table_matches:
        grid, total_cols, raw_rows, cell_cols = parse_table(m.group(0))
        if not grid or total_cols < 4 or not raw_rows:
            continue
        projects_scan = find_projects(grid, total_cols)
        if projects_scan:
            volume = projects_scan[0]["id"][0]  # 第一个定额编号的首字母
            break

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
    # hu 段行是 stateful process (process_md_file 内维护 cur_chapter/cur_section/...)
    #   4 种标记按出现顺序累加, 跨 table 继续维护
    cur_chapter: int | None = None
    cur_section: int | None = None
    cur_subsection: int | None = None
    # v0.13.x (用户反馈): 移除 body_emitted 全局去重 — 每个 table 的 prefix 单独 emit 段行,
    #   段行会出现在对应 table 的"前面" (而非一次性 emit 到 CSV 顶部).
    #   这样每个定额表的章节定位是正确的 (跟着 table 走), 而不是集中放在 CSV 开头.

    for table_idx, m in enumerate(table_matches):
        table_html = m.group(0)
        table_start = m.start()
        prefix = content[prev_end:table_start]

        # hu 专用: 提取本段 prefix 中的段标记 (传入 volume 册号 + 上一段 state)
        secs = extract_sections_before(prefix, volume,
                                        cur_chapter=cur_chapter,
                                        cur_section=cur_section,
                                        cur_subsection=cur_subsection)

        # hu 段行 emit (v0.13.x 用户反馈: 移除 body_emitted 全局去重,
        #   每个 table 的 prefix 单独 emit 全部匹配的段行, 段行会出现在对应 table 前面)
        for sec_id, sec_name in secs:
            all_rows.append(["段", sec_id, clean_latex_name(sec_name), "", "", "", "", "", "", ""])
            last_section_id = sec_id
            # 同步累加 cur_chapter/cur_section/cur_subsection (用于下个段行兜底)
            parts = sec_id.split(".")
            if len(parts) == 2 and sec_id != volume:  # volume.X = 章 (排除只有 volume 自身)
                cur_chapter = int(parts[1])
                cur_section = None
                cur_subsection = None
            elif len(parts) == 3:  # volume.X.Y = 节
                cur_section = int(parts[2])
                cur_subsection = None
            elif len(parts) == 4:  # volume.X.Y.Z = 小节 (Z 可能是被吃小节的 X)
                cur_subsection = parts[3] if parts[3].isdigit() else _NUM_MISSING
            # 5 段 (小小节) 不需要维护, 见 sec_id 自身的 5 段

        # 提取工作内容
        # 提取工作内容（支持多行，结束于"单位："或<table）
        wc_match = None
        for wc_m in re.finditer(r"工作内容[：:]\s*", prefix):
            wc_match = wc_m

        if wc_match:
            rest = prefix[wc_match.end():]
            # 2026-08-17 修复：湖北 OCR 输出"计量单位："（4 字前缀），旧正则 `单位[：:]`
            # 会匹配 `计量单位` 中的子串 `单位`，导致工作内容末尾残留"计量"二字。
            # 修复策略：先尝试"计量单位"（含 OCR 抖动空白），回退到普通"单位"，再回退 <table。
            # \s* 严格只容忍空白字符，不容忍其他字符。
            end_match = (
                re.search(r"计\s*量\s*单位\s*[：:]", rest)
                or re.search(r"(?<![计\s])\b单位\s*[：:]", rest)
                or re.search(r"<table", rest)
            )
            wc_text = rest[:end_match.start()] if end_match else rest
            wc_text = wc_text.strip()
            wc_text = re.sub(r"\s+", " ", wc_text)
        else:
            wc_text = ""

        # 提取单位 fallback（与工作内容结束符对称：先吞"计量单位"，再回退"单位"）
        unit_matches = (
            list(re.finditer(r"计\s*量\s*单位\s*[：:]\s*([^\n]+)", prefix))
            or list(re.finditer(r"(?<![计\s])\b单位\s*[：:]\s*([^\n]+)", prefix))
        )
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