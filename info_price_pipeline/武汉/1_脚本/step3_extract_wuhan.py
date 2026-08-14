"""step3_extract_wuhan.py - 武汉字段提取 (2026-08-04 建)

输入: result.json (OCR 缓存, 含 md_content)
输出: extract.json (3 sheet 数据)

设计（用户 2026-08-04 拍板）:
  Sheet 1 "标准材料" (schema A, 7 列) = table[0,1,2,3,5] 合并
       序号 | 名称 | 规格型号 | 单位 | 含税价(元) | 除税价(元) | 备注
  Sheet 2 "装配式" (schema B, 7 列含备注) = table[4]
       序号 | 名称 | 含钢量(kg) | 单位 | 含税价(元) | 除税价(元) | 备注
  Sheet 3 "节能门窗" (schema C, 10 列) = table[6]
       序号 | 名称 | 配用玻璃 | 传热系数 | 单位 | 含税价(元) | 除税价(元) | 安装费 | 检测费 | 备注

算法骨架 (2026-08-04 OCR 实测):
  1. md_content 抽 7 个 HTML <table>
  2. table 第一行 caption → sheet + schema 分配 (caption 关键词匹配, 读 caption_keywords.json):
     "建筑装饰/安装/市政/城建/海绵/商品混凝土/沥青混凝土/预拌砂浆/新型墙材" → A (标准材料, 7 列)
     "装配式建筑工程材料" → B (装配式, 7 列含备注)
     "节能门窗" → C (节能门窗, 10 列)
  3. 每个 table 内逐行扫描:
     - cells=1 (colspan) → caption/章首/编者按 → skip
     - cells=7 或 10 + row[0]='序号' + row[1]='名称' → 表头 → skip
     - cells=7 或 10 + row[0] 是数字 → 数据行 → 提取
     - cells 数不对 → skip
  4. 单位 LaTeX 清洗 (复用 hb 代码: $m^2$ → m2, $m^3$ → m3)
  5. 价格 OCR 字符清洗 (复用 hb 代码)
  6. 两价都空 → 删
  7. 每 sheet 独立 1, 2, 3... 编号 (PDF 原貌)
"""
import json
import re
import sys
from pathlib import Path

# Windows GBK 编码修复
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ROOT, load_config

# ============================================================
# 复用 hb 的清洗函数 (从 hb step3 拷过来)
# ============================================================

# 2026-08-04 复用 hb: 单位 LaTeX 清洗
# 2026-08-04 补: 武汉 PDF 含 $m^{3}$ (大括号形式)
# 2026-08-10 改: 升级 4 行 regex 覆盖 \mathrm{m}^{N} / \text{m}^{N} / \mathrm m^{N} / {\mathrm m}^{N} 等变种
# 关键陷阱: \mathrm 是字体命令, m 前后空格不终止 (LaTeX 语义); 用 \s* 覆盖
_UNIT_LATEX_REPLACES = [
    ("${\\mathrm{m}}^{3}$", "m3"),
    ("${\\mathrm{m}}^{2}$", "m2"),
    ("$m^3$", "m3"),
    ("$m^2$", "m2"),
    ("$m^{3}$", "m3"),
    ("$m^{2}$", "m2"),
]
_PRICE_OCR_CHARS = re.compile(r"[\s$]+")


