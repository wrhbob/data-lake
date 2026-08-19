"""step3_extract_cq.py - 重庆专属字段提取（2026-07-29 重写版）

约束（per-city 独立架构）：
  - 0 改动成都 step3_extract.py / step3_extract_hb.py
  - 重庆独立运行（重庆\1_脚本\）
  - 用 utils.py + 重庆市.yaml

输入：classified.json（content_list 含 section 字段）
输出：extract.json → 给 step4_clean.py

核心设计（按用户 7 条新需求）：
  1. 只提 2 个 section：MARKET + NEW_MATERIAL（其他全 SKIP）
  2. Sheet 1 市场信息价（区县 6 列：序号|材料名|规格|单位|不含税价|备注）
  3. Sheet 2 行业推荐（6 列 + 填报人/联系方式）
  4. 嵌套表头处理：
     - 章节小标题（"一、水泥及商品混凝土" / "二、砖、砂、石"）→ 过滤
     - rowspan="N" 材料名（如"普通商品混凝土"）→ forward inherit
     - 备注 forward inherit（同材料名共享备注）
  5. 行业推荐跨页：每页底部"填报人/联系方式"→ 注入该页所有行
  6. 注入 市/区 字段（中心城区 vs 区名 vs 行业协会）
"""
import json
import re
import sys

# 2026-08-10 增：LaTeX 上标映射（北京 P0-1 同步），clean_cell 内 _strip_latex 用
UNICODE_SUPERSCRIPT = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ROOT


def load_city_yaml(city="重庆市"):
    """读 6_配置/城市模板/重庆市.yaml"""
    import yaml
    candidates = [
        ROOT / "6_配置" / "城市模板" / f"{city}.yaml",
        ROOT / "6_配置" / "城市模板" / f"{city}市.yaml",
    ]
    for path in candidates:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError(f"未找到: {candidates[0]}")


# ============================================================
# HTML 解析层
# ============================================================

