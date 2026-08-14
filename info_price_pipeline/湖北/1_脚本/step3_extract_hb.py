"""step3_extract_hb.py - 湖北 step3 字段提取（2026-08-04 全新）

输入: classified.json (step2 输出)
输出: extract.json (含 sheet_data["MARKET"] + sheet_data["PC"])

设计（用户 2026-08-04 拍板）:
  Sheet 1 ("MARKET", 7 列) = Part 1 + Part 2 价格表, 垂直展开
       序号 | 城市 | 名称 | 规格 | 单位 | 含税价 | 除税价
       14 城 × 22 page-pair × ~33 行 = ~5000 行（实际）
  Sheet 2 ("PC", 6 列) = 装配式武汉 only
       序号 | 名称 | 含钢量(kg) | 单位 | 含税价(元) | 除税价(元)
  SKIP: 政策类 3 张 + 定额解答 1 张（step2 已分类，cleaner 过滤）

算法骨架 (来自 2026-08-04 指纹校验):
  1. 读 classified.json.content_list + md_content
  2. 从 md_content 抽 49 张 table 的 markdown HTML
  3. table[3..24]=Part1 22 张 (主+续配对), table[25..46]=Part2 22 张, table[47]=装配式
  4. 主表 8 cell (4 基础 + 2 城×2 价格)；续表 10 cell (5 城×2 价格)
  5. 主表 + 续表 配对 → 按行号对齐 → 7 城 × 7 列
  6. 章首过滤: row[0]='' AND row[1] 含 '材料' '水泥' '苗木' 等章节标题 → skip
  7. 序号全局连续 1→N（sheet 1），sheet 2 独立 1→N
  8. 装配式直接输出 6 列
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
from utils import ROOT  # ROOT = 湖北/, 复用路径机制

# 章首关键词（参考 v14 hb memory 的 v14-hb-abc-fix 经验）
# 2026-08-04 加「阀门」(p57 末段 row[0]='' AND row[1]='阀门' 漏过滤)
_CHAPTER_KEYWORDS = (
    "金属材料", "水泥", "砖瓦", "灰", "砂", "石", "木", "竹材",
    "玻璃", "砖", "砌块", "墙", "装饰", "涂料", "油漆", "门窗",
    "苗木", "混凝土", "装配式", "防水", "保温", "管材", "阀门",
)


def _looks_like_price(s):
    """s 看起来像价格（纯数字/小数）"""
    s = s.strip()
    return bool(s) and bool(re.fullmatch(r"[\d.,]+", s))


def _looks_like_price_or_empty(s):
    """s 是数字 OR 空字符串 (OCR 漏名称后 row[3] 是空或价格)"""
    s = s.strip()
    if not s:  # 空
        return True
    return bool(re.fullmatch(r"[\d.,]+", s))


# 2026-08-04 加: 单位列 LaTeX 后处理 (MinerU 把 m²/m³ 转 LaTeX 了)
# 2026-08-10 改: 升级 4 行 regex 覆盖 \mathrm{m}^{N} / \text{m}^{N} / \mathrm m^{N} / {\mathrm m}^{N} 等变种
_UNIT_LATEX_REPLACES = [
    ("${\\mathrm{m}}^{3}$", "m3"),
    ("${\\mathrm{m}}^{2}$", "m2"),
    ("$m^3$", "m3"),
    ("$m^2$", "m2"),
]

# 2026-08-04 加: 价格列 OCR 字符清洗 (去除 $/空格)
_PRICE_OCR_CHARS = re.compile(r"[\s$]+")


def _clean_unit(u):
    """单位列 LaTeX 后处理 (2026-08-10 升级 4 行 regex 覆盖全部变种)

    输出保持 m2/m3 数字后缀 (湖北格式), 跟原 4 行 replace 行为一致
    """
    s = str(u).strip()
    # 保留 4 行字面替换 (兜底 已有场景)
    for pat, repl in _UNIT_LATEX_REPLACES:
        s = s.replace(pat, repl)
    # 2026-08-10 增: 4 行 regex 覆盖所有 LaTeX 变种
    # 关键顺序：外层花括号版 (3/4) 必须先跑，否则无外层 regex 1 会抢匹配 → 外层 { 残留
    _latex_m_repl = lambda m: f"m{m.group(1)}"
    s = re.sub(r"\{\s*\\mathrm\s*\{?\s*m\s*\}?\s*\}\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, s)
    s = re.sub(r"\{\s*\\text\s*\{?\s*m\s*\}?\s*\}\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, s)
    s = re.sub(r"\\mathrm\s*\{?\s*m\s*\}?\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, s)
    s = re.sub(r"\\text\s*\{?\s*m\s*\}?\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, s)
    s = re.sub(r"\bm\s*\^\s*\{\s*(\d+)\s*\}", _latex_m_repl, s)
    return s


def _clean_price(p):
    """价格列 OCR 字符清洗 (2026-08-04 加: 英文逗号 → 英文小数点)

    OCR 常见 bug: 把小数点 . 误识别为英文逗号 ,
    例: '3186.37' 被识别为 '3186,37'
    """
    s = str(p).strip()
    if not s:
        return ""
    # 模式「数字,数字」: 这种是 OCR 把小数点当逗号 → 替换
    # HB 价格无千分位 (3000-7000 区间), 不会误伤
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    # 去空格 + $ 包围
    s = _PRICE_OCR_CHARS.sub("", s)
    return s


def _is_chapter_header(row_cells):
    """判定 row 是否是章首标题（不计入数据）"""
    if not row_cells:
        return False
    seq = row_cells[0].strip() if len(row_cells) > 0 else ""
    name = row_cells[1].strip() if len(row_cells) > 1 else ""
    if seq == "" and any(kw in name for kw in _CHAPTER_KEYWORDS):
        rest_cells = row_cells[2:]
        if all((c.strip() == "" for c in rest_cells)):
            return True
    return False


def _parse_html_table(html):
    """解析 markdown HTML <table> 块 → list[list[str]]"""
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


def _parse_main_table(html):
    """解析主表（Part 1/2 第 1 张）: 物理 8 cell（4 基础 + 2 城 × 2 价格）

    2026-08-04 增 OCR 容错: row[3] 是数字 → 视为 OCR 漏「材料名称」列
      兜底: name = 上一行 row[1] (HB 同系列连续); spec = row[1]; unit = row[2]
      根因: p21/p43/p55/p57 OCR 漏识别「热轧碳素结构钢工字钢(普通型)」等名称
    """
    rows_raw = _parse_html_table(html)
    if len(rows_raw) < 3:
        return [], []
    # row[0] = 城市名（colspan=2 但 HTML 抽 cell 物理 1 个, 4 基础 + 2 城市 = 6 td）
    # row[1] = 含税价/除税价 配对 = 4 td
    cities_from_row1 = rows_raw[0][4:]  # ["武汉市", "襄阳市"] 或 ["鄂州市", "孝感市"]
    sub_labels = rows_raw[1]              # 4 个 cell

    city_pairs = []
    for i, city in enumerate(cities_from_row1):
        incl = sub_labels[2*i] if 2*i < len(sub_labels) else "含税价"
        excl = sub_labels[2*i+1] if 2*i+1 < len(sub_labels) else "除税价"
        city_pairs.append((city, incl, excl))

    data_rows = rows_raw[2:]
    result = []
    last_valid_name = ""
    n_rescued = 0

    for row in data_rows:
        # 跳过空行
        if not row or all(c.strip() == "" for c in row):
            continue
        if _is_chapter_header(row):
            continue
        if len(row) < 4:
            continue

        # OCR 漏名称检测: row[3] 是数字（价格）或空字符串 (2026-08-04 扩展)
        if _looks_like_price_or_empty(row[3]):
            # 漏名称 row: ['35', '16~18', 't', '3768.90', '3336.80', ...]
            # 重映射: name=上一行, spec=row[1], unit=row[2], 价格从 row[3] 开始
            spec = row[1]
            unit = row[2]
            prices = []
            for i, (city, incl_lbl, excl_lbl) in enumerate(city_pairs):
                incl_idx = 3 + 2*i
                excl_idx = 3 + 2*i + 1
                incl = row[incl_idx] if incl_idx < len(row) else ""
                excl = row[excl_idx] if excl_idx < len(row) else ""
                prices.append((city, incl, excl))
            if not last_valid_name:
                # 上一行也没名称 → 整行丢弃（避免空名称污染）
                print(f"[step3 hb]   ⚠️ OCR 漏名称且无上一行可继承, 跳过 row[0]={row[0]}")
                continue
            n_rescued += 1
            result.append({
                "seq": row[0],
                "name": last_valid_name,
                "spec": spec,
                "unit": unit,
                "prices": prices,
            })
            continue

        # 正常解析: row[1]=name, row[2]=spec, row[3]=unit
        seq = row[0]
        name = row[1]
        spec = row[2]
        unit = row[3]
        prices = []
        for i, (city, incl_lbl, excl_lbl) in enumerate(city_pairs):
            incl_idx = 4 + 2*i
            excl_idx = 4 + 2*i + 1
            incl = row[incl_idx] if incl_idx < len(row) else ""
            excl = row[excl_idx] if excl_idx < len(row) else ""
            prices.append((city, incl, excl))
        if not seq and not name:
            continue
        if name.strip():
            last_valid_name = name.strip()
        result.append({
            "seq": seq, "name": name, "spec": spec, "unit": unit,
            "prices": prices,
        })

    if n_rescued:
        print(f"[step3 hb]   ↻ OCR 漏名称继承 {n_rescued} 行")
    return result, city_pairs


def _parse_continue_table(html):
    """解析续表: 物理 10 cell（5 城市 × 2 价格，无基础列）"""
    rows_raw = _parse_html_table(html)
    if len(rows_raw) < 2:
        return [], []
    header_row1 = rows_raw[0]  # 5 cells = 5 城市
    header_row2 = rows_raw[1]  # 10 cells = 含税/除税 配对

    cities_from_row1 = header_row1
    sub_labels = header_row2

    city_pairs = []
    for i, city in enumerate(cities_from_row1):
        incl = sub_labels[2*i] if 2*i < len(sub_labels) else "含税价"
        excl = sub_labels[2*i+1] if 2*i+1 < len(sub_labels) else "除税价"
        city_pairs.append((city, incl, excl))

    data_rows = rows_raw[2:]
    result = []
    for row in data_rows:
        # 续表章首行: colspan=10 全空, _parse_html_table 输出 1 cell = "", skip
        if len(row) == 1 and row[0].strip() == "":
            continue
        if len(row) < 2:
            continue
        prices = []
        for i, (city, incl_lbl, excl_lbl) in enumerate(city_pairs):
            incl_idx = 2*i
            excl_idx = 2*i+1
            incl = row[incl_idx] if incl_idx < len(row) else ""
            excl = row[excl_idx] if excl_idx < len(row) else ""
            prices.append((city, incl, excl))
        result.append({"prices": prices})
    return result, city_pairs


def _parse_pc_table(html):
    """解析装配式 table[47]（6 列: 序号+名称+含钢量+单位+含税+除税）"""
    rows_raw = _parse_html_table(html)
    if len(rows_raw) < 2:
        return []
    data_rows = rows_raw[1:]
    result = []
    for row in data_rows:
        if len(row) < 6:
            continue
        seq = row[0]
        name = row[1]
        steel_kg = row[2]
        unit = row[3]
        incl = row[4]
        excl = row[5]
        if not seq and not name:
            continue
        if _is_chapter_header(row):
            continue
        result.append({
            "seq": seq, "name": name, "steel_kg": steel_kg,
            "unit": unit, "incl": incl, "excl": excl,
        })
    return result


def _build_sheet1_from_pairs(main_tables_html, cont_tables_html, source_label):
    """主+续 page-pair 配对，按行对齐，垂直展开成 sheet 1 的 7 列行"""
    all_rows = []
    pair_count = min(len(main_tables_html), len(cont_tables_html))
    for pair_idx in range(pair_count):
        main_html = main_tables_html[pair_idx]
        cont_html = cont_tables_html[pair_idx]

        main_rows, main_cities = _parse_main_table(main_html)
        cont_rows, cont_cities = _parse_continue_table(cont_html)

        row_count = min(len(main_rows), len(cont_rows))
        n_filtered = 0
        for i in range(row_count):
            m = main_rows[i]
            c = cont_rows[i]
            all_cities = list(m["prices"]) + list(c["prices"])  # 主表 2 城 + 续表 5 城 = 7 城
            for (city, incl, excl) in all_cities:
                # 2026-08-04 加: 单位 LaTeX 清洗 + 价格 OCR 字符清洗
                incl_clean = _clean_price(incl)
                excl_clean = _clean_price(excl)
                # 2026-08-04 加: 两价都空 → 删 (任一非空保留)
                if not incl_clean.strip() and not excl_clean.strip():
                    n_filtered += 1
                    continue
                all_rows.append({
                    "name": m["name"],
                    "spec": m["spec"],
                    "unit": _clean_unit(m["unit"]),
                    "city": city,
                    "incl": incl_clean,
                    "excl": excl_clean,
                    "source_pair": f"{source_label}-{pair_idx}",
                })
        if n_filtered:
            print(f"[step3 hb]   ↻ {source_label} 过滤两价都空 {n_filtered} 行")
    return all_rows


def _classify_table_type(html):
    """按 row[0] 内容判断主/续表 (避免依赖 idx 奇偶)

    主表: row[0] = ['序号', '材料名称', '规格及型号', '单位', '<城市>', '<城市>']
    续表: row[0] = ['<城市>', '<城市>', '<城市>', '<城市>', '<城市>'] (无「序号」)
    """
    rows_raw = _parse_html_table(html)
    if not rows_raw:
        return "unknown"
    first_cell = rows_raw[0][0].strip() if rows_raw[0] else ""
    if first_cell == "序号":
        return "main"
    return "cont"


def _number_rows_globally(rows):
    """全局连续 1→N 编号"""
    for i, r in enumerate(rows, 1):
        r["seq"] = i
    return rows


def extract(classified_json, city, log_lines=None):
    """主入口: classified.json → extract.json

    Returns: extract.json 路径
    """
    classified_path = Path(classified_json)
    out_path = classified_path.parent / "extract.json"
    print(f"[step3 hb] 输入: {classified_path}")
    print(f"[step3 hb] 输出: {out_path}")

    with classified_path.open(encoding="utf-8") as f:
        classified = json.load(f)

    md_content = classified.get("md_content", "")
    all_tables_html = re.findall(r"<table>.*?</table>", md_content, re.DOTALL)

    if len(all_tables_html) < 48:
        print(f"[WARN] 只找到 {len(all_tables_html)} 个 HTML table（预期 48）")

    # 按 row[0] 内容启发式判断主/续表 (避免依赖 idx 奇偶)
    part1_main = []
    part1_cont = []
    part2_main = []
    part2_cont = []
    pc_html = None

    for idx in range(3, len(all_tables_html)):
        html = all_tables_html[idx]
        if idx == 47:
            pc_html = html
            continue
        table_type = _classify_table_type(html)
        if idx < 25:
            # Part 1 (table 3-24)
            if table_type == "main":
                part1_main.append(html)
            else:
                part1_cont.append(html)
        elif idx < 47:
            # Part 2 (table 25-46)
            if table_type == "main":
                part2_main.append(html)
            else:
                part2_cont.append(html)

    print(f"[step3 hb] Part 1 主表={len(part1_main)} 张, 续表={len(part1_cont)} 张")
    print(f"[step3 hb] Part 2 主表={len(part2_main)} 张, 续表={len(part2_cont)} 张")
    print(f"[step3 hb] 装配式: {'找到' if pc_html else '未找到'}")

    # Sheet 1
    sheet1_part1 = _build_sheet1_from_pairs(part1_main, part1_cont, "Part1")
    sheet1_part2 = _build_sheet1_from_pairs(part2_main, part2_cont, "Part2")
    sheet1_rows = _number_rows_globally(sheet1_part1 + sheet1_part2)
    print(f"[step3 hb] Sheet 1 (MARKET): {len(sheet1_rows)} 行")

    # Sheet 2
    sheet2_rows = []
    if pc_html:
        pc_raw = _parse_pc_table(pc_html)
        n_filtered_pc = 0
        for r in pc_raw:
            incl_clean = _clean_price(r["incl"])
            excl_clean = _clean_price(r["excl"])
            # 2026-08-04 加: 两价都空 → 删
            if not incl_clean.strip() and not excl_clean.strip():
                n_filtered_pc += 1
                continue
            sheet2_rows.append({
                "seq": 0,
                "name": r["name"],
                "spec": r.get("steel_kg", ""),
                "unit": _clean_unit(r["unit"]),
                "incl": incl_clean,
                "excl": excl_clean,
            })
        _number_rows_globally(sheet2_rows)
        if n_filtered_pc:
            print(f"[step3 hb]   ↻ PC 过滤两价都空 {n_filtered_pc} 行")
    print(f"[step3 hb] Sheet 2 (PC): {len(sheet2_rows)} 行")

    # 写 extract.json (step4 期望 schema: 顶层 table 列表)
    extract_data = [
        {
            "caption": "2026 年 4 月湖北省 14 市州材料价格信息 (Part 1+Part 2)",
            "section": "MARKET",
            "page": 20,
            "sheet_name": "2026-04湖北省14市州材料价格信息",
            "headers": ["序号", "城市", "名称", "规格", "单位", "含税价", "除税价"],
            "rows": sheet1_rows,
        },
        {
            "caption": "2026 年 4 月武汉市装配式建筑工程材料综合信息价",
            "section": "PC",
            "page": 64,
            "sheet_name": "2026-04武汉市装配式建筑工程材料综合信息价",
            "headers": ["序号", "名称", "含钢量(kg)", "单位", "含税价(元)", "除税价(元)"],
            "rows": sheet2_rows,
        },
    ]
    out_path.write_text(json.dumps(extract_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[step3 hb] ✅ 写出: {out_path}")

    if log_lines is not None:
        log_lines.append(f"[step3 hb] Sheet 1 (MARKET) {len(sheet1_rows)} 行")
        log_lines.append(f"[step3 hb] Sheet 2 (PC) {len(sheet2_rows)} 行")

    return str(out_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python step3_extract_hb.py <classified_json> [city]", file=sys.stderr)
        sys.exit(1)
    extract(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "湖北", [])
