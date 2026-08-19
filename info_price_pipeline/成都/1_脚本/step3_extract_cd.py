"""step3_extract.py - 字段提取 + 跨页合并 + PC 拆行 + LaTeX 清洗

目的：
  classified.json → extract.json

输出：
  3_中间产物/{city}_{period}_ocr/extract.json
  [
    {
      "section": "MARKET" | "PC",
      "page": int,
      "district": "" | 区县,  # MARKET 才有
      "header": [...],         # 原始 header
      "rows": [
        {
          "材料名称": "HPB300高线",
          "规格型号": "Φ6",
          "产地": "各地综合",
          "单位": "t",
          "除税价格(元)": "3439.82",
          "含税价(元)": "",
        },
        ...
      ]
    }
  ]

核心逻辑：
  1. 跨页合并：同章节 + 跨页 + 列数相同 → 合并 rows
  2. PC 拆行：检测「运输费调整」列 → 拆 2 行（到场价/运输费调整）
  3. LaTeX 清洗：$m^{2}$ → m², $m^{3}$ → m³
  4. 粗体/斜体 strip：<b>...</b> → ...
"""
import json
import re
import sys
from pathlib import Path
import html as _html  # 2026-07-30 同步北京 P0-1：HTML 实体反转义（&lt; → < 等）

# 2026-08-10 增：LaTeX 上标映射 (北京 P0-1 同步)，clean_cell 用
UNICODE_SUPERSCRIPT = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}

# Windows GBK 编码修复：强制 stdout 用 utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ROOT


def load_city_yaml(city):
    """读 6_配置/城市模板/{city}.yaml 或 {city}市.yaml"""
    import yaml
    candidates = [
        ROOT / "6_配置" / "城市模板" / f"{city}.yaml",
        ROOT / "6_配置" / "城市模板" / f"{city}市.yaml",
    ]
    for path in candidates:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError(f"未找到城市模板: {candidates[0]} 或 {candidates[1]}")


# ============================================================
# 文本清洗
# ============================================================