def clean_cell(text):
    """strip HTML 标签 + LaTeX 公式 → 人话"""
    if not text:
        return ""
    # HTML 标签
    text = re.sub(r"<(b|i|u|strong|em)>(.*?)</\1>", r"\2", text, flags=re.DOTALL)
    text = re.sub(r"</?(b|i|u|strong|em)>", "", text)
    # 2026-08-10 增：4 行 regex 先跑（必须在 ^{2} → ² 之前，否则末尾 \^ 期望字面 ^ 失败）
    # 关键顺序：外层花括号版 (3/4) 必须先跑，否则无外层 regex 1 会抢匹配 → 外层 { 残留
    _latex_m_repl = lambda m: f"m{UNICODE_SUPERSCRIPT.get(m.group(1), '^'+m.group(1))}"
    text = re.sub(r"\{\s*\\mathrm\s*\{?\s*m\s*\}?\s*\}\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, text)
    text = re.sub(r"\{\s*\\text\s*\{?\s*m\s*\}?\s*\}\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, text)
    text = re.sub(r"\\mathrm\s*\{?\s*m\s*\}?\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, text)
    text = re.sub(r"\\text\s*\{?\s*m\s*\}?\s*\^\s*\{?\s*(\d+)\s*\}?", _latex_m_repl, text)
    # LaTeX 公式 ($...$) — 整段处理
    # 1) 移除 $...$ 边界后处理内部 LaTeX 命令
    def _strip_latex(s):
        # 2026-08-10 改：4 行 regex 提到顶层入口（clean_cell 函数体开头），这里不重复
        # 2026-08-11 增: 英寸分数清洗 (必须在 line 93 \\xxx 删之前, 否则 \frac 被吃 → {1}{2} → 12")
        s = re.sub(r"\\?frac\{(\d+)\}\{(\d+)\}", r"\1/\2", s)
        s = s.replace("⁄", "/")
        # 希腊字母 / 数学符号
        s = re.sub(r"\\times", "×", s)
        s = re.sub(r"\\cdot", "·", s)
        s = re.sub(r"\\phi", "φ", s)
        s = re.sub(r"\\delta", "δ", s)
        s = re.sub(r"\\Delta", "Δ", s)
        s = re.sub(r"\\pm", "±", s)
        s = re.sub(r"\\le\b", "≤", s)
        s = re.sub(r"\\ge\b", "≥", s)
        # 2026-07-29 v5b 增: 重庆 p127 PDF 用 \geqslant / \leqslant (变体, 不是 \ge / \le)
        # 之前漏了这两个 → ≥ ≤ 符号丢失, 备注变成 "120MPa" 而非 "≥120MPa"
        s = re.sub(r"\\geqslant", "≥", s)
        s = re.sub(r"\\leqslant", "≤", s)
        s = re.sub(r"\\ne\b", "≠", s)
        s = re.sub(r"\\approx", "≈", s)
        s = re.sub(r"\\sim", "~", s)
        s = re.sub(r"\\Rightarrow", "⇒", s)
        s = re.sub(r"\\rightarrow", "→", s)
        s = re.sub(r"\\rightarrow", "→", s)
        s = re.sub(r"\\leftarrow", "←", s)
        # \mathrm{xxx} / \bf{xxx} / \vec{xxx} → xxx（去包裹层）
        s = re.sub(r"\\(?:mathrm|mathbf|mathit|vec|bm|text|mathit|operatorname)\{([^{}]*)\}", r"\1", s)
        # 剩余 \xxx 命令 → 删（保留字母+数字）
        s = re.sub(r"\\[a-zA-Z]+\s*", "", s)
        # \# → #
        s = s.replace("\\#", "#").replace("\\$", "$").replace("\\%", "%")
        # x^{2} → x²  /  x^{3} → x³
        s = re.sub(r"\^\{2\}", "²", s)
        s = re.sub(r"\^\{3\}", "³", s)
        s = re.sub(r"\^\{0\}", "", s)
        s = re.sub(r"\^\{([^{}])\}", r"\1", s)
        # x_2 / x_{2} → x2
        s = re.sub(r"_\{([^{}]+)\}", r"\1", s)
        s = re.sub(r"_([a-zA-Z0-9])", r"\1", s)
        # \, \; \! \  → 空格
        s = re.sub(r"\\[ ,;!]", " ", s)
        # {} → 空
        s = re.sub(r"[{}]", "", s)
        return s
    # 整段 $...$ 模式
    text = re.sub(r"\$([^$]*)\$", lambda m: _strip_latex(m.group(1)), text)
    # 单美元残留（成对但没匹到）→ 删
    text = text.replace("$", "")
    # 数字^{2/3} 残留（无 $ 包裹）
    text = re.sub(r"\^\{2\}", "²", text)
    text = re.sub(r"\^\{3\}", "³", text)
    text = re.sub(r"\^2\b", "²", text)
    text = re.sub(r"\^3\b", "³", text)
    # mm2/mm3 残留
    text = re.sub(r"\bmm\^2\b", "mm²", text)
    text = re.sub(r"\bmm\^3\b", "mm³", text)
    text = re.sub(r"\bm2\b", "m²", text)
    text = re.sub(r"\bm3\b", "m³", text)
    # 2026-08-11 增: 英寸清洗 (复广州 _clean_inch, 范围最广覆盖所有 LaTeX/HTML 变种)
    #   $\frac{1}{2}"$ → 1/2"      LaTeX 分数英寸
    #   21⁄2&quot;     → 21/2"     Unicode 分数 + HTML 实体
    #   2&quot;        → 2"        整数英寸
    import html as _html  # 2026-08-11 增
    text = re.sub(r"\\?frac\{(\d+)\}\{(\d+)\}", r"\1/\2", text)  # \\? 容错
    text = text.replace("⁄", "/")
    text = _html.unescape(text)
    text = text.replace("$", "")
    # 多余空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_html_table(html, expand_colspan=True, expand_rowspan=True):
    """HTML → list of rows（展开 colspan/rowspan）

    关键语义（PDF HTML 实际格式，2026-07-29 修正）：
      - row 1: <td rowspan="N">水泥</td>...（N=2 → row 2 不再有 col 1 的 td）
      - row 2: <td>2</td><td>P·O 42.5级(袋装)</td>...（只有 5 个 td，比 row 1 少 1）
      → row 2 的 col 1 应该填"水泥"（继承 row 1 的 rowspan 占位值）

    正确逻辑：先按 colspan 解析所有 raw rows → 然后对每个 row[i]，
               扫描 row[k] (k<i) 的 rowspan > 1，**插入** row[k][j] 的 text
               到 row[i] 对应 col 位置（不是 row[i] 自己凭空加空 cell）。
    """
    if not html:
        return []
    tr_pat = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
    td_pat = re.compile(r"<td([^>]*)>(.*?)</td>", re.DOTALL)

    # 第一遍：解析每行 raw cells（含 colspan/rowspan 信息）
    raw_rows = []  # [[(text, colspan, rowspan), ...], ...]
    for tr in tr_pat.findall(html):
        cells = []
        for attrs, inner in td_pat.findall(tr):
            text = clean_cell(inner)
            colspan = 1
            m = re.search(r'colspan\s*=\s*"?(\d+)"?', attrs)
            if m:
                colspan = max(1, int(m.group(1)))
            rowspan = 1
            m = re.search(r'rowspan\s*=\s*"?(\d+)"?', attrs)
            if m:
                rowspan = max(1, int(m.group(1)))
            cells.append([text, colspan, rowspan])
        raw_rows.append(cells)

    # 第二遍：rowspan 展开 → 在 row[i] 缺 td 的 col 插入 row[k] 的 cell 值
    if expand_rowspan:
        for i in range(len(raw_rows)):
            cells = raw_rows[i]
            # 收集上方 row[k] 的 rowspan 占位信息
            insertions = []  # [(col_pos, text, colspan)]
            for k in range(i):
                k_col = 0
                for j_k, (text_k, colspan_k, rowspan_k) in enumerate(raw_rows[k]):
                    if rowspan_k > 1 and k + rowspan_k > i:
                        insertions.append((k_col, text_k, colspan_k))
                    k_col += colspan_k
            if not insertions:
                continue
            insertions.sort(key=lambda x: x[0])
            # 合并 row[i] cells + insertions（按 col 顺序穿插，正确处理 colspan+rowspan 组合）
            new_cells = []
            ins_idx = 0
            cell_idx = 0
            cur_col = 0
            while ins_idx < len(insertions) or cell_idx < len(cells):
                next_ins_col = insertions[ins_idx][0] if ins_idx < len(insertions) else float('inf')
                # cell 优先条件：cur_col 还不到下一个 ins 位置 或 ins 已尽
                if cell_idx < len(cells) and (cur_col < next_ins_col or ins_idx >= len(insertions)):
                    text, colspan, rowspan = cells[cell_idx]
                    new_cells.append([text, colspan, rowspan])
                    cur_col += colspan
                    cell_idx += 1
                elif ins_idx < len(insertions):
                    # ins 优先（cur_col 已到 ins 位置 或 cell 已尽）
                    text, colspan = insertions[ins_idx][1], insertions[ins_idx][2]
                    new_cells.append([text, colspan, 1])
                    cur_col += colspan
                    ins_idx += 1
            raw_rows[i] = new_cells

    # 第三遍：转成 list of strings（含 colspan 展开）
    out = []
    for cells in raw_rows:
        row = []
        for text, colspan, _ in cells:
            row.append(text)
            if expand_colspan and colspan > 1:
                row.extend([""] * (colspan - 1))
        if row:
            out.append(row)
    return out


def parse_md_tables(md_content):
    """md_content 全文 HTML 流 → 提取所有 <table>...</table> 块"""
    if not md_content:
        return []
    tables = []
    pos = 0
    while True:
        start = md_content.find("<table>", pos)
        if start < 0:
            break
        end = md_content.find("</table>", start)
        if end < 0:
            break
        tables.append(md_content[start:end + 8])
        pos = end + 8
    return tables


# ============================================================
# 章节小标题识别
# ============================================================

# 匹配："一、水泥及商品混凝土" / "二、砖、砂、石" / "1. 玻璃" / "1、xxx"
SECTION_SUBTITLE_RE = re.compile(
    r"^\s*([一二三四五六七八九十]+|\d+)[、\.]\s*\S+"
)


def is_section_subtitle(text):
    """是否为章节小标题（要过滤的）"""
    if not text:
        return False
    return bool(SECTION_SUBTITLE_RE.match(text))


# ============================================================
# 2026-08-11 P0 增：主章节标题检测（修 26.1 苗木 sheet 错位）
# ============================================================
# 26.1 OCR 把 印刷页 38-64 (玻璃+石材+金属+水箱+苗木) 合并到 item 515 单表 (948 行)。
# 原代码 body 关键字 "苗木" 判断 → 整表 924 行全标 苗木 → 苗木 sheet 错位
# 根因：跨章节混表 + 表级关键字检查，无法识别行级段落切换
# 修：行级 segment 跟踪，精确列举主章节标题（不用 regex, OCR 漂移风险大）。
# 遇 MAIN_SECTION_HEADERS → 更新 segment_state["current_segment"] → 后续行 _segment=新值
# 4 个补丁坑（用户同意 2026-08-11）：
#   1. 主标题列举 (本 dict, 最小集)
#   2. cap 优先 (dispatch 时 cap_label != None 覆盖 _segment)
#   3. NM 重置 (进入 NEW_MATERIAL 段前 segment_state["current_segment"]="default")
#   4. 状态独立 (segment_state 与 NM 的 prev_filing/prev_contact 互不干扰)
# 字段错位（8 col 苗木 schema 套 6 col 玻璃 schema）下次修，本次先让 苗木 进对 sheet
MAIN_SECTION_HEADERS = {
    # PART 1 综合价格信息 27 个主章节 (26.1/26.2/26.6 一致, 取自 dump colspan cell)
    "一、水泥及制品": "default",
    "二、砖、砂、石": "default",
    "三、湿拌商品砂浆": "default",
    "四、干混商品砂浆": "default",
    "五、黑色及有色金属": "default",
    "六、木、竹材料及其制品": "default",
    "七、水泥、砖瓦灰砂石及混凝土制品": "default",
    "八、玻璃及玻璃制品": "default",
    "九、装饰石材及石材制品": "default",
    "十、墙面、顶棚及屋面饰面材料": "default",
    "十一、保温、吸声材料及石棉制品": "default",
    "十二、龙骨、龙骨配件": "default",
    "十三、幕墙及配件": "default",
    "十四、门窗": "default",
    "十五、橡胶、塑料及非金属材料": "default",
    "十六、油漆、涂料": "default",
    "十七、油品、化工原料及胶粘材料": "default",
    "十八、防水材料": "default",
    "十九、五金制品": "default",
    "二十、管材(包含各类非金属管、复合管)": "default",
    "二十一、阀门": "default",
    "二十二、通风、空调及消防器材": "default",
    "二十三、电缆及光纤光缆": "default",
    "二十四、电工器材": "default",
    "二十五、配电装置及高压电器": "default",
    "二十六、成型加工制品": "default",
    "二十七、苗木": "苗木",  # 唯一 SPECIAL：行级 _segment=苗木 → step6 单出 sheet
    # PART 2 行业推荐价格信息 (城市道路桥梁) 4 个主章节
    # 注意："一、黑色及有色金属" 与 PART 1 的 "五、黑色及有色金属" 名相似但前缀不同 (无冲突)
    "一、黑色及有色金属": "default",
    "二、轨枕": "default",
    "三、扣件": "default",
    "四、道岔": "default",
}


# 2026-08-11 增：item 边界 pre-scan — body 含 苗木 信号才保留 segment state, 否则重置
# 修 page 74 (装配式, 11 行) + page 75 (道路桥梁, 20 行) 被 item 515 苗木 state 漏标
# 这些 item 的 body 没有 MAIN_SECTION_HEADERS 匹配 + 没有 苗木 子标题, 必须边界重置
# 26.1 item 691 (印刷页 65, 苗木续) body 含 "2、花灌木" → 保留 state → 全 201 行 _segment=苗木 ✓
# 26.2 p=76 / 26.6 p=91 body 含 "二十七、苗木" → 保留 state → 184 行 _segment=苗木 ✓
SEEDLING_BODY_SIGNALS = (
    "二十七、苗木",   # 主章节
    "1、乔木",        # 子章节 (含 1、)
    "2、花灌木",      # 子章节
    "3、棕榈",        # 子章节
    "4、地被",        # 子章节
)


# 2026-08-11 增：表头切换机制（修 26.1 MARKET 3 错位）
# 触发：body 内遇到表头行 → 切换 schema (只换 schema, 上下文沿用)
# 3 道防线：关键词 ≥ 2 + 数字密度 < 30% + 字符长度 < 30
HEADER_SCHEMA_PATTERNS = {
    "market_8col_seedling": {
        "must_have": [],   # 2026-08-11 v12 改: 放宽 (26.2/26.6 R0 无 株高/胸径 关键词)
        "any_of": ["材料名称", "品名"],   # 26.1 苗木表头用 "品名", 26.2/26.6 也用 "品名"
        "n_cols_options": [8, 9, 12, 13],  # 26.1 实测 9 列 (含 1 列 padding)
        "no_have": ["含税价(元)", "填报人", "联系方式"],  # 排除 8col_district (有"含税价(元)") 和 recommend_6col_assoc
    },
    "market_8col_district": {
        "must_have": ["材料名称", "规格", "单位", "含税价(元)", "不含税价(元)"],  # 2026-08-11 v12 改: 含税→含税价(元) 整词避免子串误匹
        "any_of": ["序号"],
        "n_cols_options": [8, 9, 10],  # 26.1 荣昌区 8 列 (2 区域 4 价格)
        "no_have": ["株高", "胸径"],  # 排除 8col_seedling
    },
    "market_6col_district": {
        "must_have": ["材料名称", "规格", "单位", "不含税"],
        "any_of": ["备注", "市/区"],
        "n_cols_options": [6, 7, 8, 9, 10],  # 26.1 实测 9 列 (含 2 列 padding)
    },
    "market_5col_district": {
        "must_have": ["材料名称", "规格", "单位", "不含税"],
        "any_of": ["序号"],
        "n_cols_options": [5, 6],  # 26.1 实测 6 列 (含 1 列 padding)
        "no_have": ["备注"],
    },
    "market_5col_seedling": {
        "must_have": ["规格", "单位", "不含税"],
        "any_of": ["品名", "材料名称"],
        "n_cols_options": [5, 6, 7, 8],  # 26.1 实测 8 列 (含 3 列 padding)
    },
    "recommend_6col_assoc": {
        "must_have": ["材料名称", "规格", "单位", "不含税"],
        "any_of": ["填报人", "联系方式", "型号"],
        "n_cols_options": [6, 7, 8, 9, 10],  # 26.1 实测 7 列 (含 1 列 padding)
    },
}


def header_detector(row):
    """3 道防线识别表头行 → 返回匹配的 schema_name 或 None

    漏洞 1 应对: 表头行检测出来了 → skip 当前行 (不当作数据)
    漏洞 2 应对: 数据行含关键词不会被误判 (3 道防线 + 数字密度排除)
    漏洞 3 应对: 是上层 wrapper schema_state 处理的, 这里只负责识别

    2026-08-11 v12 改: 优先 "品名" + n_cols>=8 → 8col_seedling (26.2/26.6 苗木 R0)
    """
    cells = [c.strip() for c in row if c.strip()]
    if len(cells) < 2:
        return None
    # 漏洞 2 防线 1: 数字密度 > 30% → 数据行
    digit_count = sum(1 for c in cells if any(ch.isdigit() for ch in c))
    if digit_count / len(cells) > 0.3:
        return None
    # 漏洞 2 防线 2: 字符长度 > 50 (数据行常有长规格型号, 26.1 荣昌区 8 col header 字符 39)
    if sum(len(c) for c in cells) > 50:
        return None
    # 2026-08-11 v12 增: 优先规则 — R0 含 "品名" + n_cols>=8 → 8col_seedling (26.2/26.6 苗木 R0 特征)
    # 没有这条规则会 fall through 到 must_have tie-breaker, 5col_seedling 会赢 (must_have=3 vs 8col_seedling=0)
    if "品名" in cells and 8 <= len(row) <= 13:
        return "market_8col_seedling"
    # 关键词匹配
    # 2026-08-11 v12 增: market_8col_district 必须有 exact "含税价(元)" cell (区分 含税 vs 不含税)
    # 因为子串匹配会让 "不含税价(元)" 误匹 "含税价(元)"
    has_exact_tax_cell = any(c == "含税价(元)" for c in cells)
    candidates = []
    for schema_name, pat in HEADER_SCHEMA_PATTERNS.items():
        if len(row) not in pat["n_cols_options"]:
            continue
        if schema_name == "market_8col_district" and not has_exact_tax_cell:
            continue
        if pat["must_have"] and not all(any(kw in c for c in cells) for kw in pat["must_have"]):
            continue
        if "any_of" in pat and not any(any(kw in c for kw in pat["any_of"]) for c in cells):
            continue
        # 2026-08-11 v12 改: no_have 用整词匹配 (避免 "含税价" 子串匹 "不含税价(元)")
        if "no_have" in pat and any(kw == c for c in cells for kw in pat["no_have"]):
            continue
        candidates.append(schema_name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # 多匹配 → must_have 数量多的胜出 (8col_seedling 因 must_have=0 自然败给 6col_district 等)
    return max(candidates, key=lambda s: len(HEADER_SCHEMA_PATTERNS[s]["must_have"]))


# ============================================================
# 表头识别
# ============================================================

def find_header_row(rows):
    """找表头行（含双行 header 识别）

    返回 (header_start, header_end, header_row)
      - header_start: 真 header 起始索引
      - header_end: header 延续的最后索引（双行 header 时 start<end）
      - header_row: 第一行 header 内容

    双行 header 检测：起始 header 后，若后续行所有非空格 cell 都不含数字 → 仍视为 header
    （如苗木表："品名 | 规格(cm) | 单位 | 不含税价(元) | 备注" + "株高 | 胸径 | 冠幅 | 分枝高"）
    """
    start = None
    for i, r in enumerate(rows):
        if not any(c.strip() for c in r):
            continue
        if is_section_subtitle("".join(r)):
            continue
        # 单位行（只有 1 个非空格，全是单位）
        non_empty = [c for c in r if c.strip()]
        if len(non_empty) == 1 and re.match(r"^[a-zA-Z²³]+$", non_empty[0]):
            continue
        start = i
        break
    if start is None:
        return 0, 0, rows[0] if rows else []

    # 2026-07-29 增：双行 header 识别（苗木表 row 2-3 嵌套表头）
    end = start
    while end + 1 < len(rows):
        next_row = rows[end + 1]
        non_empty = [c for c in next_row if c.strip()]
        if not non_empty:
            end += 1
            continue
        # 数据 cell 通常含数字（价格/序号）；header 延续行无数字
        has_data = any(re.search(r"\d", c) for c in non_empty)
        if has_data:
            break
        end += 1

    # 2026-07-30 增：扫描后续 body 找 sub-section 重置表头（如苗木 row 33 "品名/规格(cm)/..."）
    # 之前漏识别：sub-section 重置表头被当作数据行抽出，导致 xlsx 出现 (33, "品名", None, "规格(cm)", ...)
    # 检测：row[0]=="品名" + 至少 2 列含列头关键词（规格/单位/价格/株高/胸径/冠幅/分枝高/备注/地径）
    # 标记后由 extract_market_rows 在 body 循环时跳过
    # 注：行内 sub-header 检测放在 extract_market_rows 内更直接（因为 body 是数据行 stream）
    return start, end, rows[start]


# ============================================================
# 字段映射提取
# ============================================================

def extract_market_rows(rows, schema_def, segment_state, cq_yaml=None, schema_state=None):
    """区县 6 列 schema 提取：序号 | 材料名 | 规格 | 单位 | 不含税价 | 备注

    segment_state: dict 含 'current_segment' 键（跨 item 持续, extract() 传入/更新）
      2026-08-11 增：行级 segment 跟踪，26.1 印刷页 38-64 跨章节混表 (item 515, 948 行)
      遇 MAIN_SECTION_HEADERS 主章节标题 → 更新 current_segment → 后续行 _segment=新值

    schema_state: 2026-08-11 增：表头切换机制（修 26.1 MARKET 3 错位）
      - 跨 item 持续, extract() 传入/更新
      - 内部结构: {"name": "schema_name", "def": schema_def_dict}
      - body 内遇到表头行 → header_detector 命中 → 切换 schema_state
      - 上下文 (segment_state, prev_name, prev_remark, current_subschema) 全部沿用
      - 漏洞 3 应对: 切 schema 只换 schema, 上下文全部继承

    嵌套表头处理：
      - 章节小标题行（colspan=N 全空）→ 过滤
      - 双行 header 识别（苗木表）→ find_header_row 返回 header_end
      - 材料名 forward inherit（前一行非空 → 当前行为空则继承）
      - 备注 forward inherit（同上）
    """
    header_start, header_end, header = find_header_row(rows)
    body = rows[header_end + 1:]

    # 2026-08-11 增：schema_state 初始化（漏洞 3 应对：上下文沿用）
    if schema_state is None:
        schema_state = {"name": "?", "def": schema_def}
    field_map = schema_state["def"].get("field_map", {})
    n_cols_expected = schema_state["def"].get("n_cols", 6)
    # 2026-07-29 增：苗木 schema 识别（field_map 含 4 规格维度 = market_8col_seedling）
    is_seedling_8col = all(k in field_map for k in ("株高", "胸径", "冠幅", "分枝高"))

    extracted = []
    prev_name = ""
    prev_remark = ""
    # 2026-07-30 P2-5 增：苗木子表追踪（乔木/花灌木/棕榈/地被），用来把 col 2 正确写到「地径」或「胸径」
    # markdown 锚点："1、乔木"（4-spec：胸径）/ "2、花灌木"（3-spec：地径）
    # / "3、棕榈植物类"（3-spec：胸径）/ "4、地被及其它植物"（5 列，单独走草本 schema）
    current_subschema = "乔木"   # 默认从乔木开始（第一个子表）
    # 2026-07-30 增：sub-header 行内检测（苗木表 row 33+ "品名/规格(cm)/..." 重置表头）
    # 检测：row[0]=="品名" + 至少 2 列含列头关键词 → skip
    sub_header_kws = ["规格", "单位", "价格", "株高", "胸径", "冠幅", "分枝高",
                      "备注", "地径", "含税", "不含税", "不含税价"]
    # 2026-08-11 增：行级 segment 跟踪（主章节标题驱动）
    # 26.1 item 515 body row 936 「二十七、苗木」→ state 切苗木 → 后续 11 行 _segment=苗木
    # 26.1 item 691 (印刷页 65 苗木续) → state 继承 "苗木" → 全 201 行 _segment=苗木
    current_segment = segment_state.get("current_segment", "default")
    # 2026-08-11 增：header 区扫描 — 26.2 p=76 / 26.6 p=91 的「二十七、苗木」在 colspan=N 全空行, header 之前,
    # body loop 不命中 → 必须额外扫 rows[:header_end + 1] 更新 state
    for r in rows[:header_end + 1]:
        non_empty = [c for c in r if c.strip()]
        if len(non_empty) <= 1:
            text = "".join(c for c in r).strip()
            if text in MAIN_SECTION_HEADERS:
                current_segment = MAIN_SECTION_HEADERS[text]
                segment_state["current_segment"] = current_segment

    # 2026-08-11 改：移除 R0 header check (v10+ 发现 R0 check 太激进)
    # R0 check 旨在修 dispatch 误判 (n_cols=8 兜底 8col_seedling → 应切到 8col_district)
    # 但 R0 = ['序号', '材料名称', '规格及型号', '单位', '昌元、昌州、...', '', '盘龙、远觉、...', '']
    # 同时匹 8col_district (n=5 must_have) 和 5col_seedling (n=3 must_have, n_cols=8 在 options)
    # tie-breaker: max must_have → 8col_district (5 vs 3) ✓ 但仍可能切到错误 schema
    # 更稳: 不依赖 R0 check. dispatch 已按 n_cols 兜底, body loop header_detector 处理子表切换
    for r in body:
        # 2026-08-11 增：表头切换（漏洞 1 应对：检测到表头 → switch + skip 自身表头行）
        # 触发 header_detector 3 道防线: 关键词 ≥ 2 + 数字密度 < 30% + 字符长度 < 30
        # 命中后切换 schema_state; 上下文 (segment_state/prev_name/...) 全部沿用 (漏洞 3)
        if cq_yaml is not None:
            matched_schema = header_detector(r)
            if matched_schema and matched_schema != schema_state["name"]:
                new_def = cq_yaml["table_schemas"].get(matched_schema)
                if new_def is not None:
                    schema_state["name"] = matched_schema
                    schema_state["def"] = new_def
                    field_map = new_def.get("field_map", {})
                    n_cols_expected = new_def.get("n_cols", 6)
                    is_seedling_8col = all(k in field_map for k in ("株高", "胸径", "冠幅", "分枝高"))
                    continue  # skip 表头行自身

        # 整行只有 1 列 + 是章节小标题 → 跳过（并检测苗木子表切换）
        if len([c for c in r if c.strip()]) <= 1:
            text = "".join(c for c in r).strip()
            # 2026-08-11 增：主章节标题检测（P0 修 26.1 苗木 sheet 错位）
            # 优先 is_section_subtitle 前 — 「二十七、苗木」是主章节
            if text in MAIN_SECTION_HEADERS:
                current_segment = MAIN_SECTION_HEADERS[text]
                segment_state["current_segment"] = current_segment
                continue
            if is_section_subtitle(text):
                # 2026-07-30 P2-5：识别苗木子表锚点
                if "乔木" in text and "灌木" not in text:
                    current_subschema = "乔木"
                elif "花灌木" in text:
                    current_subschema = "花灌木"
                elif "棕榈" in text:
                    current_subschema = "棕榈"
                elif "地被" in text:
                    current_subschema = "地被"
                continue
            if not text:
                continue

        # 多列但文本是 "1、乔木"/"2、花灌木"/...（colspan 展开后 1 个非空格）→ 子表切换
        full_text = "".join(c.strip() for c in r if c.strip())
        # 2026-08-11 增：主章节标题检测（colspan 展开后场景，文本可能跨多列）
        if full_text in MAIN_SECTION_HEADERS:
            current_segment = MAIN_SECTION_HEADERS[full_text]
            segment_state["current_segment"] = current_segment
            continue
        if is_section_subtitle(full_text):
            if "乔木" in full_text and "灌木" not in full_text:
                current_subschema = "乔木"
            elif "花灌木" in full_text:
                current_subschema = "花灌木"
            elif "棕榈" in full_text:
                current_subschema = "棕榈"
            elif "地被" in full_text:
                current_subschema = "地被"
            continue

        # sub-header 检测：row[0]=="品名" + 多列含列头关键词 → skip
        non_empty = [c.strip() for c in r if c.strip()]
        if non_empty and non_empty[0] == "品名":
            hit_count = sum(1 for c in non_empty if any(kw in c for kw in sub_header_kws))
            if hit_count >= 2:
                continue

        # 章节小标题行（colspan 展开后 1 个非空格）→ 跳过
        if is_section_subtitle("".join(c.strip() for c in r if c.strip())):
            continue

        # 字段映射
        record = {"raw_cells": [clean_cell(c) for c in r]}
        # 2026-08-11 增：行级 segment 字段（dispatch 用，标记行属于哪个章节段）
        record["_segment"] = current_segment

        # 2026-07-30 P2-4 修：苗木表混 3 种 spec schema (乔木 4-spec / 花灌木 3-spec / 棕榈 3-spec)
        # 8-col field_map 假设 4 spec (株高/胸径/冠幅/分枝高)；
        # 当 col 4 (分枝高 slot) 是单位词时 → 实际是 3-spec (无分枝高)，
        #   单位/不含税价/备注 全部左移 1 列：
        #     品名(0) | 株高(1) | 胸径/地径(2) | 冠幅(3) | 单位(4) | 不含税价(5) | 备注(6)
        # 影响范围：棕竹 row 2-3、细叶十大功劳 row 2-3、棕榈/花灌木其他类似行
        # 2026-07-30 P2-5 改：col 2 是「地径」(花灌木) 还是「胸径」(乔木/棕榈) 由 current_subschema 决定
        if is_seedling_8col and len(r) >= 7:
            cells = [clean_cell(c) for c in r]
            # 单位词集（含棕榈/花灌木特有的 "苗"/"窝"）
            seedling_units_3spec = {"kg", "m", "株", "苗", "盆", "杯", "袋", "%", "块", "m²", "m³", "窝"}
            if (len(cells) > 4
                    and cells[4] in seedling_units_3spec
                    and (len(cells) <= 5 or cells[5])  # col 5 应为价格/备注（非空数字串）
                    ):
                record["材料名称"] = cells[0]
                record["株高"] = cells[1] if len(cells) > 1 else ""
                # 2026-07-30 P2-5：花灌木 col 2 = 地径；乔木/棕榈 col 2 = 胸径
                spec2_value = cells[2] if len(cells) > 2 else ""
                if current_subschema == "花灌木":
                    record["地径"] = spec2_value
                    record["胸径"] = ""  # 花灌木无胸径列（用户原话：表头不一样肯定要单独列）
                else:
                    record["胸径"] = spec2_value
                    record["地径"] = ""
                record["冠幅"] = cells[3] if len(cells) > 3 else ""
                record["分枝高"] = ""  # 3-spec 无此列
                record["单位"] = cells[4] if len(cells) > 4 else ""
                record["不含税价(元)"] = cells[5] if len(cells) > 5 else ""
                record["备注"] = cells[6] if len(cells) > 6 else ""
                extracted.append(record)
                prev_name = record["材料名称"]
                prev_remark = ""
                continue

        # 2026-07-29 修：苗木表混 8 列（乔木）+ 5 列（草本/地被）两种 schema
        # 草本行 markdown 用 colspan=2 拼成 8 cell，但内容是 5 列：
        #   变体 1 (26.2/26.6 葱兰/麦冬草/玉龙草): col 0=品名, col 1='', col 2='', col 3=单位(kg), col 4=价格
        #   变体 2 (26.2/26.6 结缕草):             col 0=品名, col 1='', col 2=材质(草块), col 3=单位(m²), col 4=价格
        #   变体 3 (2026-08-11 v12 增: 26.1 韭兰): col 0=品名, col 1='', col 2=单位(kg), col 3=价格, col 4=''=备注
        #   变体 4 (2026-08-11 v12 增: 26.1 结缕草): col 0=品名, col 1=材质(草块), col 2=单位(m²), col 3=价格(13.65)
        # 检测：col 1 是空（填补）+ col 2 or col 3 是常见草本单位 → 走草本 dispatch
        # 乔木保护：乔木 col 3 是 冠幅数字范围（"大于200"/"全冠" 等），不在 grass_units 集合
        if is_seedling_8col and len(r) >= 6:
            cells = [clean_cell(c) for c in r]
            grass_units = {"kg", "m", "株", "盆", "杯", "袋", "%", "块", "m²", "m³"}
            # 变体 1/2: col 3 = 单位
            if (not cells[1]
                    and len(cells) > 3
                    and cells[3] in grass_units):
                record["材料名称"] = cells[0]
                record["株高"] = ""
                record["胸径"] = ""
                record["冠幅"] = ""
                record["分枝高"] = ""
                record["规格及型号"] = cells[2] if (len(cells) > 2 and cells[2]) else ""
                record["单位"] = cells[3] if len(cells) > 3 else ""
                record["不含税价(元)"] = cells[4] if len(cells) > 4 else ""
                record["备注"] = cells[7] if len(cells) > 7 else ""
                extracted.append(record)
                prev_name = record["材料名称"]
                prev_remark = ""
                continue
            # 2026-08-11 v12 增: 变体 3 (26.1 韭兰): col 2 = 单位
            if (not cells[1]
                    and len(cells) > 3
                    and cells[2] in grass_units):
                record["材料名称"] = cells[0]
                record["株高"] = ""
                record["胸径"] = ""
                record["冠幅"] = ""
                record["分枝高"] = ""
                record["规格及型号"] = ""
                record["单位"] = cells[2] if len(cells) > 2 else ""
                record["不含税价(元)"] = cells[3] if len(cells) > 3 else ""
                record["备注"] = cells[4] if len(cells) > 4 else ""
                extracted.append(record)
                prev_name = record["材料名称"]
                prev_remark = ""
                continue
            # 2026-08-11 v12 增: 变体 4 (26.1 结缕草): col 1=材质, col 2=单位, col 3=价格
            # 区别于变体 2: 变体 2 cells[1] 空 + cells[3] 单位; 变体 4 cells[1] 非空 + cells[2] 单位
            if (cells[1]
                    and len(cells) > 3
                    and cells[2] in grass_units
                    and re.match(r"\d", cells[3])):
                record["材料名称"] = cells[0]
                record["株高"] = ""
                record["胸径"] = ""
                record["冠幅"] = ""
                record["分枝高"] = ""
                record["规格及型号"] = cells[1]  # 材质 (草块/种籽 等)
                record["单位"] = cells[2] if len(cells) > 2 else ""
                record["不含税价(元)"] = cells[3] if len(cells) > 3 else ""
                record["备注"] = cells[4] if len(cells) > 4 else ""
                extracted.append(record)
                prev_name = record["材料名称"]
                prev_remark = ""
                continue

        for field, idx in field_map.items():
            if isinstance(idx, int) and 0 <= idx < len(r):
                record[field] = clean_cell(r[idx])
            else:
                record[field] = ""

        # 2026-07-30 P2-5：花灌木的 col 2 不是「胸径」是「地径」（花灌木表头无胸径）
        # 4-spec 路径（花灌木 colspan=2 行）col 2 写到「地径」字段，「胸径」列置空
        # 乔木/棕榈 col 2 保留「胸径」，「地径」列置空（用户原话：表头不一样肯定要单独列）
        if is_seedling_8col:
            if current_subschema == "花灌木":
                record["地径"] = record.get("胸径", "")  # 把刚写入 col 2 的「胸径」字段改名
                record["胸径"] = ""
            else:
                record["地径"] = ""  # 乔木/棕榈无地径列

        # rowspan 继承：材料名为空 → 取前一行
        # 2026-07-29 修：先记 name 原始值，再做 重置判断（之前 prev_name 更新在比较前）
        prev_name_before = prev_name
        name = record.get("材料名称", "").strip()
        if not name:
            # inherit（材料名空 = 同行是 rowspan 共享列）
            record["材料名称"] = prev_name
        else:
            record["材料名称"] = name

        # 备注 forward inherit：材料名切换 → 重置备注
        # 关键：name 原始值非空 且 跟前一行 name 不一样 → 才重置 prev_remark
        if name and name != prev_name_before:
            prev_remark = ""

        # 更新 prev_name 给下一行用
        prev_name = record["材料名称"]

        # 备注 inherit：备注空 → 取 prev_remark
        remark = record.get("备注", "").strip()
        if not remark:
            record["备注"] = prev_remark
        else:
            prev_remark = record["备注"]

        extracted.append(record)

    return {"header": header, "rows": extracted}


def extract_nm_rows(rows, schema_def, page_filing_person, page_contact, schema_name="", cq_yaml=None, schema_state=None):
    """行业推荐 6 列 schema 提取 + 跨页填报人/联系方式注入

    2026-07-29 v5b 改: 统一输出 6 列 (序号|材料名|规格|单位|不含税价|备注)
    - 5 列 (混凝土协会)  → 补空单位列 + col 3 (加价) → 不含税价
    - 6 列 (墙体/建材/绿色建筑) → 标准映射
    - 7 列 (地质矿业嵌套) → 取消合并 + 每行独立输出
      col 0=序号 1=材料名 2=指标名称(写到规格) 3=指标要求(写到备注)
      4=单位 5=不含税价 6=备注

    2026-08-11 增：schema_state 切换（修 26.1 行业推荐 NM 段 89 行字段错位）
    """
    header_start, header_end, header = find_header_row(rows)
    body = rows[header_end + 1:]

    # 2026-08-11 增：schema_state 初始化
    if schema_state is None:
        schema_state = {"name": schema_name, "def": schema_def}
    field_map = schema_state["def"].get("field_map", {})

    extracted = []
    prev_name = ""
    prev_remark = ""
    for r in body:
        # 2026-08-11 增：表头切换（漏洞 1 应对：switch + skip 自身表头行）
        if cq_yaml is not None:
            matched_schema = header_detector(r)
            if matched_schema and matched_schema != schema_state["name"]:
                new_def = cq_yaml["table_schemas"].get(matched_schema)
                if new_def is not None:
                    schema_state["name"] = matched_schema
                    schema_state["def"] = new_def
                    field_map = new_def.get("field_map", {})
                    continue  # skip 表头行

        if len([c for c in r if c.strip()]) <= 1:
            text = "".join(c for c in r).strip()
            if is_section_subtitle(text) or not text:
                continue
        if is_section_subtitle("".join(c.strip() for c in r if c.strip())):
            continue

        record = {"raw_cells": [clean_cell(c) for c in r]}
        n_cols_actual = len(r)  # 当前行实际列数

        for field, idx in field_map.items():
            if isinstance(idx, int) and 0 <= idx < n_cols_actual:
                record[field] = clean_cell(r[idx])
            else:
                record[field] = ""

        # 2026-07-29 v5b 改: NM 表按 n_cols_actual 内联映射（不再为每种列数写专属 schema）
        if n_cols_actual == 5:
            # p123 混凝土协会: 序号|混凝土品种|指标等级|建议加价|说明文字
            # 用户原话: "不写加价这一列可不可以直接忽略掉" → 「建议加价」列丢弃
            # 映射到 6 列标准输出:
            #   序号|材料名|规格|单位|不含税价|备注
            #   col 0 / col 1 / col 2 / 空 / 空 / col 4
            # 「建议加价」(col 3) 不写到任何字段 (按用户要求丢弃)
            record["单位"] = ""
            record["不含税价(元)"] = ""                      # 用户要求: 加价列不写到 xlsx
            record["备注"] = clean_cell(r[4])                 # 说明文字
            # 序号/材料名/规格 已正确 (field_map col 0/1/2)
        elif n_cols_actual == 7:
            # 区分 26.1 墙体 7 col: 序号|材料名称|规格|单位|含税价|不含税价|备注
            # 与 26.6 地质矿业 7 col: 序号|材料名|指标名称|指标要求|单位|不含税价|备注
            # 判定: col 3 是单位词 (m², t, m³, 套, 个, 等) → 26.1 墙体
            #       col 4 是单位词 → 26.6 地质矿业
            unit_words = {"m²", "m³", "t", "m", "kg", "套", "个", "只", "片", "块", "根", "袋", "升", "瓶", "件", "台", "组", "条", "把", "桶", "箱", "卷", "包", "颗", "粒", "株", "棵", "g", "L", "ml"}
            cell_at_3 = clean_cell(r[3]).strip() if len(r) > 3 else ""
            if cell_at_3 in unit_words:
                # 26.1 墙体 7 col: 序号|材料名称|规格|单位|含税价|不含税价|备注
                record["单位"] = clean_cell(r[3])
                record["含税价(元)"] = clean_cell(r[4])        # 26.1 特殊: 含税价在 col 4
                record["不含税价(元)"] = clean_cell(r[5])
                record["备注"] = clean_cell(r[6])
            else:
                # 26.6 地质矿业 7 col 嵌套表: 序号|材料名|指标名称|指标要求|单位|不含税价|备注
                # 映射到 6 列: 序号|材料名|规格(=指标名称)|单位|不含税价|备注
                # 注意: field_map 按 6 列写入时 col 3/4/5 被错位 (当成单位/价格/备注),
                #       需要覆盖回来
                record["单位"] = clean_cell(r[4])              # 实际单位在 col 4
                record["不含税价(元)"] = clean_cell(r[5])      # 实际价格在 col 5
                record["备注"] = clean_cell(r[6])              # 实际备注在 col 6
                spec_name = clean_cell(r[2])  # 指标名称
                spec_val = clean_cell(r[3])   # 指标要求
                # 规格 = 指标名称 (field_map 已正确写到 col 2)
                record["规格型号"] = spec_name
                # 备注 拼上 指标要求
                original_remark = record.get("备注", "")
                if spec_val:
                    merged = spec_val
                    record["备注"] = f"{merged}; {original_remark}" if original_remark else merged
        # n_cols_actual == 6: 26.6 墙体/建材/绿色建筑 标准映射

        name = record.get("材料名称", "").strip()
        # 2026-07-29 修：inherit bug 同 extract_market_rows
        prev_name_before = prev_name
        if not name:
            record["材料名称"] = prev_name
        else:
            record["材料名称"] = name

        # 备注重置条件：name 原始值非空 且 跟前一行不一样
        if name and name != prev_name_before:
            prev_remark = ""
        prev_name = record["材料名称"]

        remark = record.get("备注", "").strip()
        if not remark:
            record["备注"] = prev_remark
        else:
            prev_remark = record["备注"]

        # 跨页填报人/联系方式（每页注入）
        if page_filing_person:
            record["填报人"] = page_filing_person
        if page_contact:
            record["联系方式"] = page_contact

        extracted.append(record)

    return {"header": header, "rows": extracted}


# ============================================================
# 页面底部"填报人/联系方式"提取
# ============================================================

FILING_PERSON_RE = re.compile(
    # 填报人：李 川  → 到 联系方式/联系电话/填报单位/填报日期/填报时间/。 停
    r"填报人[：:]\s*(.+?)(?=\s*(?:联系方式|联系电话|填报单位|填报日期|填报时间|邮\s*箱|第\s*一页|附件|。|\n|$))"
)
CONTACT_RE = re.compile(
    r"(?:联系电话|联系方式)[：:]\s*([\d\-\s,，]+)"
)
COLLECTOR_RE = re.compile(
    r"上述价格信息由\s*([^采集发布]+?)\s*采集发布"
)


def _collect_pairs_from_html(html_text):
    """从 raw HTML 全文提取 (filing, contact, position) 列表

    4 模式：
      1) 填报人：xxx 联系方式：yyy
      2) 联系人：xxx 联系电话：yyy
      3) 上述价格信息由XXX采集发布 联系电话：YYY
    """
    # 去 HTML 标签 -> 简洁全文
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = re.sub(r"\s+", " ", text)
    pairs = []  # (filing, contact, pos)
    # 模式 1
    for m in re.finditer(r"填报人[：:]\s*(.+?)\s*(?:联系方式|联系电话)[：:]\s*([\d\-\s]{5,20})", text):
        f = m.group(1).strip().rstrip(" ，,")
        c = m.group(2).strip()
        if re.match(r"^[\d\-\s]+$", f):
            continue
        pairs.append((f, c, m.start()))
    # 模式 3：上述价格信息由 XXX 采集发布 联系电话：YYY
    for m in re.finditer(
        r"上述价格信息由\s*([^采]{2,50}?)\s*采集发布.{0,200}?(?:联系电话|联系方式)[：:]\s*([\d\-\s]{5,20})",
        text,
    ):
        f = m.group(1).strip() + "采集"
        c = m.group(2).strip()
        pairs.append((f, c, m.start()))
    pairs.sort(key=lambda x: x[2])
    return pairs


def _pair_for_table(pairs, table_search_text, html_text):
    """对当前 NM table，找 caption 出现位置 nearest pair

    关键：跳过 toc 段（toc 出现早 pos<5000）的第一次命中，
    找 caption 真正位置（通常 pos>>60000）后 nearest pair。
    """
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = re.sub(r"\s+", " ", text)
    # 找第二次出现（跳过 toc 段第一次）
    pos1 = text.find(table_search_text)
    if pos1 < 0:
        return "", ""
    pos2 = text.find(table_search_text, pos1 + 1)
    pos = pos2 if pos2 > 0 and pos2 > 60000 else pos1
    # 如果 pos1 已经在数据区，直接用
    if pos1 >= 60000:
        pos = pos1
    if pos < 0:
        return "", ""
    # 找 pos 之后第一个 pair
    for f, c, p in pairs:
        if p >= pos:
            return f, c
    # 没找到 → 找 pos 之前的最后一个 pair
    prev_f, prev_c = "", ""
    for f, c, p in pairs:
        if p < pos:
            prev_f, prev_c = f, c
        else:
            break
    return prev_f, prev_c


def extract_page_meta(page_text):
    """从页面文本中提填报人/联系方式（content_list text 项）"""
    filing = ""
    contact = ""
    # 1) "填报人：xxx"
    m = FILING_PERSON_RE.search(page_text)
    if m:
        filing = m.group(1).strip()
        if "联系方式" in filing or "联系电话" in filing:
            filing = filing.split("联系方式")[0].split("联系电话")[0].strip()
        if re.match(r"^[\d\-\s]+$", filing):
            filing = ""
    # 2) "联系电话：xxx"
    m = CONTACT_RE.search(page_text)
    if m:
        contact = m.group(1).strip().rstrip(",，")
    # 3) 兜底："联系人：xxx" 模式
    if not filing:
        m = re.search(r"联系人[：:]\s*([^\n，,;；)）]+)", page_text)
        if m:
            filing = m.group(1).strip()
    # 4) 兜底："上述价格信息由XXX采集发布"
    if not filing:
        m = COLLECTOR_RE.search(page_text)
        if m:
            filing = m.group(1).strip()
    return filing, contact


# ============================================================
# 主流程
# ============================================================

def extract(classified_json, log_lines=None):
    """重庆 06 字段提取主流程"""
    if log_lines is None:
        log_lines = []

    cq_yaml = load_city_yaml()
    districts = cq_yaml.get("districts", [])
    skip_rules = cq_yaml.get("skip_rules", {})

    with open(classified_json, encoding="utf-8") as f:
        classified = json.load(f)
    content_list = classified.get("content_list", [])

    out_tables = []
    n_ok = n_skip = n_fail = 0
    period = Path(classified_json).parent.name.split("_")[1] if "_" in Path(classified_json).parent.name else "00"
    # 跨表 forward inherit 状态（NEW_MATERIAL 各协会表共享）
    prev_filing = ""
    prev_contact = ""
    # 2026-07-29 增：区县汇总 cap 第一次出现 = 万州（mineru cap 串味修复）
    _summary_seen = False
    # 2026-08-11 增：跨 item 持续 segment state（主章节标题如「二十七、苗木」跨多 item）
    # 26.1 item 515 (印刷页 38-64) 含「二十七、苗木」→ state="苗木"，item 691 (印刷页 65) 继承 → 全 201 行 _segment=苗木
    # 进入 NEW_MATERIAL 段前重置（状态独立）
    segment_state = {"current_segment": "default"}
    # 2026-08-11 增：schema_state 跨 item 持续（表头切换机制）
    # 26.1 item 515 跨章节混表 → 遇到苗木表头 → 切到 market_8col_seedling
    # 上下文 (segment_state, prev_name, prev_remark) 全部沿用 (漏洞 3)
    schema_state = {"name": "?", "def": None}

    # 加载 raw HTML 全局 (filing, contact, pos) 索引，用于 NM 表 caption 定位
    # 2026-08-04 修：原硬编码 "重庆工程造价2026-6期.html" 只对 06 期巧合成立，
    # 01 期实际 HTML 是 UUID 形式 (98729bf1-...html) → 找不到 → pairs=[] →
    # 5 个协会的填报人没法按 caption 派发，全 fallback 成 page 98 的 prev_filing="李 川"。
    # 修：固定名 → 同目录下任意 .html 兜底（每个 work_dir 只 1 个）
    work_dir = Path(classified_json).parent
    html_path = work_dir / "重庆工程造价2026-6期.html"
    if not html_path.exists():
        candidates = sorted(work_dir.glob("*.html"))
        if candidates:
            html_path = candidates[0]
    html_text = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
    pairs = _collect_pairs_from_html(html_text) if html_text else []  # [(filing, contact, pos)] sorted by pos

    for item in content_list:
        if item.get("type") != "table":
            continue
        section = item.get("section", "UNKNOWN")
        page = item.get("page_idx", 0)
        if section not in ("MARKET", "NEW_MATERIAL"):
            n_skip += 1
            continue

        # 2026-07-29 改：直接用 item.table_body（每 item 自带），
        # 不再用 md_tables 索引（md_content 表数 < content_list 表数，错位）
        body = item.get("table_body", "") or ""
        if not body.strip():
            continue

        cap = item.get("table_caption", [])
        cap_text = (cap[0] if isinstance(cap, list) and cap
                    else (cap if isinstance(cap, str) else ""))

        # 2026-08-11 增：item 边界 pre-scan — body 不含 苗木 信号 → 重置 segment state (防漏)
        # 4 坑之 4：状态独立 — MARKET 段跨 item 必须基于 body 信号决定是否重置
        # page 74 (装配式, 11 行) + page 75 (道路桥梁, 20 行) body 无 苗木 信号 → 重置 → 不被漏标
        # item 691 (印刷页 65, 苗木续) body 含 "2、花灌木" → 保留 → 201 行 _segment=苗木
        if section == "MARKET":
            has_signal = any(sig in body for sig in SEEDLING_BODY_SIGNALS)
            if not has_signal:
                segment_state["current_segment"] = "default"

        # SKIP 规则
        skip_reasons = []
        for kw in skip_rules.get("by_caption", []):
            if kw in cap_text:
                skip_reasons.append(f"caption:{kw}")
                break
        rows = parse_html_table(body, expand_colspan=True)
        if not rows:
            n_skip += 1
            continue
        # 2026-07-29 改：用 mode（出现最多的行长度）而非 max
        # 原因：少数行因 colspan+rowspan 组合 / OCR 异常导致长度不一致
        # 用 mode 选出"主流"列数，避免 schema 误匹
        from collections import Counter
        len_counter = Counter(len(r) for r in rows)
        n_cols_actual = len_counter.most_common(1)[0][0]
        if n_cols_actual in skip_rules.get("by_ncols", []):
            skip_reasons.append(f"ncols={n_cols_actual}")
        # row signal：前 5 行拼起来搜关键词
        if not skip_reasons:
            row_signals = skip_rules.get("by_row_signal", [])
            top_text = " | ".join(
                " ".join(str(c) for c in r) for r in rows[:5]
            )
            for kw in row_signals:
                if kw in top_text:
                    skip_reasons.append(f"row_signal:{kw}")
                    break
            # header signal：表头（含真 header）含关键词 → 整表 SKIP
            if not skip_reasons:
                header_signals = skip_rules.get("by_header_signal", [])
                _, _, header = find_header_row(rows)
                header_text = " ".join(str(c) for c in header)
                for kw in header_signals:
                    if kw in header_text:
                        skip_reasons.append(f"header_signal:{kw}")
                        break
        if skip_reasons:
            n_skip += 1
            continue

        # 选 schema
        schema_name = None
        schema_def = None
        if section == "MARKET":
            # 2026-07-29 v5 改：5 列时 inline check 是区县（row[0]=数字）还是草本（row[0]=中文）
            # 区县 5 列: 序号|材料名|规格|单位|价格 (无备注)
            # 草本 5 列: 品名|规格|单位|价格|备注
            # 旧 dispatch 按 yaml 顺序匹 → 草本 (market_5col_seedling) 在前 → 区县表被错匹
            if n_cols_actual == 5:
                # 看 body 第 1 行（去掉 header 后）row[0] 是否数字
                header_start, header_end, _ = find_header_row(rows)
                body_rows = rows[header_end + 1:]
                is_district_5col = False
                for br in body_rows[:5]:
                    if not br:
                        continue
                    first_cell = clean_cell(br[0]).strip() if br else ""
                    if first_cell.isdigit():
                        is_district_5col = True
                        break
                    if first_cell:  # 非空非数字 → 不是区县
                        break
                if is_district_5col:
                    schema_name = "market_5col_district"
                    schema_def = cq_yaml["table_schemas"]["market_5col_district"]
                else:
                    schema_name = "market_5col_seedling"
                    schema_def = cq_yaml["table_schemas"]["market_5col_seedling"]
            else:
                # 2026-08-11 改：MARKET schema 初始用 n_cols 兜底
                # n_cols=8 歧义：8col_district (荣昌区 含税+不含税×2区域) 或 8col_seedling (苗木 4规格)
                # 区分: 荣昌区 col 0=数字(序号), 苗木 col 0=中文(品名)
                # 2026-08-11 v11 inline dispatch: 看 body 第 1 行 (find_header_row 后) col 0
                if 8 == n_cols_actual or 12 == n_cols_actual:
                    header_start2, header_end2, _ = find_header_row(rows)
                    body_rows2 = rows[header_end2 + 1:]
                    first_data = next((br for br in body_rows2 if br), None)
                    if first_data and first_data[0].strip().isdigit():
                        # 数字 → 区县 (8col_district)
                        schema_name = "market_8col_district"
                        schema_def = cq_yaml["table_schemas"]["market_8col_district"]
                    else:
                        # 中文 → 苗木 (8col_seedling)
                        schema_name = "market_8col_seedling"
                        schema_def = cq_yaml["table_schemas"]["market_8col_seedling"]
                elif 6 == n_cols_actual or 7 == n_cols_actual:
                    schema_name = "market_6col_district"
                    schema_def = cq_yaml["table_schemas"]["market_6col_district"]
                else:
                    for sn, sd in cq_yaml.get("table_schemas", {}).items():
                        if sd.get("section") == "MARKET" and sd.get("n_cols") == n_cols_actual:
                            schema_name, schema_def = sn, sd
                            break
            if not schema_def:
                # 兜底 6 列
                schema_def = cq_yaml["table_schemas"]["market_6col_district"]
                schema_name = "market_6col_district"
        elif section == "NEW_MATERIAL":
            # 2026-07-29 v5b 改: 所有 NM 表统一走 recommend_6col_assoc（6 列标准输出）
            # 5 列（混凝土协会 5 列 PDF）→ 补空单位列 + col 3 当不含税价
            # 6 列（墙体/建材/绿色建筑）→ 正常
            # 7 列（地质矿业嵌套表）→ 取消合并 + col 3 指标要求 → 备注
            schema_name = "recommend_6col_assoc"
            schema_def = cq_yaml["table_schemas"]["recommend_6col_assoc"]
            # schema_name 用于 inline 分支 (5 列 / 7 列) 不同处理

        # 2026-08-11 增：每 item 都用 dispatch 选的 schema 重置 schema_state
        # 不重置会污染: 前一 item 是 5col_district, 当前 8col dispatch 后 schema_state 仍 5col_district
        # 修 26.1 荣昌区 bug 2 + 26.2/26.6 苗木 regression 双 bug 同源
        if schema_name and schema_def:
            schema_state["name"] = schema_name
            schema_state["def"] = schema_def

        # 2026-08-11 增：R0 header 检测 → 覆盖 dispatch 误判 (修 26.1 荣昌区 bug 2)
        # dispatch 按 n_cols 兜底 8col_seedling，但 R0 header 自身可能匹 8col_district (有"含税"/"不含税"关键词)
        # 走 find_header_row 拿 R0 (header_start=0, header_end=0) → header_detector(R0) → 覆盖 schema_state
        # ⚠️ 漏洞：26.2/26.6 苗木 R0 缺「株高/胸径/冠幅/分枝高」4 关键词 → header_detector 只匹 5col_seedling → 错切
        # 防御：R0 切换移入 extract_market_rows, 在 segment 扫描完后做 (current_segment=='苗木' 时跳过)

        # 行业推荐：提页面填报人/联系方式
        page_filing = page_contact = ""
        if section == "NEW_MATERIAL":
            # 1) 先从 content_list text 项提（p120+ 大多数协会）
            page_text = ""
            for ti in content_list:
                if ti.get("page_idx") == page and ti.get("type") == "text":
                    page_text += ti.get("text", "")
            page_filing, page_contact = extract_page_meta(page_text)
            # 2) 兜底：raw HTML caption 位置 → nearest pair（p118/119 万盛/双桥）
            if not page_filing and pairs:
                # 2a) Unique anchors（HTML 中只出现 1 次的子串，避免 toc 误匹）
                # 万盛段：toc 是 "万盛经开区"，数据段是 "万盛经开区建设管理局"
                # 用 unique anchor 找 caption 正位置
                text = re.sub(r"<[^>]+>", " ", html_text)
                text = re.sub(r"\s+", " ", text)
                # 优先：搜 caption 主体 + "由" + "采集" 完整模式
                unique_anchors = {
                    "万盛经开区": "上述价格信息由万盛经开区建设管理局采集发布",
                    "双桥经开区": "上述价格信息由双桥经开区建设管理局采集发布",
                    "墙体材料工业行业协会": "填报人：李 川",
                    "建筑材料协会": "填报人：孙 群",
                    "混凝土协会": "填报人：谢凤鳞",
                    "绿色建筑与建筑产业化协会": "填报人：凡秋明",
                    "地质矿业协会": "填报人：邝 野",   # 2026-07-29 加: page 127 地质矿业填报人
                    "装配式建筑": "填报人：邝 野",
                    "智能建造": "填报人：邝 野",
                    "绿色低碳建材": "填报人：邝 野",
                }
                anchor = ""
                for prefix, unique in unique_anchors.items():
                    if prefix in cap_text and unique in text:
                        anchor = unique
                        break
                if anchor:
                    pos = text.find(anchor)
                    if pos >= 0:
                        # 找 pos 之后最近的 pair
                        for f, c, p in pairs:
                            if p >= pos:
                                page_filing = f
                                page_contact = c
                                break
                        else:
                            # 没找到 → nearest before
                            for f, c, p in pairs:
                                if p < pos:
                                    page_filing = f
                                    page_contact = c
                                else:
                                    break

        # 跨页 forward inherit：协会表跨多页时下一页继承上一页
        if section == "NEW_MATERIAL":
            if page_filing:
                prev_filing = page_filing
            if page_contact:
                prev_contact = page_contact
            # 双桥/万盛段（p118/119）没填报人 → 用 prev 兜底
            if not page_filing:
                page_filing = prev_filing
            if not page_contact:
                page_contact = prev_contact

        # 提取
        if section == "MARKET":
            # 2026-08-11 增：传 schema_state + cq_yaml 启用表头切换
            result = extract_market_rows(rows, schema_def, segment_state, cq_yaml, schema_state)
        else:
            # 2026-08-11 增：NM 段独立 — 重置 segment state 避免 MARKET 段状态污染 NM
            # 4 坑之 3：NM 重置
            segment_state["current_segment"] = "default"
            # 2026-08-11 增：NM 段 schema_state 也独立 (避免 MARKET 切到 NM 段 schema 残留)
            schema_state["name"] = schema_name
            schema_state["def"] = schema_def
            result = extract_nm_rows(rows, schema_def, page_filing, page_contact, schema_name, cq_yaml, schema_state)

        # 注入 市/区
        # 2026-08-19 改：cap-level label 整表共享，_summary_seen 移到 for 循环外
        # 原 bug：row loop 内 flip _summary_seen=True → row 0 标万州、row 1+ 标区县汇总
        # 修：先决定整张表 label（_summary_seen flip 在循环外），所有 row 共享同一 label
        # dispatch 优先级：cap_label > 区县汇总表 > row-level segment > default
        # 2026-07-29 改：万州修复 — cap='2026年5月区县材料价格信息' 第一次出现实际是 万州区 数据
        # 根因：mineru 把页内大标题当第一张表 cap，覆盖了 万州区 cap
        # 修：第一次出现此 cap → 整表标 万州区；后续出现 → 整表标 区县汇总
        district_label = _match_district(cap_text, districts)
        is_summary_table = section == "MARKET" and "区县材料价格信息" in cap_text
        # 整表共享一个 label（cap-level 决定）
        if district_label:
            table_label = f"重庆市/{district_label}"
        elif is_summary_table and not _summary_seen:
            _summary_seen = True
            table_label = "重庆市/万州区"
        elif is_summary_table:
            table_label = "重庆市/区县汇总"
        elif section == "NEW_MATERIAL":
            table_label = "重庆市/行业协会"
        else:
            table_label = "重庆市/"
        for r in result["rows"]:
            # ① row-level segment（苗木 sheet 分流标记，step6 单出 sheet）
            if r.get("_segment") == "苗木":
                r["市/区"] = "苗木"
            # ② default：cap-level table_label（整表共享）
            else:
                r["市/区"] = table_label

        out_tables.append({
            "section": section,
            "page": page,
            "caption": cap_text,
            "header": result["header"],
            "rows": result["rows"],
            "_debug_origin": f"p{page}_{schema_name}",
        })
        n_ok += 1

    out_path = Path(classified_json).parent / "extract.json"
    out_path.write_text(
        json.dumps(out_tables, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[step3-cq] 写出: {out_path}")
    print(f"[step3-cq] 统计: {n_ok} ok | {n_skip} skip | {n_fail} fail")
    return out_path


def _match_district(text, districts):
    """从 caption 匹配区名（长字符串优先）"""
    if not text:
        return ""
    districts_sorted = sorted(districts, key=len, reverse=True)
    for d in districts_sorted:
        if d and d in text:
            return d
    return ""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python step3_extract_cq.py <classified_json>", file=sys.stderr)
        sys.exit(1)
    extract(sys.argv[1])