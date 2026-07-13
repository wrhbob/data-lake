"""定额表解析 (核心模块) — 从 parse_dinge.py §5 演进.

将 PP-StructureV3 输出的 HTML 表格转为结构化 ParsedQuotaItem 列表。
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ── 数据模型 ──

@dataclass
class ParsedResource:
    """解析出的人材机消耗行 (纯数据, 非 ORM)."""
    category: str       # 人工 | 材料 | 机械
    name: str           # 如: 混凝土工
    spec: str = ""      # 规格型号
    unit: str = ""      # 工日 / kg / m³ ...
    price: float = None
    quantity: float = None


@dataclass
class ParsedQuotaItem:
    """一个解析完的定额子目 (纯数据, 非 ORM)."""
    code: str
    name: str
    name_parts: list = field(default_factory=list)
    unit: str = ""
    base_price: float = None   # qa_printed_price.base
    labor_cost: float = None   # qa_printed_price.labor
    material_cost: float = None  # qa_printed_price.material
    machine_cost: float = None  # qa_printed_price.machine
    work_content: str = ""
    note: str = ""
    chapter_path: list = field(default_factory=list)
    list_code_prefix: str = ""
    page: int = 0
    resources: list = field(default_factory=list)


@dataclass
class TableContext:
    """解析上下文 — 跨页传递."""
    chapter_path: list = field(default_factory=list)
    list_code_prefix: str = ""
    work_content: str = ""
    page: int = 0
    unit_global: str = ""
    continued_from_previous: bool = False


# ── 正则 / 常量 ──

DEFAULT_CODE_PATTERN = re.compile(r"^[A-Z]{2}\d{4}$")
RE_UNIT_HDR = re.compile(r"(?:计量)?单位\s*[::]\s*(\S+)")
RE_WORK = re.compile(r"工作内容\s*[::]\s*(.+)")
RE_NOTE = re.compile(r"^注\s*[::]\s*(.+)")

# 默认水印字符 (省级 Profile 可覆盖)
_WATERMARK_DEFAULT = set("四川省住房和城乡建设厅信息公开浏览专用")


def clean(s: str, watermarks: set = None) -> str:
    """清洗文本: 去空白 / 过滤水印碎片."""
    wm = watermarks or _WATERMARK_DEFAULT
    s = re.sub(r"\s+", "", s or "")
    if s and all(c in wm for c in s):
        return ""
    return s


def parse_number(s: str):
    """提取字符串中的数字, 返回 float 或 None."""
    s = (s or "").replace(",", "").replace(" ", "").replace("—", "").replace("-", "")
    # 保留小数点但不接受仅含减号的字符串
    if s == "" or s == ".":
        return None
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


# ── HTML 表格 → 2D 网格 ──

def html_table_to_grid(html: str) -> list:
    """PP-Structure HTML 表格 → 二维网格 (展开合并单元格).

    值向右/向下填充 rowspan/colspan 占位.
    从 parse_dinge.py 第 126-168 行零改动提取.
    """
    from html.parser import HTMLParser

    class TP(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.cur = None
            self.cell = None
            self.rs, self.cs = 1, 1

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "tr":
                self.cur = []
            elif tag in ("td", "th"):
                self.cell = ""
                self.rs = int(a.get("rowspan", 1))
                self.cs = int(a.get("colspan", 1))

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cur is not None:
                self.cur.append((self.cell.strip(), self.rs, self.cs))
                self.cell = None
            elif tag == "tr" and self.cur is not None:
                self.rows.append(self.cur)
                self.cur = None

        def handle_data(self, d):
            if self.cell is not None:
                self.cell += d

    tp = TP()
    tp.feed(html)

    grid = []
    spans = {}  # {(r, c): value} — 由上方 rowspan 占位
    for r, row in enumerate(tp.rows):
        out = []
        c = 0
        for val, rs, cs in row:
            while (r, c) in spans:
                out.append(spans.pop((r, c)))
                c += 1
            for dc in range(cs):
                out.append(val)
                for dr in range(1, rs):
                    spans[(r + dr, c)] = val
                c += 1
        while (r, c) in spans:
            out.append(spans.pop((r, c)))
            c += 1
        grid.append(out)
    return grid


# ── 编号行检测 ──

def detect_code_row(
    grid: list,
    code_pattern=DEFAULT_CODE_PATTERN,
    code_header_keywords=None,
    continued_from_previous: bool = False,
) -> Optional[int]:
    """三规则检测编号行索引.

    1. 行首含"定额编号"/"编号"字样 且 至少命中 1 个 code → 编号行
    2. 一行命中 ≥2 个 code → 编号行
    3. 命中 1 个 code 且上页表格未闭合 → 编号行

    Returns:
        编号行索引, 或 None.
    """
    if code_header_keywords is None:
        code_header_keywords = ["定额编号", "编号"]

    for ri, row in enumerate(grid):
        codes = [c for c in (clean(x) for x in row) if code_pattern.match(c)]
        row_text = clean("".join(row))

        # 规则 1: 含编号表头 + 至少 1 个 code
        if any(kw in row_text for kw in code_header_keywords) and len(codes) >= 1:
            return ri

        # 规则 2: ≥2 个 code
        if len(codes) >= 2:
            return ri

        # 规则 3: 1 个 code + 上页表格未闭合
        if len(codes) == 1 and continued_from_previous:
            return ri

    return None


# ── 核心解析 ──

def parse_quota_table(
    grid: list,
    context: TableContext,
    code_pattern=DEFAULT_CODE_PATTERN,
    code_header_keywords=None,
    watermarks=None,
) -> List[ParsedQuotaItem]:
    """把一个定额表网格解析成若干 ParsedQuotaItem.

    Args:
        grid: html_table_to_grid 输出的 2D 列表.
        context: 当前解析上下文 (章节路径, 工作内容, 页码等).
        code_pattern: 定额编号正则.
        code_header_keywords: 编号行表头关键词.
        watermarks: 水印字符集.

    Returns:
        list[ParsedQuotaItem].
    """
    if code_header_keywords is None:
        code_header_keywords = ["定额编号", "编号"]

    # 找编号行
    code_row = detect_code_row(
        grid, code_pattern, code_header_keywords, context.continued_from_previous
    )
    if code_row is None:
        return []

    header = [clean(x, watermarks) for x in grid[code_row]]
    col_of = {}  # 列索引 → 定额编号
    for ci, v in enumerate(header):
        if code_pattern.match(v):
            col_of[ci] = v

    items = {}
    for code in col_of.values():
        items[code] = ParsedQuotaItem(
            code=code,
            name="",
            work_content=context.work_content,
            chapter_path=list(context.chapter_path),
            list_code_prefix=context.list_code_prefix,
            page=context.page,
        )

    # ── 行分类游标 ──
    unit_global = context.unit_global
    name_rows, price_rows, res_rows = [], [], []
    section = "name"
    for ri in range(code_row + 1, len(grid)):
        row = grid[ri]
        joined = clean("".join(row), watermarks)
        first = clean(row[0], watermarks) if row else ""

        if "基价" in first or ("基价" in joined and parse_number("".join(row[1:]))):
            section = "price"
            price_rows.append(ri)
            continue

        if section == "price" and re.search(
            r"(人工费|材料费|机械费|综合基价)",
            first + clean(row[1], watermarks) if len(row) > 1 else "",
        ):
            price_rows.append(ri)
            continue

        if re.match(r"^(人工|材料|机械)$", first) or (
            section in ("price", "res") and len(row) >= 4
        ):
            section = "res"
            res_rows.append(ri)
            continue

        if section == "name":
            name_rows.append(ri)

    # ── 项目名称: 纵向逐级拼接 ──
    for ci, code in col_of.items():
        parts = []
        for ri in name_rows:
            if ci < len(grid[ri]):
                v = clean(grid[ri][ci], watermarks)
                v = re.sub(r"^项目$", "", v)
                mu = RE_UNIT_HDR.search(v)
                if mu:
                    items[code].unit = mu.group(1)
                    v = RE_UNIT_HDR.sub("", v)
                if v and v not in parts:
                    parts.append(v)
        it = items[code]
        it.name_parts = parts
        it.name = " ".join(parts)
        if not it.unit:
            it.unit = unit_global

    # ── 基价 / 三费 → qa_printed_price ──
    for ri in price_rows:
        row = grid[ri]
        label = clean(row[0], watermarks) + clean(
            row[1], watermarks
        ) if len(row) > 1 else clean(row[0], watermarks)
        for ci, code in col_of.items():
            val = parse_number(row[ci]) if ci < len(row) else None
            it = items[code]
            if "基价" in label:
                it.base_price = val
            elif "人工费" in label:
                it.labor_cost = val
            elif "材料费" in label:
                it.material_cost = val
            elif "机械费" in label:
                it.machine_cost = val

    # ── 人材机消耗矩阵 ──
    cur_cat = ""
    for ri in res_rows:
        row = [clean(x, watermarks) for x in grid[ri]]
        if not any(row):
            continue
        if row[0] in ("人工", "材料", "机械"):
            cur_cat = row[0]

        # 结构: [类别] 名称 单位 单价 q1 q2 ...
        cells = row[1:] if row[0] in ("人工", "材料", "机械", "") else row
        if len(cells) < 3:
            continue
        name, unit, price_val = cells[0], cells[1], parse_number(cells[2])
        if not name or name in ("名称", "单位"):
            continue

        offset = len(row) - len(cells)
        for ci, code in col_of.items():
            idx = ci
            q = (
                parse_number(row[idx])
                if idx < len(row) and idx >= offset + 3
                else None
            )
            if q is not None:
                items[code].resources.append(
                    ParsedResource(
                        category=cur_cat or "材料",
                        name=name,
                        unit=unit,
                        price=price_val,
                        quantity=q,
                    )
                )

    return list(items.values())