def clean_cell(text):
    """LaTeX + 粗体/斜体 + 空白 strip + HTML 实体反转义（2026-07-30 同步北京 P0-1）

    2026-07-30 同步 P0-1：OCR 把 `<` 转 `&lt;`、`>` 转 `&gt;` → 反转义
    """
    if not text:
        return ""
    text = str(text)
    # 2026-07-30 同步北京 P0-1：HTML 实体反转义
    text = _html.unescape(text)
    # 粗体/斜体 strip
    text = re.sub(r"<(b|i|u|strong|em)>(.*?)</\1>", r"\2", text, flags=re.DOTALL)
    text = re.sub(r"</?(b|i|u|strong|em)>", "", text)
    # LaTeX 公式
    text = re.sub(r"\$\s*m\s*\^\s*\{\s*2\s*\}\s*\$", "m²", text)
    text = re.sub(r"\$\s*m\s*\^\s*\{\s*3\s*\}\s*\$", "m³", text)
    text = re.sub(r"\$\s*m\s*\^\s*2\s*\$", "m²", text)
    text = re.sub(r"\$\s*m\s*\^\s*3\s*\$", "m³", text)
    text = re.sub(r"\$\s*\\?cdot\s*\$", "·", text)
    # OCR 残段：m2/m3 单独出现（PC 单位列常见）
    text = re.sub(r"\bm\s*\^\s*\{\s*2\s*\}\b", "m²", text)
    text = re.sub(r"\bm\s*\^\s*\{\s*3\s*\}\b", "m³", text)
    text = re.sub(r"^m\s*\^\s*2\s*$", "m²", text)
    text = re.sub(r"^m\s*\^\s*3\s*$", "m³", text)
    # 2026-08-10 增：覆盖 \mathrm{m}^{N} / \text{m}^{N} / \mathrm m^{N} / {\mathrm m}^{N} 等 LaTeX 单位变种
    # 关键陷阱：\mathrm 是字体命令，m 前后空格不终止 (LaTeX 语义)；用 \s* 覆盖
    # 关键顺序：外层花括号版 (3/4) 必须先跑，否则无外层 regex 1 会抢匹配 → 外层 { 残留
    _latex_m_repl = lambda m: f"m{UNICODE_SUPERSCRIPT.get(m.group(1), '^'+m.group(1))}"
    text = re.sub(r"\{\s*\\mathrm\s*\{?\s*m\s*\}?\s*\}\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, text)
    text = re.sub(r"\{\s*\\text\s*\{?\s*m\s*\}?\s*\}\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, text)
    text = re.sub(r"\\mathrm\s*\{?\s*m\s*\}?\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, text)
    text = re.sub(r"\\text\s*\{?\s*m\s*\}?\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, text)
    # LaTeX 数学符号（规格列常见）：\Phi → Φ、\times → ×、\cdot → ·
    text = re.sub(r"\\Phi\b", "Φ", text)
    text = re.sub(r"\\times\b", "×", text)
    text = re.sub(r"\\cdot\b", "·", text)
    text = re.sub(r"\\pm\b", "±", text)
    text = re.sub(r"\\le\b", "≤", text)
    text = re.sub(r"\\ge\b", "≥", text)
    text = re.sub(r"\\ne\b", "≠", text)
    # 移除残留反斜杠 + 清理 $\Phi 75 \times 2.3$ → Φ75×2.3
    text = re.sub(r"\$\\?([^$]*?)\$", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    # OCR 错（m²/m³ 被 OCR 成 m2/m3）：PC 单位列常见，整词匹配
    text = re.sub(r"\bm2\b", "m²", text)
    text = re.sub(r"\bm3\b", "m³", text)
    # 2026-08-11 增: 英寸清洗 (复广州 _clean_inch, 范围最广覆盖所有 LaTeX/HTML 变种)
    #   $\frac{1}{2}"$ → 1/2"      LaTeX 分数英寸
    #   21⁄2&quot;     → 21/2"     Unicode 分数 + HTML 实体
    #   2&quot;        → 2"        整数英寸
    text = re.sub(r"\\?frac\{(\d+)\}\{(\d+)\}", r"\1/\2", text)  # \\? 容错: 前面 $..$ 移除会吃掉前导 \
    text = text.replace("⁄", "/")
    text = _html.unescape(text)
    text = text.replace("$", "")
    # 空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ============================================================
# HTML 解析
# ============================================================

def parse_md_tables(md_content):
    """md_content 全文 HTML 流 → 提取所有 <table>...</table> 块

    2026-07-29 改：表数据源从 content_list[i].table_body 切到 md_content
      - 原因：MinerU 对多 section 合并表的列检测会漏行（如 idx=1303 DRPO 被吞 21 行）
      - md_content 是 MinerU 全文 markdown dump，table 块完整无截断
      - 实测 cd_06：md_content 113 张表 == content_list 113 张非空表，1:1 顺序对应

    返回 list[dict]: [{"html": ..., "sig1": ..., "sig2": ...}, ...]
      sig1/sig2 = 第 1/2 行签名（cells 拼接），供 match_md_table 匹配
    """
    tables = []
    pos = 0
    while True:
        start = md_content.find("<table>", pos)
        if start < 0:
            break
        end = md_content.find("</table>", start)
        if end < 0:
            break
        html = md_content[start:end + 8]
        tables.append({
            "html": html,
            "sig1": _first_row_signature(html),
            "sig2": _second_row_signature(html),
        })
        pos = end + 8
    return tables


def _first_row_signature(html):
    """提取 <table> 第 1 行的 cell 文本签名"""
    m = re.search(r"<tr>(.*?)</tr>", html, re.DOTALL)
    if not m:
        return ""
    cells = re.findall(r"<td[^>]*>([^<]*)</td>", m.group(1))
    return " ".join(cells).strip()


def _second_row_signature(html):
    """提取 <table> 第 2 行的 cell 文本签名"""
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
    if len(rows) < 2:
        return ""
    cells = re.findall(r"<td[^>]*>([^<]*)</td>", rows[1])
    return " ".join(cells).strip()


def match_md_table(content_list_body, md_tables, used_md_indices):
    """为 content_list[i].table_body 找匹配的 md_content table

    匹配策略（2026-07-29 改）：
      1. 第 1 行签名严格相等 → 用最早的未占用 md_idx
      2. 多匹配 → 用第 2 行签名严格相等筛
      3. 仍多匹配 → 用"包含"关系筛（第 1 行签名出现在 md sig1 中）
      4. 全失败 → 返回 None（fallback 用 table_body）

    关键：used_md_indices 跟踪已分配的 md_idx，避免重复占用
    """
    sig1 = _first_row_signature(content_list_body)
    sig2 = _second_row_signature(content_list_body)
    if not sig1:
        return None

    # 1. sig1 严格相等 + 未占用
    candidates = [i for i, t in enumerate(md_tables) if t["sig1"] == sig1 and i not in used_md_indices]
    # 2. sig2 严格相等筛
    if len(candidates) > 1 and sig2:
        candidates2 = [c for c in candidates if md_tables[c]["sig2"] == sig2]
        if candidates2:
            candidates = candidates2
    # 3. 包含筛
    if len(candidates) > 1:
        candidates2 = [c for c in candidates if sig1 in md_tables[c]["sig1"]]
        if candidates2:
            candidates = candidates2
    if not candidates:
        return None
    return candidates[0]


def parse_html_table(html):
    """<table><tr><td>...</td></tr>...</table> → [{col1, col2, ...}, ...]

    v1 简化：不展开 colspan/rowspan（colspan="N" 只取 1 列作为标签）
    原因（2026-07-27）：展开 colspan 让 header 比数据多空列，导致 spec/unit/rebar 列错位
    """
    rows = []
    tr_pattern = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
    td_pattern = re.compile(r"<td([^>]*)>(.*?)</td>", re.DOTALL)

    for tr in tr_pattern.findall(html):
        cells = []
        for attrs, content in td_pattern.findall(tr):
            cleaned = clean_cell(content)
            cells.append(cleaned)
        rows.append(cells)
    return rows


def same_columns(rows1, rows2):
    """两表表头是否同结构（列数相同）"""
    if not rows1 or not rows2:
        return False
    h1 = rows1[0] if rows1 else []
    h2 = rows2[0] if rows2 else []
    return len(h1) == len(h2) and len(h1) > 0


def split_2d_table(rows):
    """二维拼接表硬切（按列名重复检测，替代奇偶+mid 对齐）

    切分依据（2026-07-27 改）：
      合并前 3 行作为虚拟表头 → 找第一个重复列名 → 切分点 = 重复位置

    适用（成都 06 实测）：
      - p178 8 列 4+4 拼接（hdr '材料名称' 重复）
      - p183 12 列 6+6（'厚度规格' 在 row[1] 重复）
      - p208 14 列管件表（'规格' 在 col 1/8 重复）
      - p209 8 列（'材料名称' 重复）
      - p216/p220 11/15 列（左右表不等宽）

    不适用：
      - 列名不重复的单表 → 不切，raw_cells 兜底
    """
    if not rows:
        return rows, None
    n_cols = max(len(r) for r in rows)
    if n_cols < 6:
        return rows, None  # 太窄不切

    # 合并前 3 行作为虚拟表头（处理多行表头）
    merged = ["" for _ in range(n_cols)]
    for r in rows[:3]:
        for i, cell in enumerate(r):
            if i < n_cols and cell and cell.strip():
                if not merged[i]:  # 只填空白位置（避免覆盖已有内容）
                    merged[i] = clean_cell(cell)

    # 找第一个重复列名 = 切分点
    seen = {}
    split_at = -1
    for i, col_name in enumerate(merged):
        col_name = col_name.strip()
        if not col_name:
            continue
        if col_name in seen:
            split_at = i
            break
        seen[col_name] = i

    if split_at <= 1:
        return rows, None  # 切分点太靠前（< 2 列）或不切

    # 切分：左 = [0, split_at), 右 = [split_at, n_cols)
    left = [r[:split_at] for r in rows]
    right = [r[split_at:n_cols] for r in rows]

    # 补列：子表 < 5 列 → 在 index=2 插空（让 extract_market_table 走 5 列路径）
    for half in (left, right):
        half_n = max(len(r) for r in half) if half else 0
        if 0 < half_n < 5:
            for r in half:
                while len(r) < 5:
                    if len(r) >= 2:
                        r.insert(2, "")
                    else:
                        r.append("")
    return left, right


# ============================================================
# 跨页合并
# ============================================================

def merge_tables(tables, log_lines):
    """同章节 + 跨页 + 同列 → 合并"""
    merged = []
    prev = None
    for t in tables:
        if prev is None:
            prev = t
            continue
        # 同章节 + 跨页 + 同列 → 合并
        if (
            t["section"] == prev["section"]
            and t["page"] == prev["page"] + 1
            and same_columns(t["rows"], prev["rows"])
        ):
            # 跳过下一页重复表头
            extra_rows = t["rows"][1:] if t["rows"] else []
            prev["rows"].extend(extra_rows)
            log_lines.append(
                f"  [step3] page {prev['page']}+{t['page']} 跨页合并: "
                f"prev={len(prev['rows']) - len(extra_rows)} 行, cur={len(extra_rows)} 行"
            )
        else:
            merged.append(prev)
            prev = t
    if prev is not None:
        merged.append(prev)
    return merged


# ============================================================
# 字段提取
# ============================================================

def extract_market_table(table):
    """MARKET 表（支持 5/6/7 列 + 二维拼接表硬切）

    5 列（成都）：材料名称/规格型号/产地/单位/除税价格(元)
    6 列（重庆）：序号/材料名称/规格及型号/单位/不含税价(元)/备注
    7 列（重庆区县）：序号/材料名称/规格/单位/价格1/价格2/备注
    8+ 列：调 split_2d_table 切 2 半；切成功 → 每半各走 5-7 列流程
    """
    rows = table["rows"]
    if not rows:
        return []
    n_cols = max(len(r) for r in rows) if rows else 0

    # 8+ 列：调二维拼接表硬切
    if n_cols >= 8:
        left_rows, right_rows = split_2d_table(rows)
        out_rows = []
        if left_rows is not rows:
            # 切分成功 → 左半走正常提取
            table_left = {**table, "rows": left_rows}
            out_rows.extend(extract_market_table(table_left))
            table_right = {**table, "rows": right_rows}
            out_rows.extend(extract_market_table(table_right))
            # 标记
            for r in out_rows:
                r["is_2d_spliced"] = True
            return out_rows
        # 切分失败（奇数/表头不对称）→ 整表保留 + 标 is_2d_spliced=True
        for cells in rows[1:]:
            cells_clean = [clean_cell(c) for c in cells]
            out_rows.append({
                "材料名称": "",
                "规格型号": "",
                "产地": "",
                "单位": "",
                "除税价格(元)": "",
                "含税价(元)": "",
                "序号": "",
                "is_2d_spliced": True,
                "raw_cells": cells_clean,
            })
        return out_rows

    if n_cols < 5:
        return []

    # 第一行是表头
    header = rows[0]
    # 校验：5/6/7 列都要有 "材料名称" 或 "工料" 之类关键词
    expected = ["材料名称", "规格型号", "规格及型号", "工料", "产品名称"]
    if not any(any(e in h for e in expected) for h in header):
        return []

    out_rows = []
    for cells in rows[1:]:
        if len(cells) < 5:
            continue
        # 序号列：纯数字 1-3 位
        seq = ""
        if n_cols >= 6:
            first = clean_cell(cells[0])
            if first.isdigit() or (first and first.replace(".", "").isdigit() and "." in first):
                seq = first
                # 6 列：序号/名称/规格/单位/价格/备注
                # 7 列：序号/名称/规格/单位/价格1/价格2/备注
                name = clean_cell(cells[1])
                spec = clean_cell(cells[2])
                # 7 列有产地
                if n_cols == 7:
                    origin = clean_cell(cells[3])
                    unit = clean_cell(cells[4])
                    price = clean_cell(cells[5])
                else:
                    # 2026-08-19 增：6 列含"产地"列（蒲江3月勘误表 67）→ 不提取产地
                    # 蒲江格式：序号/名称/规格/产地/单位/价格 → cells[4]=单位, cells[5]=价格
                    if len(header) >= 6 and clean_cell(header[3]).strip() == '产地':
                        origin = ""
                        unit = clean_cell(cells[4])
                        price = clean_cell(cells[5])
                    else:
                        origin = ""
                        unit = clean_cell(cells[3])
                        price = clean_cell(cells[4])
            else:
                # 第一列不是序号 → 退化为 5 列模式
                seq = ""
                name = clean_cell(cells[0])
                spec = clean_cell(cells[1])
                origin = clean_cell(cells[2]) if n_cols >= 5 else ""
                unit = clean_cell(cells[3])
                price = clean_cell(cells[4])
        else:
            # 5 列（成都）
            name = clean_cell(cells[0])
            spec = clean_cell(cells[1])
            origin = clean_cell(cells[2])
            unit = clean_cell(cells[3])
            price = clean_cell(cells[4])

        out_rows.append({
            "材料名称": name,
            "规格型号": spec,
            "产地": origin,
            "单位": unit,
            "除税价格(元)": price,
            "含税价(元)": "",
            "序号": seq,
        })
    return out_rows


def extract_pc_table(table, log_lines=None):
    """PC 表（按 name_idx + 1/2/3 推 spec/unit/rebar，按 header 关键词决定是否存在）

    核心思路（2026-07-27 v3，2026-07-28 修：n_pairs 只数 row 0 不数 sub-header）：
      - n_pairs = row 0 表头的"含税价/不含税价" cell 数 // 2（修复 sub-header 误算导致错位）
      - 每行独立算 n_row_prefix = len(cells) - n_pairs*2
      - name_idx 按 cells[0] 是否含 "类别" 决定（0 或 1）
      - spec_idx / unit_idx / rebar_idx 按 name_idx + 1/2/3 推（spec/unit/rebar 永远紧跟 name）
      - 但只有当 header 含对应关键词时才存在（否则该列跳过）

    适用 5 种 schema（成都 06 实测）：
      - 8 列（房建 PC）：header 有 规格/计量单位/钢筋含量 → spec=name+1, unit=name+2, rebar=name+3
      - 7 列（综合管廊）：header 有 规格/计量单位/钢筋含量 → 同上
      - 9 列（市政 II 小箱梁）：header 无 规格，有 计量单位/钢筋含量 → spec=-1, unit=name+1, rebar=name+2
      - 8/7 列混合（钢构件）：row 3 含 '类别'（skip 1），row 4-10 不含（skip 0），但 name+1 推的 spec_idx 永远对
    """
    rows = table["rows"]
    if not rows or len(rows) < 2:
        return []

    # 数 row 0 表头的"含税价/不含税价" cell 数（除以 2 = n_pairs）
    # 2026-07-28 修：取 row 0 + 数据行实际列数两个推断值的最大值
    # 根因：idx=1259 表 row 0 是 5 列简化表头（只有 1 对含税/不含税），但 sub-header 展开后
    #       数据行 9 列实际有 2 对价格对（含税/不含税 + 距离调整含税/不含税）
    # 之前数 rows[:3] 会把 sub-header 的"含税价,不含税价,含税价,不含税价"算进 n_price_cols=6 → n_pairs=3 → 错位
    # 修法：从数据行最长列数 + prefix 反推 n_pairs，取 row 0 推断值的最大值
    n_price_cols_in_row0 = sum(
        1 for c in rows[0] if "含税价" in c or "不含税价" in c
    ) if rows else 0
    n_pairs_from_row0 = n_price_cols_in_row0 // 2 if n_price_cols_in_row0 >= 2 else 0

    # 从数据行反推 n_pairs：prefix = name_idx(0/1) + 1 + has_spec + has_unit + has_rebar
    # 注：has_rebar 按 row 0+row 1 中钢筋/钢绞线/总含钢量关键字数取最大值（防 idx=1259 row 0 只有 1 个钢筋 col 但 row 1 sub-header 展开 3 个）
    header0 = rows[0] if rows else []
    has_spec_h = any("规格" in h for h in header0)
    has_unit_h = any("计量单位" in h or "单位" in h for h in header0)
    rebar_kws = ["钢筋", "钢绞线", "总含钢量"]
    has_rebar_h = any(kw in h for h in header0 for kw in rebar_kws)
    n_rebar_in_row0 = sum(1 for h in header0 for kw in rebar_kws if kw in h)
    n_rebar_in_row1 = sum(1 for h in rows[1] for kw in rebar_kws if kw in h) if len(rows) > 1 else 0
    n_rebar_max = max(n_rebar_in_row0, n_rebar_in_row1)
    n_prefix_from_header = 1 + (1 if has_spec_h else 0) + (1 if has_unit_h else 0) + n_rebar_max

    # 找数据行最长列数 → 推断 n_pairs
    max_data_cols = max((len(r) for r in rows[1:] if len(r) >= 3), default=0)
    n_pairs_from_data = max(0, (max_data_cols - n_prefix_from_header) // 2) if max_data_cols else 0

    n_pairs = max(n_pairs_from_row0, n_pairs_from_data)
    if n_pairs == 0:
        return []

    # 数据行
    data_rows = [r for r in rows[1:] if len(r) >= 3]
    category_kw = ["房建工程", "市政工程", "钢构件"]
    data_rows = [r for r in data_rows if not (len(r) <= 2 and any(kw in r[0] for kw in category_kw))]

    # 过滤 sub-header 行（2026-07-28）：价格列被"总含钢量(t/m3)"/"含税价(元/m3)"等子表头文本占位
    # 真实场景：钢构件 sub-section 第一行是 sub-header（"钢筋(t/m3) ... 总含钢量(t/m3) | 含税价(元/m3) | 不含税价(元/m3) | 备注"）
    subheader_kw = ["含税价", "不含税价", "总含钢量", "(t/m3)", "(元/m3)"]
    n_before = len(data_rows)
    data_rows = [
        r for r in data_rows
        if not any(any(kw in c for kw in subheader_kw) for c in r)
    ]
    if n_before != len(data_rows) and log_lines is not None:
        log_lines.append(f"[step3] PC 过滤 sub-header 行: 删除 {n_before - len(data_rows)} 行")

    if not data_rows:
        return []

    # n_pairs 已在前面算好（只数 row 0，不数 sub-header）

    # header 关键词检测（决定 spec/unit/rebar 是否存在）
    # 2026-07-28 修：用 row 0+row 1 sub-header 中 rebar 关键字数取最大值（防 idx=1259 row 0 只有 1 个钢筋 col 但 row 1 sub-header 展开 3 个）
    header0 = rows[0]
    has_spec = any("规格" in h for h in header0)
    has_unit = any("计量单位" in h or "单位" in h for h in header0)
    has_rebar = any(kw in h for h in header0 for kw in ["钢筋", "钢绞线", "总含钢量"])
    n_rebar_row0_n = sum(1 for h in header0 for kw in ["钢筋", "钢绞线", "总含钢量"] if kw in h)
    n_rebar_row1_n = sum(1 for h in rows[1] for kw in ["钢筋", "钢绞线", "总含钢量"] if kw in h) if len(rows) > 1 else 0
    n_rebar_cols = max(n_rebar_row0_n, n_rebar_row1_n)

    out_rows = []
    for cells in data_rows:
        # name_idx：cells[0] 是类别 → 1；否则 0
        name_idx = 1 if (cells and any(kw in cells[0] for kw in category_kw)) else 0
        if name_idx >= len(cells):
            continue
        # 实际 prefix 列数：name_idx + 1(name) + has_spec + has_unit + n_rebar_cols
        # 不再用 len(cells) - n_pairs*2（会受"说明"列干扰）
        n_row_prefix = name_idx + 1 + (1 if has_spec else 0) + (1 if has_unit else 0) + n_rebar_cols
        if n_row_prefix < 1:
            continue
        name = clean_cell(cells[name_idx])
        if not name:
            continue
        if len(cells) <= 2:
            continue

        # spec/unit/rebar 按 name_idx + 1/2/3 推（rebar 列可能多列，依次填充）
        spec = ""
        unit = ""
        rebar = ""
        next_col = name_idx + 1

        if has_spec and next_col < n_row_prefix:
            spec = clean_cell(cells[next_col])
            next_col += 1
        if has_unit and next_col < n_row_prefix:
            unit = clean_cell(cells[next_col])
            next_col += 1
        # 钢筋含量：合并所有 rebar 列到一格（"0.195/0.049/0.244"），避免丢信息
        rebar_parts = []
        while next_col < n_row_prefix:
            v = clean_cell(cells[next_col])
            if v:
                rebar_parts.append(v)
            next_col += 1
        rebar = "/".join(rebar_parts)

        # 价格对
        for g in range(n_pairs):
            incl_col = n_row_prefix + g * 2
            excl_col = n_row_prefix + g * 2 + 1
            incl = clean_cell(cells[incl_col]) if incl_col < len(cells) else ""
            excl = clean_cell(cells[excl_col]) if excl_col < len(cells) else ""

            if n_pairs == 1:
                # 2026-07-29 修：完整措辞（用户原话："到场价(含60KM内运输费以及施工现场构件下车及堆放费)"）
                # km 从 row 0 表头抓（idx=1249 是 40km, idx=1245/1266 是 60km）
                km_match = re.search(r"(\d+)\s*[Kk][Mm]", " ".join(rows[0]))
                km_str = f"{km_match.group(1)}KM" if km_match else "60KM"
                price_type = f"到场价(含{km_str}内运输费以及施工现场构件下车及堆放费)"
            else:
                # n_pairs >= 2: 第 1 对是到场价，第 2 对是运输费调整
                # 2026-07-29 修：完整措辞
                km_match = re.search(r"(\d+)\s*[Kk][Mm]", " ".join(rows[0]))
                km_str = f"{km_match.group(1)}KM" if km_match else "60KM"
                if g == 0:
                    price_type = f"到场价(含{km_str}内运输费以及施工现场构件下车及堆放费)"
                else:
                    # 2026-07-29 修：完整措辞（用户原话："运输费调整 超过60km每增加1km费用"）
                    price_type = f"运输费调整(超过{km_str}每增加1km费用)"

            # 说明列：cells 长度 > n_row_prefix + n_pairs*2 时，剩余最后一列是说明
            # 2026-07-28 用户反馈"说明还在不含税价里"，cells[8] 被丢弃
            desc = ""
            desc_col = n_row_prefix + n_pairs * 2
            if desc_col < len(cells):
                desc = clean_cell(cells[desc_col])

            out_rows.append({
                "构件名称": name,
                "规格": spec,
                "单位": unit,
                "钢筋含量": rebar,
                "价格类型": price_type,
                "含税价(元)": incl,
                "不含税价(元)": excl,
                "说明": desc,
            })

    return out_rows


def find_col(header, kw):
    """找包含 keyword 的列索引"""
    for i, h in enumerate(header):
        if kw in h:
            return i
    return -1


def sort_kws_by_pos(header, kws):
    """按 keyword 在 header 中最早出现位置排序（防止 2D 拼接表错位）

    真实场景：8 列 2D 表 ["材料名称", "规格", "单位", "信息价(元)", "材料名称", "规格", "单位", "价格(元)"]
    → "价格(元)" 在 col 7，"信息价(元)" 在 col 3（更靠左）
    → 按 list 顺序 "价格(元)" 优先命中 col 7 → 错位
    → 按位置排序 "信息价(元)" 优先命中 col 3 → 正确
    """
    pos = []
    for kw in kws:
        idx = find_col(header, kw)
        if idx >= 0:
            pos.append((idx, kw))
    pos.sort()
    return [kw for _, kw in pos]


def is_meta_row(cells, col_map):
    """判断是否是元信息行（地址/电话/联系人/邮编），过滤掉

    真实场景（成都 06 实测 ~15 张新型材料表受影响）：
      - 表后跟"地址:成都市...电话:028-xxx联系人:..."
      - 单 cell 长字符串（>30 字符），其它列空
      - 有 "地址/电话/联系人/邮编/传真/邮箱/网址" 关键词

    反例保护（防误杀）：
      - 超长规格描述（如"高耐候耐水天彩石漆(平面)..."）但有价格数字 → 是数据行
    """
    if not cells or not any(c.strip() for c in cells if c):
        return True  # 全空行

    # 有价格数字 → 是真实数据行（保留）
    price_idx = col_map.get("价格", -1)
    if 0 <= price_idx < len(cells):
        price = (cells[price_idx] or "").strip()
        if price and re.search(r"\d", price):
            return False

    # 长字符串（>80 字符）且无价格 → 元信息（2026-07-28 阈值从 30 改 80：HDPE-PG 产品名 31 字符误杀）
    name_idx = col_map.get("产品名", 0)
    name = (cells[name_idx].strip() if 0 <= name_idx < len(cells) and cells[name_idx] else "")
    if len(name) > 80:
        return True

    # 关键词匹配（2026-07-28 加 "公司简介" 拦国塑表脚注 800+ 字符长描述）
    if any(kw in name for kw in ["地址", "电话", "联系人", "邮编", "传真", "邮箱", "网址", "公司简介", "销售地址", "生产基地"]):
        return True

    return False


# ============================================================
# 新型材料表（2026-07-27 增）
# ============================================================

# 通用列关键词映射（按 header 含关键词匹配列索引）
NEW_MATERIAL_COL_KW = {
    "产品名": ["材料名称", "产品名称", "构件名称", "商品名称"],
    "规格": ["规格型号", "规格", "型号规格", "规格、型号", "规格型号(mm)", "规格(mm)", "型号"],
    "品牌": ["品牌", "厂家", "生产商", "供应商"],
    "单位": ["单位", "计量单位"],
    # 价格（单价格表用，含税不含税全匹配）
    "价格": ["价格(元)", "不含税单价(元)", "不含税价", "不含税价(元)", "单价(元)", "除税价格(元)", "除税价格(元)(含15Km内运输费)", "信息价(元)", "价格(元)(不含税)", "单价(元/m2)"],
    # 含税价（双价格表专用）
    "含税价": ["含税价(元)", "含税单价(元)", "含税价(元/m3)", "含税价(元/m2)", "含税价(元/只)", "含税价(元/吨)", "含税价(元/套)", "含税价"],
    # 不含税价（双价格表专用）
    "不含税价": ["不含税价(元)", "不含税单价(元)", "不含税价(元/m3)", "不含税价(元/m2)", "不含税价(元/只)", "不含税价(元/吨)", "不含税价(元/套)", "不含税价"],
    "公司名": [],  # 从 caption 提
    "联系人": ["联系人", "联系电话", "电话"],
    "执行标准": ["执行标准", "备注", "备注/执行标准/产品说明", "说明"],
}


def find_real_header(rows, max_scan=5):
    """找真实表头（前 max_scan 行合并虚拟表头）

    适用场景（成都 06 实测）：
      - 多行表头：row 0 标题，row 1 真实表头（如建华建材/三棵树报价表）
      - 标题 + 副标题：row 0 公司名，row 1 报价表标题，row 2 表头
      - 标准表：row 0 已是表头

    判定标准（阈值降到 2 个关键词）：
      - 至少 1 个产品名关键词（材料名/产品名/商品名/构件名/型号/产品编号）
      - 至少 1 个价格关键词（价格/单价/含税/不含税/除税）

    2026-07-28 修复: 跳过开头的单 cell 行（公司名/标题），避免单 cell col 0 挤掉表头列

    2026-08-19 修复：检测首列 1-N 序号列（如蒲江3月勘误表 6 列含"序号 1-10"），drop 后按正常 dispatch
    """
    if not rows:
        return None, -1

    # 2026-08-19 增：drop 序号列
    nums = []
    for r in rows[:5]:
        if not r:
            break
        v = r[0]
        if v is None:
            break
        try:
            nums.append(int(str(v).strip()))
        except (ValueError, TypeError):
            break
    if len(nums) >= 3 and nums == list(range(1, len(nums) + 1)):
        rows = [r[1:] for r in rows]

    # 跳过开头的单 cell 行（公司名/材料标题/章节小标题）和全空行
    data_start = 0
    while data_start < len(rows):
        r = rows[data_start]
        if len(r) == 1 and r[0] and r[0].strip():
            data_start += 1
            continue
        if not any(c.strip() for c in r if c):
            data_start += 1
            continue
        break
    if data_start >= len(rows):
        return None, -1

    scan_rows = rows[data_start:data_start + max_scan]
    n_cols = max(len(r) for r in scan_rows) if scan_rows else 0
    if n_cols == 0:
        return None, -1

    # 合并 scan_rows 作为虚拟表头
    merged = ["" for _ in range(n_cols)]
    for r in scan_rows:
        for i, cell in enumerate(r):
            if i < n_cols and cell and cell.strip():
                if not merged[i]:  # 只填空位
                    merged[i] = clean_cell(cell)

    # 关键词判定
    product_kws = ["材料名称", "产品名称", "商品名称", "构件名称", "型号", "产品编号", "产品名"]
    price_kws = ["价格", "单价", "含税", "不含税", "除税"]

    n_product = sum(1 for h in merged if any(kw in h for kw in product_kws))
    n_price = sum(1 for h in merged if any(kw in h for kw in price_kws))

    if n_product >= 1 and n_price >= 1:
        # 找哪一行首次出现 ≥1 product kw（在 scan_rows 范围内）
        for ri, r in enumerate(scan_rows):
            for c in r:
                if any(kw in c for kw in product_kws):
                    return merged, data_start + ri
        return merged, data_start

    return None, -1


def extract_d2_pivot_table(table, rows, content_list=None):
    """D2 横拼接表 → 转长表（2026-07-28 GFPE/RTP-SW 类管材）

    触发条件：row 0 第一列 = "规格"（如 ['规格', 'SN8', 'SN10', ...]）

    结构：
      row 0: ['规格', '等级1', '等级2', ...]   ← 等级作为列
      row 1: ['公称尺寸mm', '元/米', '元/米', ...]  ← 单位说明（兜底）
      row 2+: [尺寸值, 价格1, 价格2, ...]  ← 数据

    输出长表：
      产品名 = caption（不漏！）
      规格 = "DN{尺寸值} {等级}"（合字符串）
      单位 = 每列独立（2026-07-29 改）：
        - 列头匹配 "(元/XXX)" → 拆 XXX（如"不锈钢卡套(元/套)" → 单位="套"）
        - 否则走 row 1 对应列（如 SN8 → 元/米）
      不含税价 = 对应价格

    2026-07-28 增：扫同 page 末尾 text 项提取联系人/电话（GFPE/RTP-SW 同 page 176 底部
    "联系人:李先生" + "联系电话:15583839911"）

    2026-07-29 增：单位按列头拆（idx=1303 8-col DRPO 嵌套：SN 走米、配件走套/只）
    """
    out_rows = []
    cap = (table.get("caption", "") or "").strip()
    # 2026-07-28 修：cap 空 → 用被清掉的 1-cell 标题行（idx=1309 DRPO 实测）
    pre_caption = (table.get("pre_caption") or "").strip()
    product_name = cap or pre_caption or "新型材料"
    page_idx = table.get("page", 0)

    # 扫同 page 末尾 text 项 → 提取联系人/电话（2026-07-28 GFPE/RTP-SW 实测 page 176 y=814）
    contact_name, contact_phone = "", ""
    if content_list:
        page_texts = []
        for ci in content_list:
            if ci.get("type") != "text":
                continue
            if ci.get("page_idx", 0) != page_idx:
                continue
            t = ci.get("text", "").strip()
            if t:
                page_texts.append(t)
        # 拼接（用换行分隔，匹配 "联系人:李先生" / "联系电话:..."）
        page_text_joined = "\n".join(page_texts)
        m_name = re.search(r"联系人[:：]\s*([^\n]+)", page_text_joined)
        m_phone = re.search(r"(?:联系电话|电话)[:：]\s*([^\n]+)", page_text_joined)
        if m_name:
            contact_name = m_name.group(1).strip()
        if m_phone:
            contact_phone = m_phone.group(1).strip()

    if len(rows) < 3:
        return []

    # row 0 = ['规格', '等级1', '等级2', ...] → 等级列表
    grades = [clean_cell(c) for c in rows[0][1:]]
    # 2026-07-29 改：单位来源
    #   列头含 "(元/XXX)" 模式 → 拆出 XXX（如"不锈钢卡套(元/套)" → 单位="套"）
    #   否则走老逻辑 → row 1 第二列共享（仅当 row 1 是单位行时）
    # 2026-07-29 增：检测 row 1 是单位行还是数据行（idx=1303/1309 DRPO 缺单位行）
    #   row 1 col 0 形如 "DN/ID..." / "Φ..." / "外径..." / 纯数字 → 数据行
    #   否则 → 单位行（如 "公称尺寸mm"）
    has_unit_row = False
    if len(rows) > 1 and len(rows[1]) > 1:
        row1_col0 = clean_cell(rows[1][0]) if rows[1] else ""
        if not re.match(r"^[DNΦφ外径\d]", row1_col0):
            has_unit_row = True
    # 2026-07-29 改：捕获括号整段 OR 裸末尾 "元/X"（L1220 DRPO 第二张表列头是裸字）
    #   括号形式 group(1) = "元/套"/"元/只"（用户原话「单位元/套 198」= 三字）
    #   裸字形式 group(2) = "元/只"/"元/套"（L1220 "热收缩套元/只" 无括号）
    _unit_pat = re.compile(r"(?:[（(]([^)）]+)[)）]|元/(\S+))")
    default_unit = clean_cell(rows[1][1]) if has_unit_row and len(rows) > 1 and len(rows[1]) > 1 else ""
    units = []
    for grade_hdr in grades:
        m = _unit_pat.search(grade_hdr)
        if m:
            units.append(m.group(1) or ("元/" + m.group(2)))  # 优先括号整段("元/套"), 否则裸字 group(2) 补回"元/"前缀
        else:
            units.append(default_unit)
    # 数据行起始：有单位行 → rows[2:]；无单位行（如 DRPO）→ rows[1:]
    data_start = 2 if has_unit_row else 1

    # row 2+ 是数据行（每行 = 一个规格值 + 多个价格）
    for data_row in rows[data_start:]:
        if not data_row or all(not (c or "").strip() for c in data_row):
            continue
        size_val = clean_cell(data_row[0])
        if not size_val:
            continue
        # 遍历每个等级 → 一行长表
        for i, (grade, unit) in enumerate(zip(grades, units)):
            price_col_idx = i + 1
            if price_col_idx >= len(data_row):
                break
            price = clean_cell(data_row[price_col_idx])
            if not price:
                continue
            # 2026-07-28 修：size_val 已含"DN/ID"/"Φ"/"外径" 前缀时不再硬拼 DN
            # 真例：DRPO row 0 = "DN/ID300" → 应输出 "DN/ID300 SN8" 而不是 "DNDN/ID300 SN8"
            row1_first = (rows[1][0] if len(rows) > 1 and rows[1] else "")
            if size_val.startswith(("DN/", "DN/", "DN ", "Φ", "φ", "外径")):
                spec_str = f"{size_val} {grade}"
            elif "外径" in row1_first:
                spec_str = f"Φ{size_val} {grade}"
            else:
                spec_str = f"DN{size_val} {grade}"
            # 2026-07-29 增：剥掉 grade 末尾的 "(元/XXX)" 括号 OR 裸 "元/X" 字（DRPO 两张表都覆盖）
            # 例 grade="不锈钢卡套(元/套)" → spec="DN/ID200 不锈钢卡套"
            # 例 grade="热收缩套元/只"（L1220 裸字）→ spec="DN/ID2000 热收缩套"
            spec_str = re.sub(r"(?:[（(][^）)]*[）)]|元/\S+)\s*$", "", spec_str).strip()
            out_rows.append({
                "产品名": product_name,
                "规格": spec_str,
                "品牌": "",
                "单位": unit,
                "价格(元)": "",
                "含税价(元)": "",
                "不含税价(元)": price,
                "公司名": "",
                "联系人": contact_name,
                "电话": contact_phone,
                "执行标准": "",
            })
    return out_rows


def extract_new_material_4col(rows, company_name="", log_lines=None, contact_phone=""):
    """4 列精简 NM 提取（[产品名, 规格, 单位, 价格]）

    给 split_2d_table 拆 4+4 后用（2026-07-28 P1 通用修法）。
    复用 extract_new_material_table 的所有通用规则：
      - last_real_name 跨行产品名继承
      - is_meta_row 元信息过滤（已用阈值 80 + 公司简介关键词）
      - 含税/不含税两列分流
      - 三道锁（仅双价格表）
    """
    if not rows or len(rows) < 2:
        return []
    # 4 列硬编码 col_map
    name_idx, spec_idx, unit_idx, price_idx = 0, 1, 2, 3
    out_rows = []
    last_real_name = ""
    for cells in rows[1:]:  # 跳过首行 header
        if not cells or all(not c.strip() for c in cells):
            continue
        # 补列长度
        while len(cells) < 4:
            cells.append("")
        name = clean_cell(cells[name_idx])
        # 跨行继承（产品名=DN/Φ/× 开头 → 继承上一行）
        # 2026-07-28 用户原话："第一个材料名称到第二个材料名称之间全是第一个表格的信息"
        # cells[0] 是 DN 规格时，结构是 [规格, 单位, 价格]，要左移解读
        if name and re.match(r"^[DNΦ×]+", name) and last_real_name:
            name = last_real_name  # 产品名继承
            spec = clean_cell(cells[name_idx])  # 真正的规格是 cells[0]
            unit = clean_cell(cells[spec_idx])
            price = clean_cell(cells[unit_idx])
        elif name and not re.match(r"^[DNΦ×]+", name):
            last_real_name = name
            spec = clean_cell(cells[spec_idx])
            unit = clean_cell(cells[unit_idx])
            price = clean_cell(cells[price_idx])
        if not name:
            continue
        out_rows.append({
            "产品名": name,
            "规格": spec,
            "品牌": "",
            "单位": unit,
            "价格(元)": "",
            "含税价(元)": "",
            "不含税价(元)": price,
            "公司名": company_name,
            "联系人": "",
            "电话": contact_phone,
            "执行标准": "",
        })
    return out_rows


def split_new_material_2d(rows):
    """NM 章节专用 2D 拼接表判定（2026-07-28 增）

    规则（P1 通用）：
      - 表头 col idx >= 6 时检测列名重复
      - 重复位置 split_at → 拆 [0:split_at] + [split_at:n_cols]
      - 子表头从前 3 行合并虚拟表头（处理多行表头）
      - 左右共享表头（用户原话：从左表复制）

    返回: (left_rows, right_rows, header)
      - left_rows, right_rows: 已切好的 4-列子表 rows
      - header: 共享表头（用左子表表头，右子表如有独立 header 优先）

    不适用:
      - 列名不重复 → 返回 None
    """
    if not rows:
        return None
    n_cols = max(len(r) for r in rows)
    if n_cols < 6:
        return None

    # 合并前 3 行作为虚拟表头
    merged = ["" for _ in range(n_cols)]
    for r in rows[:3]:
        for i, cell in enumerate(r):
            if i < n_cols and cell and cell.strip():
                if not merged[i]:
                    merged[i] = clean_cell(cell)

    # 找第一个重复列名 = split_at
    seen = {}
    split_at = -1
    for i, col_name in enumerate(merged):
        col_name = col_name.strip()
        if not col_name:
            continue
        if col_name in seen:
            split_at = i
            break
        seen[col_name] = i

    if split_at <= 1:
        return None

    # 切分（2026-07-28 修：按每行实际列数切两半，缺产品名补空）
    # 原 bug：`r[:split_at]` 对 6 列行得到 4 列，`r[split_at:]` 得到 2 列，两边都不对
    # 修法：effective_split = len(r) // 2（每行按实际列数切两半）
    #   8 列行 → split=4：左 [产品名1, 规格1, 单位1, 价格1], 右 [产品名2, 规格2, 单位2, 价格2]
    #   6 列行 → split=3：左 ['', 规格1, 单位1, 价格1]（col 0 继承）, 右 ['', 规格2, 单位2, 价格2]
    #   7 列行 → split=3：左 ['', 规格1, 单位1, 价格1], 右 ['', 规格2, 单位2, 价格2]
    left, right = [], []
    for r in rows:
        n_r = len(r)
        eff_split = n_r // 2 if n_r % 2 == 0 else n_r // 2  # 偶数 6→3, 8→4
        # 优先用 split_at（仅当行总长 >= split_at*2 时）
        if n_r >= 2 * split_at:
            eff_split = split_at
        elif n_r >= 2 and n_r % 2 == 0:
            eff_split = n_r // 2
        else:
            eff_split = split_at  # fallback
        left_piece = list(r[:eff_split]) + [""] * (split_at - eff_split)
        right_piece = list(r[eff_split : eff_split + (n_cols - split_at)]) + [""] * ((n_cols - split_at) - min(n_r - eff_split, n_cols - split_at))
        left.append(left_piece)
        right.append(right_piece)
    # 共享表头：左子表前 1 行是 header，右子表没独立 header 也用左
    # 不动 left/right rows，原样返回
    return left, right, merged


def extract_new_material_table(table, content_list=None):
    """新型材料表（24+ 张表 5+ 种 schema，智能表头 + 通用列映射）

    输入：parse_html_table 后的 rows
    输出：list of dict（8 字段：产品名/规格/品牌/单位/价格/公司名/联系人/执行标准）

    字段填充率（成都 06 实测）：
      产品名 100% / 规格 100% / 品牌 54% / 单位 95%+ / 价格 100%
      公司名 8%（p177 四川易霖诚泰、p180 建华建材）
      联系人 8%（p176 李先生+电话）
      执行标准 ~70%

    适用 schema：
      - 5 列 [材料名, 规格, 品牌, 单位, 价格]
      - 6 列 [材料名, 规格, 品牌, 单位, 价格, 备注]
      - 7 列 [类别, 产品名, 商品名, 型号, 规格, 单位, 单价]
      - 13 列横向拼接 [产品名, 商品名, 型号, 规格, 单价 + 镜像]
      - 标题 1-2 列 + 数据 5-7 列（建华/三棵树）
    """
    rows = table["rows"]
    if not rows or len(rows) < 2:
        return []

    # 2026-07-29 增：MARKET 表防护
    # 旧 bug: page 183 MARKET「六月份建筑材料市场价格(一)」被 step2 误归 NM, 写"100*50"当产品名
    # 旧修法: 硬编码 table_page > 181 → return []  (2026-08-03 改：硬编码 181 误伤所有 NM 锚点 > 181 的期)
    # 新修法: 只看 rows 前 5 行 cell 内容（"建筑材料市场价格"特征），不再依赖 page 硬编码
    # 验证: 4 期 NM 锚点 page_idx 范围 135-192，硬编码 181 会误伤 2026-3 (NM 锚点 192)
    cap = table.get("caption", "")
    for r in rows[:5]:
        if any(c and "建筑材料市场价格" in c for c in r):
            return []

    # 2026-07-28 用户要求：1-cell 标题行清理 **只对 D2 类型表生效**（不要全局改所有 NM 表）
    # 真实场景：idx=1309 page=179 DRPO 表 r0 = "FRD承插双层增强改性聚烯烃DRPO..." 1-cell 标题
    #   原 bug：D2 pivot 检测要求 rows[0] = ['规格', 'SN8', ...]，DRPO 表 rows[0] 是 1-cell
    #            通用 col_map 路径也走不到（"规格" 不是 product kw）→ 输出 0 行
    #   修法：D2 判定包含 2 种情况
    #     - 情况1：rows[0] 直接是 ['规格', 'SN8', ...]（GFPE/RTP-SW）
    #     - 情况2：rows[0] 是 1-cell 标题 + rows[1] 是 ['规格', 'SN8', ...]（DRPO）
    #          确认是 D2 后，清 1-cell 标题 + 1-cell pre_caption 作 product_name
    # 防误伤：非 D2 表（用 col_map 路径的）走原有逻辑，1-cell 由 find_real_header 自己清
    is_d2_pivot = False
    if rows[0] and len(rows[0]) >= 3 and clean_cell(rows[0][0]).strip() == "规格":
        is_d2_pivot = True  # 情况1
    elif (
        rows[0] and len(rows[0]) == 1 and rows[0][0] and rows[0][0].strip()
        and len(rows) >= 2
        and rows[1] and len(rows[1]) >= 3
        and clean_cell(rows[1][0]).strip() == "规格"
    ):
        is_d2_pivot = True  # 情况2（DRPO 类）

    if is_d2_pivot:
        # D2 表专属：开头 1-cell 标题行清理 + 取 pre_caption 作 product_name
        pre_caption_parts = []
        while rows and len(rows[0]) == 1 and rows[0][0] and rows[0][0].strip():
            pre_caption_parts.append(rows[0][0].strip())
            rows = rows[1:]
        if pre_caption_parts:
            table = {**table, "pre_caption": max(pre_caption_parts, key=len)}
        if not rows or len(rows) < 2:
            return []
        # D2 pivot 检测（清完后确认 rows[0] = ['规格', ...]）
        if rows[0] and len(rows[0]) >= 3 and clean_cell(rows[0][0]).strip() == "规格":
            return extract_d2_pivot_table({**table, "rows": rows}, rows, content_list=content_list)

    # 2026-08-03 增：NM schema 期望关键词检查（防 14 列横向多价表误抽）
    # 必须放在 D2 pivot / 嵌套 1-cell 处理**之后**：D2 表 rows[0] = ['规格', ...]（无 NM 标志词），
    #       但已走 D2 分支 early return；嵌套 1-cell（超耐腐/建华）可能清掉首行后才到这
    # 根因：2023-5 p144 沟槽产品表是 14 列横向多价表（[规格, 刚性卡箍, 柔性卡箍, 45°弯头, 90°弯头, ...]）
    #       真实 schema = 序号 | 规格 | 14 列不同管件单价，**全部是数字**，没有产品名/材料名称
    #       旧代码走 find_real_header → col_map（产品名/价格）→ 序号当产品名、3 档单价分别当规格/单位/不含税价
    #       → xlsx NM sheet 出现 212 行 4 列无表头数据（产品名=序号，规格=第 1 档单价）
    # 修法 1（关键词）：schema header（rows[0] 前 3 cell）必须含 NM 标志词
    #       否则 return []（不是 NM 协会推荐表）
    # 修法 2（列数）：NM 协会推荐表典型 ≤11 列，14 列沟槽表 column_count > 11 → return []
    # 碰撞：沟槽表 14 列 + 关键词全无 → 双重拦截
    #       真 NM 表：5/6/7/9/13 列 + 含「材料名称/产品名称/构件名称/名称/规格型号/型号/类别」之一 → 通过
    nm_header_kws = ["材料名称", "产品名称", "商品名称", "构件名称", "产品名", "材料名",
                     "名称", "规格型号", "型号", "类别"]
    first_row_text = " ".join(str(c) for c in rows[0][:3] if c)
    n_cols_first = len(rows[0])
    # 2026-08-03 改：仅保留列数 > 11 拦截（沟槽管件表 14 列），不放宽 schema header 关键词
    # 根因：放宽「名称/型号/类别」会误伤 2026-06 「干式连接螺栓」/「卡靴」表（rows[0] 第一 cell 是 1-cell 标题）
    # 沟槽表列数 < 11 的（如 2024-8 p172 10列）已被 line 1193 col_map 找不到「价格」拦截
    if n_cols_first > 11:
        return []

    # 2026-07-28 修超耐腐复合表 bug：嵌套子表拆分
    # 真嵌套（超耐腐+DRPO）：row 中 1-cell + 后面数据行**第一列名是 "规格"**（D2 横拼接特征）
    # 假嵌套（易霖诚泰）：row 1 1-cell 副标题 + 后面数据列名正常（如 "材料名称"）
    # 判定：切分点后的第一行 >=3 列数据，若第一列是 "规格"（D2 横拼接起始特征）→ 真嵌套
    nested_split_idx = None
    for i, r in enumerate(rows[1:], 1):  # 跳过 row 0（表头）
        if len(r) == 1 and r[0] and r[0].strip():
            # 看后面第一行 >=3 列的第一列名
            j = i + 1
            while j < len(rows) and len(rows[j]) < 3:
                j += 1
            if j >= len(rows):
                continue
            first_cell = clean_cell(rows[j][0]).strip() if rows[j] else ""
            # 真嵌套条件：子表起始行第一列 == "规格"（D2 横拼接特征）
            if first_cell == "规格":
                # 验证后面 >=2 行 >=3 列
                n_data_after = 0
                k = j
                while k < len(rows) and len(rows[k]) >= 3:
                    n_data_after += 1
                    k += 1
                if n_data_after >= 2:
                    nested_split_idx = i
                    break
    if nested_split_idx is not None:
        # 找切分点后面的"连续 1-cell 行"的终点（作为子表起始）
        # 子表起始 = 第一个 >=3 列的行
        sub_start = nested_split_idx + 1
        while sub_start < len(rows) and len(rows[sub_start]) < 3:
            sub_start += 1
        pre_rows = rows[:sub_start]
        # 2026-07-28 修：先扫所有 1-cell 行提取联系电话（再清掉它们）
        # 根因：之前"保留含电话的行"导致"销售地址:...联系电话:188..."被错当成产品名
        # 修法：先扫所有行（含 1-cell 行）抓出 contact_phone，再清掉所有 1-cell 行
        contact_phone_outer = ""
        for r in rows:
            if len(r) == 1 and r[0]:
                cand = r[0].strip()
                m_ph = re.search(r"(?:服务电话|联系电话|电话)[:：]\s*([0-9\-]+)", cand)
                if m_ph:
                    contact_phone_outer = m_ph.group(1)
                    break
        # 2026-07-28 决策：嵌套子表（如 DRPO 改性聚烯烃）单位混合（前 4 列 元/米 + 后 3 列 元/套/元/只），
        #   D2 处理不支持混合单位 → 直接跳过嵌套子表，不强行解析（按"空了就空，不要反推"原则）
        # 超耐腐表是 4+4 镜像拼接但 row 列数不等（6/7/8 列），split_new_material_2d 切错，
        #   所以这里**只处理 pre_rows 中第一个子表**（row 0 到 split 第一次出现的"超耐腐碳钢XX"），
        #   后续多个产品系列各自继承产品名。
        # 2026-07-28 修：彻底删除所有 1-cell 行（不再保留联系信息行）
        # 根因：之前"保留含电话的行"导致"销售地址:...联系电话:188..."被错当成产品名
        # 联系人/电话由上面 contact_phone_outer 提前抓出（line 871-879）
        cleaned = [r for r in pre_rows if not (len(r) == 1 and r[0] and r[0].strip())]
        # 2026-07-28 修：不再"改名禁用 split"，让 split 正常工作
        # 根因之前是 split 函数对不齐行只截到 len(r)，导致左右子表错位
        # 已修 split 函数（按 half 切两半，缺产品名补空），所以这里直接传 cleaned 即可
        # 把 contact_phone_outer 注入到 table dict 让后续 extract_new_material_table 拿到
        out = extract_new_material_table({**table, "rows": cleaned}, content_list=content_list)
        if contact_phone_outer:
            for r_out in out:
                if not r_out.get("电话"):
                    r_out["电话"] = contact_phone_outer
        # 2026-07-29 增：嵌套 D2 子表（idx=1303 8-col DRPO）调 D2 提取
        # pre_caption 取 sub_start 之前最近的 1-cell 行（"改性聚烯烃(DRPO)..."）
        # 不动 pre_rows（超耐腐逻辑保留）
        sub_table_rows = rows[sub_start:]
        if sub_table_rows and len(sub_table_rows) >= 3 \
                and clean_cell(sub_table_rows[0][0]).strip() == "规格":
            sub_pre_caption = ""
            for ri in range(nested_split_idx, sub_start):
                if len(rows[ri]) == 1 and rows[ri][0] and rows[ri][0].strip():
                    cand = rows[ri][0].strip()
                    # 跳过明显的销售/联系信息行（idx=1303 row 30 实测含 "销售地址" + "联系电话" + "生产基地"）
                    if any(kw in cand for kw in ["销售", "联系", "地址", "电话", "邮箱", "生产基地"]):
                        continue
                    sub_pre_caption = cand  # 取最近的非联系信息行（不取最长）
            out2 = extract_d2_pivot_table(
                {**table, "rows": sub_table_rows,
                 "pre_caption": sub_pre_caption, "caption": ""},
                sub_table_rows, content_list=content_list
            )
            if out2:
                out.extend(out2)
                if contact_phone_outer:
                    for r_out in out2:
                        if not r_out.get("电话"):
                            r_out["电话"] = contact_phone_outer
        return out

    # P1 2026-07-28: 列名重复 → 拆 4+4 两张 1D 子表（不走 col_map 通用逻辑）
    split = split_new_material_2d(rows)
    if split is not None:
        left, right, shared_header = split
        # 公司名 fallback
        cap = table.get("caption", "").strip()
        company_name = ""
        if any(kw in cap for kw in ["公司", "有限", "科技", "建材", "集团", "股份"]):
            company_name = cap
        elif rows and rows[0] and len(rows[0]) == 1 and rows[0][0]:
            cand = rows[0][0].strip()
            if any(kw in cand for kw in ["公司", "有限", "科技", "建材", "集团", "股份"]):
                company_name = cand
        # 2026-07-28 加：colspan cell 提取联系电话（page 178 超耐腐类）
        contact_phone = ""
        if not company_name and rows:
            for r in rows:
                if len(r) != 1 or not r[0]:
                    continue
                cand = r[0].strip()
                if "联系电话" in cand:
                    m_ph = re.search(r"联系电话[:：]\s*([0-9\-]+)", cand)
                    if m_ph:
                        contact_phone = m_ph.group(1)
                        break
        log_lines_local = []
        left_out = extract_new_material_4col(left, company_name, log_lines_local, contact_phone=contact_phone)
        right_out = extract_new_material_4col(right, company_name, log_lines_local, contact_phone=contact_phone)
        return left_out + right_out

    # 找真实表头
    real_header, header_row_idx = find_real_header(rows)
    if real_header is None:
        return []

    # 找各列索引（基于真实表头）
    # 2026-07-28: keywords 按 header 中最早出现位置排序（防 2D 拼接表错位）
    col_map = {}
    used_idx = set()
    for field, kws in NEW_MATERIAL_COL_KW.items():
        if field == "公司名":
            continue
        sorted_kws = sort_kws_by_pos(real_header, kws)
        for kw in sorted_kws:
            idx = find_col(real_header, kw)
            if idx >= 0 and idx not in used_idx:
                col_map[field] = idx
                used_idx.add(idx)
                break

    # 必须有产品名 + (价格 或 含税价)
    if "产品名" not in col_map or ("价格" not in col_map and "含税价" not in col_map):
        return []

    # 单价格表分流：根据"价格"列的 keyword 把数据分配到含税/不含税/通用价格
    # 真实场景：5 列 [材料名, 规格, 品牌, 单位, 不含税单价(元)] 单价格表
    # → "价格"命中 idx 4，但 "不含税单价(元)" 实际是不含税价
    price_idx = col_map.get("价格", -1)
    matched_price_kw = None
    if price_idx >= 0:
        sorted_price_kws = sort_kws_by_pos(real_header, NEW_MATERIAL_COL_KW["价格"])
        for kw in sorted_price_kws:
            if find_col(real_header, kw) == price_idx:
                matched_price_kw = kw
                break
        if matched_price_kw and "不含税" in matched_price_kw:
            col_map["不含税价"] = price_idx
            col_map["价格"] = -1
        elif matched_price_kw and "含税" in matched_price_kw:
            col_map["含税价"] = price_idx
            col_map["价格"] = -1
        else:
            # 中性 keyword（"价格(元)"/"单价(元)"）→ 默认归到"不含税价"（NM 厂商报价约定）
            col_map["不含税价"] = price_idx
            col_map["价格"] = -1

    # 判断是否双价格表（同时有"含税价"和"不含税价"两列，且列索引不同）
    incl_idx = col_map.get("含税价", -1)
    excl_idx = col_map.get("不含税价", -1)
    is_dual_price = incl_idx >= 0 and excl_idx >= 0 and incl_idx != excl_idx

    # 公司名: caption 优先 → 失败 fallback 到 row[0][0]（建华建材/易霖诚泰等公司表）
    cap = table.get("caption", "").strip()
    company_name = ""
    if any(kw in cap for kw in ["公司", "有限", "科技", "建材", "集团", "股份"]):
        company_name = cap
    elif rows and rows[0] and len(rows[0]) == 1 and rows[0][0]:
        cand = rows[0][0].strip()
        if any(kw in cand for kw in ["公司", "有限", "科技", "建材", "集团", "股份"]):
            company_name = cand

    # 2026-07-28 新增：colspan cell 公司名 + 电话
    # 模式 A（page 177 新三亚类，5 公司集团）：单 cell 含 "股份有限公司" + "电话:"
    # 模式 B（page 178 超耐腐类）：单 cell 含 "联系电话:" 但无 "股份"
    contact_phone = ""
    if not company_name and rows:
        for r in rows:
            if len(r) != 1 or not r[0]:
                continue
            cand = r[0].strip()
            # 模式 A：公司 + 电话
            if "股份" in cand and "电话" in cand:
                m_co = re.search(r"([一-龥]{2,20}股份有限公司)", cand)
                if m_co:
                    company_name = m_co.group(1)
                m_ph = re.search(r"(?:服务电话|电话)[:：]\s*([0-9\-]+)", cand)
                if m_ph:
                    contact_phone = m_ph.group(1)
                break
            # 模式 B：仅联系电话（无公司名，仅取电话）
            if not contact_phone and "联系电话" in cand:
                m_ph = re.search(r"联系电话[:：]\s*([0-9\-]+)", cand)
                if m_ph:
                    contact_phone = m_ph.group(1)
                    break

    out_rows = []
    last_real_name = ""  # 上一行真实产品名（用于跨行材料名继承）
    for cells in rows[header_row_idx + 1:]:  # 跳过表头行
        if not cells or all(not c.strip() for c in cells):
            continue
        # 2026-07-28 修：跳过 1-cell 行（caption/产品描述/公司信息，R7 只改 1 处）
        # 根因：新三亚 row 4-5 是产品描述+公司信息 1-cell 行，被通用 col_map 路径当成产品名
        # find_real_header 只跳过开头 1-cell，没处理末尾 1-cell
        # 1-cell 行没有"规格/单位/价格"等多列信息，列映射会全错位
        if len(cells) == 1:
            continue
        # 2026-07-29 增：错误 A 修复 — 子表 header 被当数据（R514「规格型号/品牌/价格(元)/执行标准」）
        # 现有 line 1247-1248 只比对完全相同的 header 行；这里补「部分 header cell 命中」判断
        # 任一 cell 与 real_header 任一 cell 完全相同（去 strip 比对），且 ≥3 个 → 这行是子表 header
        header_hit = sum(1 for c in cells if any(c.strip() == h.strip() for h in real_header))
        if header_hit >= 3 and len(cells) <= len(real_header) + 1:
            continue
        # 跳过表头行（与真实表头完全相同）
        if cells[:len(real_header)] == real_header[:len(cells)]:
            continue
        # 过滤元信息行（地址/电话/联系人）
        if is_meta_row(cells, col_map):
            continue

        def get(field):
            idx = col_map.get(field, -1)
            if idx < 0 or idx >= len(cells):
                return ""
            return clean_cell(cells[idx])

        # 跨行材料名继承（2026-07-28）
        # 场景1：二维拼接表后续行产品名=DN/Φ/× 开头，继承上一行真实产品名
        # 场景2（新增）：佩克类 row 漏产品名（OCR 丢产品名，整行短 header 1 列）
        #   例：header 5 列 [产品名称, 规格, 单位, 价格, 品牌]，row 4 列 [BM36, 件, 1044.69, 佩克]
        #   修法：cells 前补 1 个空，让 col_map 正常 mapping，产品名继承 last_real_name
        n_cols_row = sum(1 for c in cells if c and c.strip())
        n_cols_header = sum(1 for v in real_header if v and v.strip())
        name = get("产品名")
        # 2026-07-29 增：错误 B 修复 — 备注行当产品名（R495「备注:一、600以下桥架...」）
        if name.startswith("备注"):
            continue
        # 2026-07-29 增：错误 C/D 修复 — 桥架表 schema 错位（R487-R503 桥架表 ~17 行）
        # 桥架表真实结构：[规格=100*50, 横宽mm=100, 纵高mm=50, 厚度=0.8, ..., 价格]
        # 现有 col_map 按 [产品名, 规格, 品牌, 单位, 价格] 映射 → 「100*50」当产品名整列错位
        # 修复简化：name 是「数字*数字」格式 → 100% 是错位（NM 真实产品名都是中文）
        if name and re.match(r"^\d+\s*[*×]\s*\d+(\s*[*×]\s*\d+)*$", name.strip()):
            continue
        if n_cols_row < n_cols_header and last_real_name:
            cells = [""] + list(cells)
            name = last_real_name
        elif name and re.match(r"^[DNΦΦ×]+", name) and last_real_name:
            name = last_real_name
        elif name and not re.match(r"^[DNΦΦ×]+", name):
            last_real_name = name

        out_rows.append({
            "产品名": name,
            "规格": get("规格"),
            "品牌": get("品牌"),
            "单位": get("单位"),
            "价格(元)": get("价格"),
            "含税价(元)": get("含税价"),
            "不含税价(元)": get("不含税价"),
            "公司名": company_name,
            "联系人": get("联系人"),
            "电话": contact_phone or get("电话"),
            "执行标准": get("执行标准"),
        })

    # 三道锁：仅双价格表（防"价格列交换"静默致命错）
    # 锁 1: 含税价 idx != 不含税价 idx（列位置硬切，已在 col_map 互斥保证）
    # 锁 2: 比值范围 1.00-1.20（含税=不含税×(1+税率)，13% 税点应在 1.13 附近）
    # 锁 3: 单行比值与全局中位数偏离 < 20%
    if is_dual_price and out_rows and log_lines is not None:
        valid_ratios = []
        for r in out_rows:
            try:
                incl_f = float(r.get("含税价(元)", ""))
                excl_f = float(r.get("不含税价(元)", ""))
                if excl_f > 0 and incl_f > 0:
                    ratio = incl_f / excl_f
                    if 1.00 <= ratio <= 1.20:
                        valid_ratios.append(ratio)
            except (ValueError, TypeError):
                pass

        if valid_ratios:
            median = sorted(valid_ratios)[len(valid_ratios) // 2]
            for r in out_rows:
                try:
                    incl_f = float(r.get("含税价(元)", ""))
                    excl_f = float(r.get("不含税价(元)", ""))
                    if excl_f > 0 and incl_f > 0:
                        ratio = incl_f / excl_f
                        # 锁 2：比值超 [1.00, 1.20]
                        if ratio < 1.00 or ratio > 1.20:
                            r["含税价(元)"] = ""
                            r["不含税价(元)"] = ""
                            log_lines.append(
                                f"[step3] NM 三道锁-锁2 清空: 含税={incl_f}, 不含税={excl_f}, 比值={ratio:.3f} 超出 [1.00, 1.20]"
                            )
                            continue
                        # 锁 3：偏离中位数 > 20%
                        if abs(ratio - median) / median > 0.20:
                            r["含税价(元)"] = ""
                            r["不含税价(元)"] = ""
                            log_lines.append(
                                f"[step3] NM 三道锁-锁3 清空: 含税={incl_f}, 不含税={excl_f}, 比值={ratio:.3f} 偏离中位数 {median:.3f} > 20%"
                            )
                except (ValueError, TypeError):
                    pass

    return out_rows


# ============================================================
# 主流程
# ============================================================

def extract(classified_json, city, log_lines=None):
    """classified.json → extract.json

    跨页合并行为由城市模板的 cross_page_merge 字段控制：
      - true  → 走 merge_tables() 老逻辑（未来真跨页时启用）
      - false → 跳过合并（默认，2026-07-27 成都 06 实测 10 次合并全错合）
    """
    if log_lines is None:
        log_lines = []

    with open(classified_json, encoding="utf-8") as f:
        data = json.load(f)

    content_list = data["content_list"]
    md_content = data.get("md_content", "")

    # 2026-07-29 改：表 HTML 从 md_content 取（按首行签名匹配，非简单顺序）
    # 原因：MinerU 对多 section 合并表的列检测会漏行（如 idx=1303 DRPO 被吞 21 行）
    # md_content 是 MinerU 全文 markdown dump，table 块完整无截断
    # 关键：content_list 与 md_content 顺序不严格一致（同一 first-row 签名的表有多个）
    # 必须用 first-row 签名匹配 + used set 跟踪，避免重复占用
    md_tables = parse_md_tables(md_content) if md_content else []
    used_md_indices = set()
    n_md_used = 0
    n_table_body_fallback = 0

    # 1. 解析所有 table 的 HTML
    tables = []
    n_skip_labor = 0  # 2026-08-11 增：人工单价表 SKIP 计数
    for item in content_list:
        if item.get("type") != "table":
            continue
        section = item.get("section", "UNKNOWN")
        # 2026-08-11 增：人工单价表 SKIP（实物工程量人工单价 / 行业人工成本）
        # 用户原话："人工单价不需要提取啊" — 9 区横表是工种人工单价（瓦工/木工/...），不是材料价格
        # step2 把这 2 张表归到 MARKET（section_titles.json 的 MARKET_LABOR 分类没生效），
        # 必须在 step3 caption 关键词再挡一道
        cap_raw = item.get("table_caption", [])
        cap0 = (cap_raw[0] if isinstance(cap_raw, list) and cap_raw
                else (cap_raw if isinstance(cap_raw, str) else ""))
        if any(kw in cap0 for kw in ("实物工程量人工单价", "人工成本信息表", "工种人工")):
            n_skip_labor += 1
            continue
        # 2026-07-28 改：NEW_MATERIAL 也进解析（之前被 SKIP 一并过滤）
        if section not in ("MARKET", "PC", "NEW_MATERIAL"):
            continue  # SKIP 不解析

        # 跳过空 body 占位（md_content 无对应 <table>，不消费）
        body_in_content_list = item.get("table_body", "") or ""
        if not body_in_content_list.strip():
            continue

        # 取 HTML：md_content 优先（按 first-row 签名匹配），fallback 到 content_list.table_body
        md_idx = match_md_table(body_in_content_list, md_tables, used_md_indices)
        if md_idx is not None:
            html = md_tables[md_idx]["html"]
            used_md_indices.add(md_idx)
            n_md_used += 1
        else:
            html = body_in_content_list
            n_table_body_fallback += 1

        rows = parse_html_table(html)
        caption = item.get("table_caption", [])
        if isinstance(caption, list) and caption:
            caption = caption[0]
        else:
            caption = ""

        # 2026-08-11 增：横表（人工单价表续页）兜底 SKIP
        # 实物工程量人工单价表跨 p10-p13，后续页 caption 空 → caption 关键词 SKIP 漏过
        # 2 道防线：(1) 80%+ 行 ≤ 3 列；(2) 表头含「计量单位」+ 9 区名 +「人工单价」且数据行无材料名
        if rows and len(rows) > 5:
            short_rows = sum(1 for r in rows if len(r) <= 3)
            if short_rows / len(rows) > 0.8:
                n_skip_labor += 1
                continue
            # 9 区横表检测：前 2 行含「计量单位」+「人工单价」+ 多个区名
            # 实物工程量人工单价表结构: row 0=「计量单位|人工单价(元)」, row 1=9 区名行
            header_lines = "".join(str(c) for c in (rows[0] if rows else []))
            if len(rows) > 1:
                header_lines += "".join(str(c) for c in rows[1])
            if "计量单位" in header_lines and "人工单价" in header_lines:
                _districts = ["成都市", "新都区", "温江区", "双流区", "大邑县", "蒲江县", "都江堰市", "彭州市"]
                d_hit = sum(1 for d in _districts if d in header_lines)
                if d_hit >= 5:  # 至少 5 个区名命中 → 9 区横表
                    n_skip_labor += 1
                    continue

        tables.append({
            "section": section,
            "page": item.get("page_idx", 0),
            "caption": caption,
            "rows": rows,
        })

    print(f"[step3] 解析 {len(tables)} 张表（MARKET + PC + NM），md_content={n_md_used}, table_body fallback={n_table_body_fallback}")

    # 2. 跨页合并（按城市模板控制）
    try:
        yaml_data = load_city_yaml(city)
    except FileNotFoundError as e:
        print(f"[step3] ⚠️ {e} → 默认禁跨页合并")
        yaml_data = {}
    cross_page_merge = yaml_data.get("cross_page_merge", False)

    if cross_page_merge:
        tables = merge_tables(tables, log_lines)
        print(f"[step3] 合并后 {len(tables)} 张表")
    else:
        # 直接禁跨页合并：不做任何 merge_tables 调用
        # 成都 06 实测 10 次合并全错合 → 0 真合并 → 禁掉零损失
        print(
            f"[step3] 跨页合并已禁（{city} yaml cross_page_merge={cross_page_merge}），"
            f"{len(tables)} 张表保持独立"
        )

    # 3. 字段提取
    result = []
    for t in tables:
        if t["section"] == "MARKET":
            rows = extract_market_table(t)
        elif t["section"] == "PC":
            rows = extract_pc_table(t, log_lines)
        else:
            continue
        if rows:
            result.append({
                "section": t["section"],
                "page": t["page"],
                "caption": t["caption"],
                "rows": rows,
            })

    # 3b. 新型材料表（独立 section，2026-07-28 改用目录判定代替启发式）
    # 章节名含"建筑新型材料"/"新型材料"等 → step2 标 NEW_MATERIAL
    # 本步直接读取，不再用 page>=170 + 5列含材料名启发式
    new_material_result = {
        "section": "NEW_MATERIAL",
        "page": 0,
        "caption": "新型材料",
        "rows": [],
    }
    for t in tables:
        if t["section"] != "NEW_MATERIAL":
            continue
        rows = extract_new_material_table(t, content_list=content_list)
        new_material_result["rows"].extend(rows)
    if new_material_result["rows"]:
        result.append(new_material_result)
        print(f"[step3] 新型材料: {len(new_material_result['rows'])} 行")

    print(f"[step3] 提取 {sum(len(t['rows']) for t in result)} 行")

    # 4. 写 extract.json
    out_path = Path(classified_json).parent / "extract.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[step3] ✅ 写出: {out_path}")

    return out_path, log_lines


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python step3_extract.py <classified_json_path> <city>", file=sys.stderr)
        sys.exit(1)
    extract(sys.argv[1], sys.argv[2])