def _clean_unit(u):
    """单位 LaTeX 后处理 (2026-08-10 升级 4 行 regex 覆盖全部变种)

    输出保持 m2/m3 数字后缀 (武汉/广州/湖北格式), 跟原 6 行 replace 行为一致

    2026-08-11 增: 范围最广覆盖 (复北京/成都/重庆数学符号 + 广州英寸清洗)
    """
    import html as _html  # 2026-08-11 增: HTML 实体反转义
    s = str(u).strip()
    # 保留字面替换 (兜底 6 行已有场景)
    for pat, repl in _UNIT_LATEX_REPLACES:
        s = s.replace(pat, repl)
    # 2026-08-10 增: 4 行 regex 覆盖 \mathrm{m}^{N} / \text{m}^{N} / \mathrm m^{N} / {\mathrm m}^{N} 等变种
    # 关键顺序：外层花括号版 (3/4) 必须先跑，否则无外层 regex 1 会抢匹配 → 外层 { 残留
    _latex_m_repl = lambda m: f"m{m.group(1)}"  # 输出 m2/m3 数字后缀
    s = re.sub(r"\{\s*\\mathrm\s*\{?\s*m\s*\}?\s*\}\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, s)
    s = re.sub(r"\{\s*\\text\s*\{?\s*m\s*\}?\s*\}\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, s)
    s = re.sub(r"\\mathrm\s*\{?\s*m\s*\}?\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, s)
    s = re.sub(r"\\text\s*\{?\s*m\s*\}?\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, s)
    # OCR 残段: m^2 单独出现 (去除 $ 包裹后)
    s = re.sub(r"\bm\s*\^\s*\{\s*(\d+)\s*\}", _latex_m_repl, s)
    # 2026-08-11 增: 数学符号 (复北京/成都/重庆)
    s = re.sub(r"\\Phi\b", "Φ", s)
    s = re.sub(r"\\times\b", "×", s)
    s = re.sub(r"\\cdot\b", "·", s)
    s = re.sub(r"\\pm\b", "±", s)
    s = re.sub(r"\\le\b", "≤", s)
    s = re.sub(r"\\ge\b", "≥", s)
    s = re.sub(r"\\ne\b", "≠", s)
    # 2026-08-11 增: HTML 实体反转义 + 英寸清洗 (复广州 _clean_inch)
    s = _html.unescape(s)
    s = re.sub(r"\\frac\{(\d+)\}\{(\d+)\}", r"\1/\2", s)
    s = s.replace("⁄", "/")
    s = s.replace("$", "")
    return s


def _clean_price(p):
    """价格列 OCR 字符清洗 (复用 hb 2026-08-04)"""
    s = str(p).strip()
    if not s:
        return ""
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    s = _PRICE_OCR_CHARS.sub("", s)
    return s


# ============================================================
# Schema dispatch (2026-08-04 改: caption 关键词匹配, 不再写死 idx)
# ============================================================

# 读 武汉/6_配置/caption_keywords.json + 根 6_配置/ (load_config 自动合并)
_CAPTION_KW = load_config("caption_keywords.json")

# Sheet 顺序 (按关键词 JSON key 顺序, 标准→PC→节能, 排除 _comment 等注释字段)
_SHEET_ORDER = [k for k in _CAPTION_KW.keys() if not k.startswith("_")]

# 表头 (按 sheet 名, 来自 caption_keywords.json 的 schema 字段)
_HEADERS = {
    "标准材料": ["序号", "名称", "规格型号", "单位", "含税价(元)", "除税价(元)", "备注"],
    "装配式": ["序号", "名称", "含钢量(kg)", "单位", "含税价(元)", "除税价(元)", "备注"],
    "节能门窗": ["序号", "名称", "配用玻璃", "传热系数", "单位", "含税价(元)", "除税价(元)", "安装费", "检测费", "备注"],
}


def _get_table_caption(html):
    """抽 table 第一行 (cells=1 的 caption 行)"""
    m = re.search(r"<tr>(.*?)</tr>", html, re.DOTALL)
    if not m:
        return ""
    cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", m.group(1), re.DOTALL)
    cleaned = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
    if len(cleaned) == 1:
        return cleaned[0]
    return ""


def _match_caption_to_sheet(caption):
    """caption 字符串 → sheet 名 (按 _CAPTION_KW 关键词匹配)

    返回 (sheet_name, schema, n_cols)
    都没命中 → 默认 ("标准材料", "A", 7)
    """
    for sheet_name, info in _CAPTION_KW.items():
        if sheet_name.startswith("_"):
            continue
        for kw in info.get("kw", []):
            if kw in caption:
                return (
                    sheet_name,
                    info.get("schema", "A"),
                    info.get("n_cols", 7),
                )
    return ("标准材料", "A", 7)  # 默认兜底


def _parse_html_table(html):
    """解析 markdown HTML <table> → list[list[str]] (复用 hb step3)"""
    rows = []
    trs = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
    for tr in trs:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.DOTALL)
        cleaned = []
        for cell in cells:
            text = re.sub(r"<[^>]+>", "", cell).strip()
            cleaned.append(text)
        rows.append(cleaned)
    return rows


def _is_header_row(row, n_cols):
    """识别表头行 (cells 数 + row[0]='序号' + row[1]='名称')"""
    return len(row) == n_cols and row[0] == "序号" and row[1] == "名称"


def _is_chapter_or_caption(row):
    """cells=1 单 cell (caption/章首/编者按) → True"""
    return len(row) == 1


def _looks_like_seq(s):
    """row[0] 看起来像序号 (纯数字 1, 2, 3...)"""
    s = s.strip()
    return bool(s) and bool(re.fullmatch(r"\d+", s))


def _extract_row_a(row):
    """schema A: 序号|名称|规格型号|单位|含税价|除税价|备注 (7 列)"""
    return {
        "seq": row[0].strip(),
        "name": row[1].strip(),
        "spec": row[2].strip(),
        "unit": _clean_unit(row[3]),
        "incl": _clean_price(row[4]),
        "excl": _clean_price(row[5]),
        "remark": row[6].strip() if len(row) > 6 else "",
    }


def _extract_row_b(row):
    """schema B: 序号|名称|含钢量(kg)|单位|含税价|除税价|备注 (7 列, 复用 A 字段)"""
    return _extract_row_a(row)  # schema B 字段顺序兼容 A


def _extract_row_c(row):
    """schema C: 序号|名称|配用玻璃|传热系数|单位|含税价|除税价|安装费|检测费|备注 (10 列)"""
    return {
        "seq": row[0].strip(),
        "name": row[1].strip(),
        "glass": row[2].strip(),         # 配用玻璃
        "k_factor": row[3].strip(),     # 传热系数
        "unit": _clean_unit(row[4]),
        "incl": _clean_price(row[5]),
        "excl": _clean_price(row[6]),
        "install_fee": row[7].strip() if len(row) > 7 else "",
        "inspect_fee": row[8].strip() if len(row) > 8 else "",
        "remark": row[9].strip() if len(row) > 9 else "",
    }


_EXTRACTORS = {"A": _extract_row_a, "B": _extract_row_b, "C": _extract_row_c}


def _number_rows_independently(rows):
    """每 sheet 独立 1, 2, 3... 编号 (PDF 原貌)"""
    for i, r in enumerate(rows, 1):
        r["seq"] = i
    return rows


def extract(result_json, city, log_lines=None):
    """主入口: result.json → extract.json"""
    result_path = Path(result_json)
    out_path = result_path.parent / "extract.json"
    print(f"[step3 武汉] 输入: {result_path}")
    print(f"[step3 武汉] 输出: {out_path}")

    with result_path.open(encoding="utf-8") as f:
        data = json.load(f)
    md_content = data["results"]["upload"]["md_content"]

    tables_html = re.findall(r"<table>.*?</table>", md_content, re.DOTALL)
    print(f"[step3 武汉] 找到 {len(tables_html)} 个 HTML <table>")

    if len(tables_html) < 7:
        print(f"[WARN] 只找到 {len(tables_html)} 个 table (预期 7)")

    # 按 sheet 累积行
    sheets = {name: [] for name in _SHEET_ORDER}
    n_header_skip = 0
    n_chapter_skip = 0
    n_data_rows = 0
    n_empty_price_skip = 0

    for idx, html in enumerate(tables_html):
        # 2026-08-04 改: caption dispatch (不再写死 idx 映射)
        caption = _get_table_caption(html)
        sheet_name, schema, n_cols = _match_caption_to_sheet(caption)
        print(f"[step3 武汉]   table[{idx}] caption='{caption[:40]}...' → sheet={sheet_name} schema={schema} n_cols={n_cols}")
        rows_raw = _parse_html_table(html)
        extractor = _EXTRACTORS[schema]

        for row in rows_raw:
            if not row or all(c.strip() == "" for c in row):
                continue
            # 1) caption / 章首 / 编者按 → skip
            if _is_chapter_or_caption(row):
                n_chapter_skip += 1
                continue
            # 2) 表头 → skip
            if _is_header_row(row, n_cols):
                n_header_skip += 1
                continue
            # 3) cells 数不对 → skip
            if len(row) != n_cols:
                continue
            # 4) 数据行: row[0] 必须是纯数字
            if not _looks_like_seq(row[0]):
                continue
            # 5) 两价都空 → 删 (hb 复用)
            incl = _clean_price(row[n_cols - 3])
            excl = _clean_price(row[n_cols - 2])
            if not incl.strip() and not excl.strip():
                n_empty_price_skip += 1
                continue
            # 提取
            sheets[sheet_name].append(extractor(row))
            n_data_rows += 1

    print(f"[step3 武汉] 表头 skip: {n_header_skip} 行")
    print(f"[step3 武汉] caption/章首 skip: {n_chapter_skip} 行")
    print(f"[step3 武汉] 两价都空 skip: {n_empty_price_skip} 行")
    print(f"[step3 武汉] 有效数据行: {n_data_rows}")

    # 每 sheet 独立编号
    for name in _SHEET_ORDER:
        _number_rows_independently(sheets[name])
        print(f"[step3 武汉]   Sheet '{name}' ({_HEADERS[name][0]}...{len(_HEADERS[name])} 列): {len(sheets[name])} 行")

    # 写 extract.json (step6 期望 schema: 顶层 sheet 列表)
    extract_data = []
    for name in _SHEET_ORDER:
        extract_data.append({
            "sheet": name,
            "headers": _HEADERS[name],
            "rows": sheets[name],
        })
    out_path.write_text(json.dumps(extract_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[step3 武汉] ✅ 写出: {out_path}")

    if log_lines is not None:
        log_lines.append(f"[step3 武汉] 表头 skip {n_header_skip}, 章首 skip {n_chapter_skip}, 空价 skip {n_empty_price_skip}")
        for name in _SHEET_ORDER:
            log_lines.append(f"[step3 武汉] Sheet '{name}': {len(sheets[name])} 行")

    return str(out_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python step3_extract_wuhan.py <result.json> [city]", file=sys.stderr)
        sys.exit(1)
    extract(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "武汉", [])