"""step3_extract_gz.py - 广州字段提取 (2026-08-05 建, Step 1.6)

输入: result.json (OCR 缓存, 含 md_content)
输出: extract.json (sheet 列表, step6 期望格式)

设计 (Step 1.6 schema, 9 列 4 价格按 PDF 原文命名):
  Sheet "金属材料" (广州专属 schema, 9 列) = table[0] (整页左右双列拼接)
       序号 | 名称 | 规格(mm) | 单位 | 税前综合价格(元)区间值 | 税前综合价格(元)均值 | 含税价(元) | 除税价(元) | 备注

  关键约束 (用户 2026-08-05 拍板):
    - 用户语义: 「税前综合价格」= 「税前综合价格均值」= 「除税价」(3 个同义词, 都是单值)
    - PDF 实际表达:
      * 「税前综合价格(元)区间值」← idx 4 (PDF 表格子表头「区间值」)
      * 「税前综合价格(元)均值」← idx 5 (PDF 表格子表头「均值」, 语义 = 税前综合价格)
      * 「除税价」← idx 4 (5 列 row 单价 = 税前综合价格)
    - 「含税价」PDF 没, 留空 (用户拍板)
    - 不含「材料编码」列 (跟武汉/湖北一致)

  schema 映射 (Step 1.6, 4 价格按 PDF 原文命名):
    6 列 row (编码|名称|规格|单位|区间值|均值) →
      interval_value = idx 4, mean_value = idx 5, incl = '', excl = ''
    5 列 row (编码|名称|规格|单位|单价) →
      interval_value = '', mean_value = '', incl = '', excl = idx 4
    「—」占位 → 4 价格都空 → 删

算法骨架 (2026-08-05):
  1. md_content 抽 35 个 HTML <table>
  2. 每个 table 内逐行扫描:
     - 1 cell (单章标题) → skip (A 类 sub-header)
     - 2 cells (子列名: 区间值/均值) → skip (B 类 sub-header)
     - cells=10/11 且 row[0]='材料编码' → 表头 → skip
     - cells 数 != 5/6/10/11 → skip
     - 其余 → split_2d_table → 左 + 右 → 抽 5/6 列 → skip 材料编码 → 写入 sheet
  3. 价格 OCR 字符清洗 (复用武汉)
  4. 4 价格任一有真值 → 保留; 全空 → 删 (_has_real_price)
  5. --only=N 只跑 table[N] 调试

复用 (按约束「不改其他城市」, 复制函数体):
  - 武汉 step3: _clean_price, _PRICE_OCR_CHARS
  - 武汉 utils: ROOT
  - 广州 utils_gz: split_2d_table, clean_cell, clean_unit

参数:
  python step3_extract_gz.py <result.json> [--only N]
    --only N  只跑第 N 个 table (Step 1 调试用)
"""
import argparse
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
from utils_gz import ROOT, split_2d_table, clean_cell, clean_unit, load_config

# ============================================================
# 复用武汉: 价格 OCR 字符清洗
# ============================================================
_PRICE_OCR_CHARS = re.compile(r"[\s$]+")


def _clean_price(p):
    s = str(p).strip()
    if not s:
        return ""
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    s = _PRICE_OCR_CHARS.sub("", s)
    return s


_INCH_FRAC_RE = re.compile(r"\\frac\{(\d+)\}\{(\d+)\}")


def _clean_inch(s):
    """清洗英寸 cell (md[12-14] LaTeX + HTML entity):
       $\\frac{1}{2}"$ → 1/2"      (md[11-12] 分数英寸, 去 LaTeX $ + frac + unescape)
       21⁄2&quot;     → 21/2"     (md[13-14] Unicode 分数 + HTML 实体)
       2&quot;        → 2"        (md[13-14] 整数英寸)
       $1"$          → 1"          (md[11-12] 简单英寸)
    """
    if not s:
        return ""
    s = _decode_entities(str(s)).strip()
    s = _INCH_FRAC_RE.sub(r"\1/\2", s)
    s = s.replace("⁄", "/")
    s = s.replace("$", "")              # 2026-08-07 fix: 去 LaTeX 数学包裹
    return s.strip()
    return s


def _parse_pipe_spec(s):
    """清洗管材规格 cell (md[15] 复合规格, 2026-08-07 加):
       "DN15壁厚(mm)0.8"  → "DN15壁厚(mm)0.8"   (不锈钢管, 保留原始, 由 _process_table_15 抽 DN/壁厚)
       "DN100"            → "DN100"              (铸铁管, 简单 DN)
       "Φ159*6"           → "Φ159*6"             (燃气焊管, 外径×壁厚)
    R7: 仅去空白 + 解 HTML entity, 不动原内容 (DN/壁厚 由 _process_table_15 单独 regex 抽)
    """
    if not s:
        return ""
    return _decode_entities(str(s)).strip()


def _has_real_price(p):
    """价格是否真含数字 (排除 '—' '空' 等占位符)"""
    s = str(p).strip()
    if not s:
        return False
    return any(c.isdigit() for c in s)


# ============================================================
# Sheet 配置 (Step 1.6 + 9 列 schema: 金属材料; Step 8.1 + 11 列 schema: 砂浆)
#   9 列 schema (金属/砌块/PHC/混凝土/沥青): 序号|名称|规格(mm)|单位|区间值|均值|含税|除税|备注
#   11 列 schema (干混+湿拌砂浆, 按 PDF 原文 8 列 + 4 价格展开): 序号|名称|性能指标|强度等级|单位|区间值|均值|含税|除税|适用范围|t/m³系数
# ============================================================
_BASE_SCHEMA_9 = [
    "序号",
    "名称",
    "规格(mm)",
    "单位",
    "税前综合价格(元)区间值",   # PDF 表格子表头「区间值」
    "税前综合价格(元)均值",     # PDF 表格子表头「均值」
    "含税价(元)",                # PDF 没, 留空 (用户拍板)
    "除税价(元)",                # = 税前综合价格 (5 列 row idx 4)
    "备注",
]

# 2026-08-06 加: sheet「金属材料」扩到 10 列, 新增「执行标准」字段
# 用于 md[7] 蒸压加气混凝土板 (PDF 有「执行标准」列, GB/T 15762-2020)
# 其他现有 224 行数据这列空
_BASE_SCHEMA_10 = [
    "序号",
    "名称",
    "规格(mm)",
    "单位",
    "税前综合价格(元)区间值",
    "税前综合价格(元)均值",
    "含税价(元)",
    "除税价(元)",
    "执行标准",                   # md[7] 蒸压加气 idx 5 = "GB/T 15762-2020"
    "备注",
]

_MORTAR_SCHEMA_11 = [
    "序号",
    "名称",
    "性能指标",                  # PDF 原文, 不拆字段 (Step 8.1 user 拍板)
    "强度等级",                  # PDF 原文 idx 3 (anchor) / idx 1 (inherit)
    "单位",
    "税前综合价格(元)区间值",
    "税前综合价格(元)均值",
    "含税价(元)",                # PDF 没, 留空
    "除税价(元)",                # PDF 没, 留空
    "适用范围",                  # PDF idx 7 (anchor 含中文) / inherit 继承 anchor
    "t/m³系数",                  # PDF idx 8 (n=9 anchor) / idx 7 (n=8 anchor OCR 漏 scope) / idx 5 (n=6 inherit) / 空 (n=5 inherit)
]

# md[6] 装配式独立 sheet (2026-08-06 user 拍板), 6 列严格对齐 PDF
#   PDF 6 列 + 列 1 改「序号」(跟金属材料惯例, 18 位 code 丢)
#   列名「区间值」= 金属材料 sheet 区间值, 跨 sheet 一致
_PREFAB_SCHEMA_6 = [
    "序号",                            # 1-36 自增 (_number_rows_independently)
    "项目名称",                        # PDF idx 1 (anchor) / inherit anchor
    "钢筋含量(kg/m3)",                 # PDF idx 2 (anchor 总范围, inherit inherit anchor)
    "钢筋含量",                        # PDF idx 3 (anchor 本段) / idx 1 (inherit 本段)
    "单位",
    "税前综合价格(元)区间值",          # PDF "2101 - 2160" 合并成 "2101~2160"
]

# md[11-14] 钢管 sheet (2026-08-07 加), 6 列统一 schema
#   md[11] 镀锌钢塑复合管 (per-row name) + md[12-14] 镀锌钢管(水煤气管) (caption name)
#   合并入同一 sheet: 序号 | 名称 | 规格 | 壁厚 | 单位 | 税前综合价格(元)
# 2026-08-07 fix: 加「规格」列到末尾 (md[15] 不锈钢管等用, md[11-14] 默认空)
_PIPE_SCHEMA_8 = [
    "序号",                            # _number_rows_independently 自增 1-150
    "名称",                            # md[11] per-row anchor; md[12-14] caption 抽; md[15] per-row
    "规格",                            # 2026-08-07 fix-2: 挪到名称后; md[15] 原文 (DN100壁厚(mm)2.0 / Φ159*6); md[11-14] 空
    "规格DN",                          # 2026-08-07 fix: 从 "规格" 拆出, DN 值
    "规格英寸",                        # 2026-08-07 fix: 加列, 1/2" 等 (解 LaTeX/HTML entity)
    "壁厚",                            # mm (md[11] idx 4 anchor / idx 3 inherit; md[12-14] idx 3; md[15] parse)
    "单位",                            # m (clean_unit)
    "税前综合价格(元)",                # 单价 (idx 6 anchor / idx 5 inherit; md[12-14] idx 5; md[15] idx 4/9)
]

# md[16-21] 电线电缆 sheet (2026-08-07 加), 6 张表合并
#   schema: 材料名称 | 标称截面(mm²) | 芯数 | 单位 | 税前综合价格(元) | 备注
_CABLE_SCHEMA_6 = [
    "序号",
    "材料名称",
    "标称截面(mm²)",                  # 含 $mm^2$ / $3×1.5+1×1$ LaTeX 已解
    "芯数",                            # 单芯/二芯/三芯/四芯/五芯/-
    "单位",
    "税前综合价格(元)",
    "备注",                            # 整表加价说明, 每行复用
]

# md[23-26] 镀锌桥架 sheet (2026-08-07 加), 4 张表合并 (1)(2)(3)(4)
#   schema 7 数据列 + 1 备注 (用户拍板: 说明整段进每行备注):
#     材料名称 | 规格（高×宽） | 壁厚(mm) | 单位 | 税前综合价格(元) | 表面积（m²/m）单面 | 表面积（m²/m）双面 | 备注
#   4 张表共用同一段「1、以上为槽式…2、以上单价不含…」说明 (R3/R2 anchor idx 16 整段抓)
#   inherit 行字段来源:
#     anchor (n=17): left 7 + right 7 + 说明 (idx 16)
#     inherit n=8  (md[23-26] 主):  无 单面/双面/单位 (idx 1=spec, 2=壁厚, 3=价格, 5=spec, 6=壁厚, 7=价格)
#     inherit n=14 (md[26] 部分):   含 单面/双面/单位 (idx 0=code, 1=spec, 2=壁厚, 3=单位, 4=价格, 5=单面, 6=双面)
_TRAY_SCHEMA_8 = [
    "材料名称",                         # anchor idx 1 / inherit 继承 anchor
    "规格（高×宽）",                    # anchor idx 2 / inherit idx 1 (n=8) 或 idx 1 (n=14)
    "壁厚(mm)",                        # anchor idx 3 / inherit idx 2
    "单位",                            # anchor idx 4 / inherit idx 3 (n=14) / inherit 继承 anchor (n=8)
    "税前综合价格(元)",                # anchor idx 5 / inherit idx 4 (n=14) 或 idx 3 (n=8)
    "表面积（m²/m）单面",             # anchor idx 6 / inherit idx 5 (n=14) / inherit 继承 anchor (n=8)
    "表面积（m²/m）双面",             # anchor idx 7 / inherit idx 6 (n=14) / inherit 继承 anchor (n=8)
    "备注",                            # anchor idx 16 整段 / inherit 继承 anchor
]

_HEADERS = {
    "金属材料": _BASE_SCHEMA_10,
    "砂浆": _MORTAR_SCHEMA_11,  # Step 8.1 加, table[5] 干混+湿拌砂浆
    "建筑装配式构件": _PREFAB_SCHEMA_6,  # 2026-08-06 加, md[6] 装配式 9 anchor × 4 inherit = 36 行
    "钢管": _PIPE_SCHEMA_8,  # 2026-08-07 加, md[11-15] 5 表合并 (7 列: +规格英寸; 8 列: +规格)
    "电线电缆": _CABLE_SCHEMA_6,  # 2026-08-07 加, md[16-21] 6 张表合并 6 列 schema (含芯数+备注)
    "镀锌桥架": _TRAY_SCHEMA_8,  # 2026-08-07 加, md[23-26] 4 张表合并 8 列 schema (含表面积单/双面+备注)
}

_TARGET_SHEETS = list(_HEADERS.keys())


# ============================================================
# caption dispatch (Step 7 加, 2026-08-05 按武汉 caption_dispatch 机制)
#   - 扫 rows_raw 找第一个 n=1 cell 含关键词, 返回对应 extractor 名
#   - 关键词按长度从长到短匹配 (避免「沥青」抢「沥青混凝土税前综合价格」)
#   - 找不到 → "default"
# ============================================================
def _find_caption_extractor(rows_raw, caption_dispatch):
    """从 rows_raw 找第一个含关键词的 n=1 cell, 返回 extractor 名 (key of caption_dispatch)

    返回值: 'phc' / 'asphalt' / 'aluminum' / 'default'
    """
    # 1) 收集所有 (extractor_name, kw) 按 kw 长度从长到短排序 (长关键词优先)
    all_kw = []
    for name, cfg in caption_dispatch.items():
        if name == "default" or not isinstance(cfg, dict):
            continue  # 跳过 _comment 等非 dict 配置
        for kw in cfg.get("kw", []):
            all_kw.append((name, kw))
    all_kw.sort(key=lambda x: -len(x[1]))

    # 2) 扫 rows_raw 找第一个 n=1 cell (n=3 caption 如 PHC 也包含, 索引 0)
    for r in rows_raw:
        if len(r) == 1:
            cells_to_check = [r[0]]
        elif len(r) <= 3:
            cells_to_check = r  # n=2/3 caption (PHC 是 n=3 PHC+PRC)
        else:
            continue
        for cell in cells_to_check:
            cell = cell.strip()
            if not cell:
                continue
            for name, kw in all_kw:
                if kw in cell:
                    return name
    return "default"


def _parse_html_table(html):
    """解析 markdown HTML <table> → list[list[str]] (复用武汉 step3)"""
    rows = []
    trs = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
    for tr in trs:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.DOTALL)
        cleaned = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        rows.append(cleaned)
    return rows


def _extract_block_caption_and_body(block):
    """抽 table block 的 caption + body 拼接文本 (噪音判断用)

    重要 (2026-08-06): MinerU 把表格内容存在 span['html'] 字段, 不是 span['content']
    遍历 block.blocks[] = [table_caption, table_body, ...]
    累加每个 span 的 html 字段 (table_caption 优先抽 caption)
    返回 (caption_html, body_html) 元组
    """
    caption = ""
    body = ""
    for b in block.get("blocks", []):
        btype = b.get("type", "")
        bhtml = ""
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                # MinerU 真实表格内容在 span['html'], span['content'] 通常空
                bhtml += s.get("html", "") or s.get("content", "") or s.get("text", "")
        if btype == "table_caption":
            caption = bhtml
        elif btype == "table_body":
            body += bhtml
    return caption, body


def _is_noise_block(block):
    """噪音 table block 判定 (2026-08-06): PDF 页眉/页脚里的 mini table

    特征: caption 空 + body 极短 (< 100 字符)
    真实数据表 body 长 (几十行 × 每行 50+ 字符)
    例: pdf_info 多 4 张噪音 (p12/p13/p16×2/p18) — 全 caption 空 + body < 100
    """
    cap, body = _extract_block_caption_and_body(block)
    return cap == "" and len(body) < 100


def _normalize_caption(cap):
    """caption 标准化 (2026-08-06 跨页合并 v3): 只去 (续),不去 (1)(2)(3) 编号
    - 去 (续) 后缀 (跨页表的续页标题, e.g. "干混砂浆税前综合价格(续)")
    - 不去 (1)(2)(3) 编号 (这些是分表标识, 各自独立页, 不能合并)
    - 合并多余空格/换行
    例:
      "预拌砂浆税前综合价格(1)"     → "预拌砂浆税前综合价格(1)"  (保留, 区分混凝土(1)与(2))
      "干混砂浆税前综合价格(续)"     → "干混砂浆税前综合价格"     (去 (续))
      "镀锌钢管税前综合价格(1)"     → "镀锌钢管税前综合价格(1)"
      "1.6MPa" 不动                 → "1.6MPa"
    """
    if not cap:
        return ""
    cleaned = re.sub(r"[（(]续[）)]\s*$", "", cap)  # 只去 (续)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _normalize_caption_for_merge(cap):
    """caption 标准化 for merge (2026-08-06): 去 (1)(2)(3) 编号
    - 用于跨页合并判定, 去除编号后缀, 让同章节不同分表能合并
    - 例如 "混凝土装配式构件税前综合价格(1)" 和 "(续)" 都能合并
    例:
      "预拌砂浆税前综合价格(1)"     → "预拌砂浆税前综合价格"
      "干混砂浆税前综合价格(续)"     → "干混砂浆税前综合价格"
    """
    if not cap:
        return ""
    cleaned = re.sub(r"[（(](续|\d+)[）)]\s*$", "", cap)
    cleaned = re.sub(r"-\d+\s*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _parse_page_map(middle_json_str, md_content=None, md_count=None):
    """阶段 0+跨页合并 (2026-08-06): 从 middle_json 抽 md_idx → page_idx 映射

    两阶段匹配 + 保守合并 (用户拍板 A):
      阶段 B: pdf_info.preproc_blocks → 非噪音 (page, normalized_caption)
              空 caption 自动归到上一非空 caption (跨页表续页无 caption 场景)
      阶段 A: md_content <table> 上下文 → md table captions (去 html tag, 取最近 n=1 cell)
      阶段 C: caption prefix match + 同 caption 连续页合并
              输出 "p11" 或 "p11-13" (单页/跨页范围)

    兼容性:
      - 旧调用 _parse_page_map(middle_json_str) → 返回 list[int] (旧 page_idx 列表, 仅作 B 阶段)
      - 新调用 _parse_page_map(middle_json_str, md_content, md_count) → 返回 list[str] (完整跨页 map)

    R10 时间锚点: 2026-08-06 跨页合并
    """
    # 兼容旧调用: 只传 middle_json_str → 返回 pdf page list (跟旧版一致)
    if md_content is None or md_count is None:
        try:
            mj = json.loads(middle_json_str) if isinstance(middle_json_str, str) else middle_json_str
            pdf_info = mj.get("pdf_info", [])
        except Exception:
            return []
        valid_pages = []
        for page in pdf_info:
            pidx = page.get("page_idx", 0)
            for blk in page.get("preproc_blocks", []) or page.get("para_blocks", []) or page.get("blocks", []):
                if blk.get("type") == "table" and not _is_noise_block(blk):
                    valid_pages.append(pidx)
        return valid_pages

    # 新调用: 两阶段匹配 + 跨页合并
    try:
        mj = json.loads(middle_json_str) if isinstance(middle_json_str, str) else middle_json_str
        pdf_info = mj.get("pdf_info", [])
    except Exception:
        return [f"p{i+1}" for i in range(md_count)]

    # 阶段 B: pdf_info → non-noise blocks (空 caption 归到上一非空 caption)
    pdf_blocks = []  # [(page_1idx, normalized_caption)]
    last_cap = ""
    for page_idx, page in enumerate(pdf_info):
        pidx = page.get("page_idx", page_idx) + 1  # PDF 1-indexed
        for blk in page.get("preproc_blocks", []) or page.get("para_blocks", []) or page.get("blocks", []):
            if blk.get("type") != "table":
                continue
            if _is_noise_block(blk):
                continue
            cap, _ = _extract_block_caption_and_body(blk)
            # 阶段 B 不去 (1)(2)(3) 编号 — 保留原 caption, 阶段 C 才去编号跨页合并
            # 但去 (续) (跨页续页不需要区分, 跟正页同 caption)
            cap_clean = cap.strip() if cap else ""
            if cap_clean:
                # 去 (续) 但保留 (1)(2)(3)
                cap_for_storage = re.sub(r"[（(]续[）)]\s*$", "", cap_clean).strip()
                cap_for_storage = re.sub(r"\s+", " ", cap_for_storage)
                if cap_for_storage:
                    last_cap = cap_for_storage
                    pdf_blocks.append((pidx, cap_for_storage))
                elif last_cap:
                    pdf_blocks.append((pidx, last_cap))
            elif last_cap:
                # 空 caption 跨页续页 → 归到上一非空 caption family
                pdf_blocks.append((pidx, last_cap))
            # else: 真无 caption 且无前驱 → 丢弃

    # 阶段 A: md_content → table captions (length = md_count)
    # 策略: 按 <table> 切分 markdown 成 N+1 块 (N 张表 + 1 段尾巴), 块内找 ## 标题
    # 例 "## A\nbody<table></table>## B\nbody<table>" → 块1="## A\nbody", 块2="## B\nbody"
    md_captions = []
    table_iter = list(re.finditer(r"<table", md_content))
    for i, m in enumerate(table_iter):
        # 本块: 上个 <table> 结束(or 0) 到 当前 <table> 开始
        prev_end = table_iter[i - 1].end() if i > 0 else 0
        block = md_content[prev_end:m.start()]
        # 找 ## caption (非贪婪, 行尾前)
        h_match = re.search(r"#+\s+([^\n<]+?)\s*$", block, re.MULTILINE)
        if h_match:
            cap = h_match.group(1).strip()
        else:
            # fallback: 取块内最后非空行 (短于 40 字符)
            lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
            cap = ""
            for ln in reversed(lines):
                if 2 <= len(ln) <= 40:
                    cap = ln
                    break
        md_captions.append(_normalize_caption(cap))
    while len(md_captions) < md_count:
        md_captions.append("")
    md_captions = md_captions[:md_count]

    # 阶段 C: 完全相等 match (主) + 跨页合并 (用 _normalize_caption_for_merge 去编号扩展)
    # 注 (2026-08-06 v4):
    #  - 初始匹配: 完全相等 (保留 (1)(2)(3)), 避免 (1)(2) 误合
    #  - 跨页扩展: 用 _normalize_caption_for_merge 去编号后, 跨页续页 (空 caption 继承 caption) 能合并
    #  - 例: pdf_blocks=[(p10,"混凝土(1)"),(p11,"混凝土(2)"),(p12,"砂浆(1)"),(p13,"砂浆(2)")] + pdf_caption空继承
    #         md_caption="混凝土(1)" → 完全等于 pdf[0] → 命中 → 向后: _merge("混凝土(1)", "混凝土(2)")? 去编号后都 "混凝土" → 合
    #         但 (1)(2) 是不同分表! 应分开! → 解决: 完全相等匹配, 不向 norm 跨页扩展
    #  - 例: pdf_blocks=[(p10,"砂浆(1)"),(p11,""),(p12,"")] (续页继承 caption) + md_caption="砂浆(1)"
    #         md 完全等于 pdf[0] → 命中 → 向后: pdf[1] caption="砂浆(1)" (继承的 norm 仍是 "砂浆(1)")
    #         完全等于 → 合 p10-12 ✅
    #  - 例: 跨页表 md_caption="干混砂浆(续)" → pdf_caption="干混砂浆(1)" → 不等 → 不命中
    #         实际期望命中 (跨页续页, caption 用 (续))
    #         解决: 主匹配失败时, 用 _normalize_caption_for_merge (去编号) 再试一次
    def _match_exact(a, b):
        return bool(a) and bool(b) and a == b

    def _match_merge(a, b):
        """跨页扩展用: 去编号后完全相等"""
        return _match_exact(_normalize_caption_for_merge(a), _normalize_caption_for_merge(b))

    page_map = []
    pdf_idx = 0
    last_resolved_range = None  # 上一张 md table 的页码范围, 用于 inherit 同章不同编号
    for md_cap in md_captions:
        if not md_cap:
            page_map.append(None)  # caption 缺失 → 等下一个非空 caption 回填
            continue
        # inherit 上一范围 (2026-08-06): 在 pdf_idx 用尽前先检查, 同章不同编号 (e.g. 电线电缆(1)(2)(3))
        # 继承上一 resolved 范围; 否则 pdf_idx >= len 会先兜底到最后一页, inherit 永远不触发
        if last_resolved_range is not None:
            prev_cap = last_resolved_range.get("cap", "")
            if prev_cap and _match_merge(md_cap, prev_cap):
                # inherit
                page_map.append(last_resolved_range["resolved"])
                continue
        if pdf_idx >= len(pdf_blocks):
            # pdf blocks 用尽 → 用最后一页兜底 (必须 inherit 检查后)
            last_page = pdf_blocks[-1][0] if pdf_blocks else 1
            page_map.append(f"p{last_page}")
            continue
        # 初始匹配: 完全相等 (主匹配, 避免误合 (1)(2)(3) 同章)
        start = next((i for i in range(pdf_idx, len(pdf_blocks)) if _match_exact(md_cap, pdf_blocks[i][1])), None)
        # 主匹配失败 → 兜底: 去编号后完全相等 (支持跨页续页 caption 用 (续))
        if start is None:
            start = next((i for i in range(pdf_idx, len(pdf_blocks)) if _match_merge(md_cap, pdf_blocks[i][1])), None)
        if start is None:
            # 完全没找到 caption 匹配 (2026-08-06 v6): inherit 上一范围 (占位 caption / OCR 丢失场景)
            # 否则 fallback 到 next pdf block page (子表 caption 不匹配但确实是新章节)
            if last_resolved_range is not None:
                page_map.append(last_resolved_range["resolved"])
                last_resolved_range = {"resolved": last_resolved_range["resolved"], "cap": md_cap}
            elif pdf_idx < len(pdf_blocks):
                resolved = f"p{pdf_blocks[pdf_idx][0]}"
                page_map = [resolved if p is None else p for p in page_map]
                page_map.append(resolved)
                last_resolved_range = {"resolved": resolved, "cap": md_cap}
                pdf_idx += 1
            else:
                page_map.append("p1")
            continue
        end = start
        # 跨页扩展: 完全相等 (主, 区分 (1)(2) 不同分表)
        while end + 1 < len(pdf_blocks) and _match_exact(md_cap, pdf_blocks[end + 1][1]):
            end += 1
        # 跨页扩展: 去编号合并 (兜底, 让 (续) 续页能合)
        while end + 1 < len(pdf_blocks) and _match_merge(md_cap, pdf_blocks[end + 1][1]):
            end += 1
        s, e = pdf_blocks[start][0], pdf_blocks[end][0]
        resolved = f"p{s}" if s == e else f"p{s}-{e}"
        page_map = [resolved if p is None else p for p in page_map]
        page_map.append(resolved)
        last_resolved_range = {"resolved": resolved, "cap": md_cap}
        pdf_idx = end + 1

    # final pass: 末尾残留 None 用 last resolved 兜底
    last = "p1"
    for i, p in enumerate(page_map):
        if p is None:
            page_map[i] = last
        else:
            last = p
    return page_map


def _is_header_row(row):
    """识别表头行 (cells=10/11 + row[0]='材料编码' + row[1]='材料名称')"""
    return (len(row) in (10, 11)) and row[0] == "材料编码" and row[1] == "材料名称"


def _is_chapter_or_caption(row):
    """A 类 sub-header: cells=1 单 cell (章节标题)"""
    return len(row) == 1


def _is_sub_header_b(row):
    """B 类 sub-header: cells=2 子列名 (区间值/均值/规格/单位 等)"""
    if len(row) != 2:
        return False
    keywords = {"区间值", "均值", "规格", "型号", "单位", "价格", "元", "金额"}
    return any(k in row[0] or k in row[1] for k in keywords)


def _is_sub_header_c(row):
    """C 类 sub-header: cells=4 跨列标题 (table[10] 的「平整场地/填方/挖方/运距」4 列)"""
    if len(row) != 4:
        return False
    return all(len(c) > 0 for c in row)


def _extract_half_rows(half):
    """单半表 (list[list], 含 5 列 + 6 列混合行) → list[dict] 数据行

    Step 1.6 schema (9 列 4 价格, PDF 原文命名):
      6 列: 材料编码|名称|规格|单位|区间值|均值 →
        interval_value = idx 4, mean_value = idx 5, incl = '', excl = ''
      5 列: 材料编码|名称|规格|单位|单价 →
        interval_value = '', mean_value = '', incl = '', excl = idx 4 (= 税前综合价格)

    输出字段: name/spec/unit/interval_value/mean_value/incl/excl/remark
    """
    if not half:
        return []
    out = []
    for r in half:
        n = len(r)
        if n == 6:
            out.append({
                "name": r[1].strip(),
                "spec": r[2].strip(),
                "unit": clean_unit(r[3]),
                "interval_value": _clean_price(r[4]),   # idx 4 区间值
                "mean_value": _clean_price(r[5]),       # idx 5 均值
                "incl": "",                              # 含税价 PDF 没
                "excl": "",                              # 除税价 idx 4-5 已填区间值/均值
                "remark": "",
            })
        elif n == 5:
            out.append({
                "name": r[1].strip(),
                "spec": r[2].strip(),
                "unit": clean_unit(r[3]),
                "interval_value": "",                    # 5 列没
                "mean_value": "",                        # 5 列没
                "incl": "",                              # 含税价 PDF 没
                "excl": _clean_price(r[4]),              # idx 4 单价 = 税前综合价格 = 除税价
                "remark": "",
            })
    return out


def _process_table(html):
    """处理单个 table HTML → list[dict] 数据行

    算法 (Step 1.6):
      1) 抽 HTML rows → 跳表头/章首/sub-header
      2) 全表一次 split_2d_table → (left, right)
      3) left + right 分别抽 dict
      4) 4 价格任一有真值 → 保留; 全空 → 删
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0

    # 1) 跳表头/章首/sub-header
    data_rows_raw = []
    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        if _is_header_row(row):
            n_header_skip += 1
            continue
        if _is_chapter_or_caption(row):
            n_chapter_skip += 1
            continue
        if _is_sub_header_b(row):
            n_sub_header_skip += 1
            continue
        if _is_sub_header_c(row):
            n_sub_header_skip += 1
            continue
        if len(row) < 5:
            continue
        data_rows_raw.append(row)

    # 2) 全表一次 split_2d_table (rows 含原始表头行用于检测列名重复)
    # 把表头行 (rows_raw 头几个) + data_rows_raw 拼起来
    # rows_raw 中, 第一个数据行之前的行 (caption/header/sub-header) 全保留供 split_2d_table 用
    header_rows = []
    for r in rows_raw:
        if not r or all(c.strip() == "" for c in r):
            continue
        if data_rows_raw and r is data_rows_raw[0]:
            break
        header_rows.append(r)

    # 把数据行从 data_rows_raw 转回完整 rows (header_rows + data_rows_raw)
    full_rows = header_rows + data_rows_raw
    left, right = split_2d_table(full_rows)

    # 3) 抽左+右 数据行
    data_rows = []
    for half in (left, right):
        if not half:
            continue
        for rec in _extract_half_rows(half):
            # 4) 4 价格任一有真值 → 保留; 全空 (含「—」占位) → 删
            if not any(
                _has_real_price(rec[k])
                for k in ("interval_value", "mean_value", "incl", "excl")
            ):
                n_clean_skip += 1
                continue
            data_rows.append(rec)

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


# ============================================================
# E_2: PHC+PRC 表头嵌套处理 (Step 2.2 加, 2026-08-05)
#   套成都 D2 思路 (caption 抽产品名 → 每行带产品名), 但 PHC 是 PDF 章节嵌套
#
#   table[2] 结构 (PDF 第 9 页):
#     row[0]   n=3  caption: idx 0 = PHC 全名, idx 1 = '', idx 2 = PRC 全名
#     row[1]   n=11 header (idx 5 空)  → skip
#     row[2]   n=10 自带名称: split=5, 左 PHC 5 (编码|名称|规格|单位|单价) + 右 PRC 5
#     row[3-17] n=8 缺名称: split=4, 左 PHC 4 (编码|型号规格|单位|单价) + 右 PRC 4
#     row[18-20] n=9 左 5「—」+ 右 PRC 4 (缺名称) → split=5, 删左, 补右 PRC 名
#
#   名称拼接: 缺名称行 = caption + " " + 型号规格
#   例: row[3] PHC = "预应力高强混凝土管桩(PHC) A型 Φ400×95"
#       row[3] PRC = "混合配筋预应力混凝土管桩(PRC)-I型 B型Φ500×100"
# ============================================================
def _process_phc_table(html):
    """PHC+PRC 嵌套表头处理 → list[dict] 数据行

    返回 (data_rows, stats) 跟 _process_table 一致格式
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0

    if len(rows_raw) < 3:
        return [], {"header_skip": 0, "chapter_skip": 0, "sub_header_skip": 0, "clean_skip": 0}

    # 1) 抽 caption (row[0] n=3, idx 0=PHC, idx 2=PRC, idx 1=空)
    cap = rows_raw[0]
    phc_name = cap[0].strip() if len(cap) > 0 else ""
    prc_name = cap[2].strip() if len(cap) > 2 else ""
    # 去 "税前综合价格" 后缀 (跟 caption 不混进材料名)
    for suffix in ("税前综合价格", "税前综合价格(元)"):
        phc_name = phc_name.replace(suffix, "").strip()
        prc_name = prc_name.replace(suffix, "").strip()
    n_chapter_skip += 1

    # 2) header (row[1] n=11) → skip
    n_header_skip += 1

    # 3) 数据行处理
    data_rows = []
    for i, row in enumerate(rows_raw[2:], start=2):
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)
        if n == 10:
            # row[2] 自带名称: split=5
            phc_part = row[:5]   # 编码, 名称, 规格, 单位, 单价
            prc_part = row[5:10]  # 编码, 名称, 规格, 单位, 单价
            data_rows.append(_phc_prc_row_5(phc_part))
            data_rows.append(_phc_prc_row_5(prc_part))
        elif n == 8:
            # row[3-17] 缺名称: split=4, 名称 = caption 全名 (不带型号规格), 规格 = 型号规格
            phc_part = row[:4]   # 编码, 型号规格, 单位, 单价
            prc_part = row[4:8]  # 编码, 型号规格, 单位, 单价
            data_rows.append(_phc_prc_row_4(phc_part, phc_name))
            data_rows.append(_phc_prc_row_4(prc_part, prc_name))
        elif n == 9:
            # row[18-20] 左 5「—」+ 右 4: split=5, 删左, 补右 PRC 名
            phc_part = row[:5]   # 全「—」→ 删
            prc_part = row[5:9]  # 编码, 型号规格, 单位, 单价 (n=4)
            data_rows.append(_phc_prc_row_4(prc_part, prc_name))
        # 其他长度 → skip (sub_header_skip)

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


def _phc_prc_row_5(part):
    """PHC/PRC row[2] n=5 自带名称: idx 0=编码, idx 1=名称, idx 2=规格, idx 3=单位, idx 4=单价"""
    return {
        "name": part[1].strip(),
        "spec": part[2].strip(),
        "unit": clean_unit(part[3]),
        "interval_value": "",                # PHC/PRC 单价, 没区间
        "mean_value": "",                    # 没均值
        "incl": "",                          # 没含税价
        "excl": _clean_price(part[4]),       # idx 4 单价 = 税前综合价格 = 除税价
        "remark": "",
    }


def _phc_prc_row_4(part, full_name):
    """PHC/PRC row[3+] n=4 缺名称: idx 0=编码, idx 1=型号规格, idx 2=单位, idx 3=单价
    full_name = caption 全名 (PHC/PRC), 型号规格不进 name, 单独放 spec
    """
    return {
        "name": full_name,                   # PHC/PRC 全名, 不带型号规格
        "spec": part[1].strip(),             # 型号规格 (A型 Φ400×95 / B型Φ500×100)
        "unit": clean_unit(part[2]),
        "interval_value": "",
        "mean_value": "",
        "incl": "",
        "excl": _clean_price(part[3]),
        "remark": "",
    }


# ============================================================
# E_busway: 密集型铜导体母线槽(IP54)税前综合价格(1) (md[29]) (2026-08-08)
#   套 PHC 表头嵌套模式, 但继承的不是产品名而是「电流等级」
#
#   md[29] 结构 (PDF 母线槽章节):
#     row[0]   n=1  caption (整张表标题)
#     row[1]   n=10 header (材料编码|产品名称|电流等级|单位|税前综合价格) × 2 sides  → skip
#     row[2]   n=10 自带电流等级: split=5
#       L: [code, name, 200A, unit, price]   R: [code, name, 400A, unit, price]
#     row[3-19] n=8 缺电流等级: split=4
#       L: [code, name, unit, price] (继承 200A)
#       R: [code, name, unit, price] (继承 400A)
#     row[20]  n=10 切换新电流等级 (250A/630A): split=5
#     row[21-37] n=8 缺等级 (继承 250A/630A)
#
#   emit: L+R 各 18 行 × 2 电流等级 = 72 行总 (L 36 + R 36)
#   schema: 复用金属材料 9 列, spec=电流等级 (200A/400A/250A/630A)
#   电流等级来自 row[2]/row[20] 的 idx 2, 其他行继承 state
# ============================================================
def _process_table_busway(html, skip_col=None):
    """密集型铜导体母线槽(IP54) PHC 风格嵌套 → list[dict] 数据行

    复用金属材料 9 列 schema, spec=电流等级 (200A/400A/250A/630A 等)

    skip_col 参数 (2026-08-08 加): 处理 md[30] p37 母线槽(2) 多 1 空白分隔列
      None (默认): 旧 md[29]/md[31] 路径 (n=10 split=5 + n=8 split=4)
      5: md[30] p37 n=11 路径 (L:5 + BLANK + R:5)
      4: 备选 n=9 路径 (L:4 + BLANK + R:4) — 实际 md[30] 用 5 (n=11)

    返回 (data_rows, stats) 跟 _process_table 一致格式
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0

    if len(rows_raw) < 3:
        return [], {"header_skip": 0, "chapter_skip": 0, "sub_header_skip": 0, "clean_skip": 0}

    # 1) row[0] n=1 caption (chapter_skip, 不进 dict)
    if rows_raw[0] and len(rows_raw[0]) == 1:
        n_chapter_skip += 1
    elif rows_raw[0]:
        n_chapter_skip += 1

    # 2) row[1] header (n=10 或 n=11 含空白列) → skip
    n_header_skip += 1

    # 3) 状态机: 电流等级 L/R state 继承 (PHC 模式: 产品名继承 → 这里: 电流等级继承)
    state = {"L": {"current_level": ""}, "R": {"current_level": ""}}

    data_rows = []
    for i, row in enumerate(rows_raw[2:], start=2):
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)
        if n == 10:
            # 自带电流等级 (md[29]/md[31] 锚行): split=5
            # L: [code, name, current_level, unit, price]
            # R: [code, name, current_level, unit, price]
            L_part = row[:5]
            R_part = row[5:10]
            # 更新 state
            state["L"]["current_level"] = L_part[2].strip()
            state["R"]["current_level"] = R_part[2].strip()
            # emit L + R
            data_rows.append(_busway_row_with_level(L_part))
            data_rows.append(_busway_row_with_level(R_part))
        elif n == 8:
            # 缺电流等级 (md[29]/md[31] 数据行): split=4, 继承 state
            L_part = row[:4]
            R_part = row[4:8]
            data_rows.append(_busway_row_inherit_level(L_part, state["L"]["current_level"]))
            data_rows.append(_busway_row_inherit_level(R_part, state["R"]["current_level"]))
        elif n == 11 and skip_col == 5:
            # md[30] p37 母线槽(2) 锚行: L:5 + BLANK(idx 5) + R:5
            L_part = row[:5]
            R_part = row[6:11]
            state["L"]["current_level"] = L_part[2].strip()
            state["R"]["current_level"] = R_part[2].strip()
            data_rows.append(_busway_row_with_level(L_part))
            data_rows.append(_busway_row_with_level(R_part))
        elif n == 9 and skip_col == 5:
            # md[30] p37 母线槽(2) 数据行: L:4 + BLANK(idx 4) + R:4
            L_part = row[:4]
            R_part = row[5:9]
            data_rows.append(_busway_row_inherit_level(L_part, state["L"]["current_level"]))
            data_rows.append(_busway_row_inherit_level(R_part, state["R"]["current_level"]))
        else:
            # 其他长度 → skip
            n_sub_header_skip += 1

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


def _busway_row_with_level(part):
    """母线槽 row[2]/row[20] n=5 自带电流等级: idx 0=编码, idx 1=名称, idx 2=电流等级, idx 3=单位, idx 4=单价"""
    return {
        "name": part[1].strip(),
        "spec": f"电流等级 {part[2].strip()}",   # 2026-08-08 改: 加「电流等级」前缀
        "unit": clean_unit(part[3]),
        "interval_value": "",                # 母线槽只有 1 列价
        "mean_value": "",
        "incl": "",
        "excl": _clean_price(part[4]),       # idx 4 单价 = 税前综合价格 = 除税价
        "remark": "",
    }


def _busway_row_inherit_level(part, current_level):
    """母线槽 row[3+] n=4 缺电流等级: idx 0=编码, idx 1=名称, idx 2=单位, idx 3=单价
    current_level = 继承自上一行 n=5 行 (state["L/R"]["current_level"])
    """
    return {
        "name": part[1].strip(),
        "spec": f"电流等级 {current_level}",    # 2026-08-08 改: 加「电流等级」前缀
        "unit": clean_unit(part[2]),
        "interval_value": "",
        "mean_value": "",
        "incl": "",
        "excl": _clean_price(part[3]),
        "remark": "",
    }


# ============================================================
# md[32] p39 母线槽(4) 5000A 单栏 (2026-08-08 加)
#   row[0]   n=5  header → skip
#   row[1]   n=5  锚行 [code, name, 5000A, unit, price] → emit 1 行
#   row[2+]  n=4  数据行 [code, name, unit, price], 继承 state["current_level"]=5000A
#   row[N]   n=1  说明文字 → 挂第一行 remark 字段
#
#   共 19 row: 1 header + 1 锚行 + 17 数据 + 1 说明 → 18 数据行
# ============================================================
def _process_table_busway_single(html):
    """密集型铜导体母线槽(IP54)(4) 单栏 (md[32] p39) → list[dict] 数据行

    返回 (data_rows, stats) 跟 _process_table 一致格式
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0

    if len(rows_raw) < 2:
        return [], {"header_skip": 0, "chapter_skip": 0, "sub_header_skip": 0, "clean_skip": 0}

    # 1) row[0] n=5 header → skip
    n_header_skip += 1

    state = {"current_level": ""}
    data_rows = []
    footer_note = ""

    for i, row in enumerate(rows_raw[1:], start=1):
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)
        if n == 5:
            # 锚行: [code, name, current_level, unit, price]
            # idx 0=编码, idx 1=名称, idx 2=电流等级 (5000A), idx 3=单位, idx 4=单价
            state["current_level"] = row[2].strip()
            data_rows.append({
                "name": row[1].strip(),
                "spec": f"电流等级 {state['current_level']}",  # 加前缀
                "unit": clean_unit(row[3]),
                "interval_value": "",
                "mean_value": "",
                "incl": "",
                "excl": _clean_price(row[4]),
                "remark": "",
            })
        elif n == 4:
            # 数据行: [code, name, unit, price], 继承 state["current_level"]
            data_rows.append({
                "name": row[1].strip(),
                "spec": f"电流等级 {state['current_level']}",
                "unit": clean_unit(row[2]),
                "interval_value": "",
                "mean_value": "",
                "incl": "",
                "excl": _clean_price(row[3]),
                "remark": "",
            })
        elif n == 1:
            # 说明文字 (r19): 挂第一行 remark
            footer_note = row[0].strip()
            n_sub_header_skip += 1
        else:
            n_sub_header_skip += 1

    # 挂 footer_note 到所有数据行 (2026-08-08 改: 用户说说明文字是整页的, 不只某一产品)
    if data_rows and footer_note:
        for row in data_rows:
            row["remark"] = footer_note

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


# ============================================================
# E_3: 单 cell caption + 单列 5 列 (Step 3 加, 2026-08-05)
#   适用 table[9] 铝单板 (类似 table[15] 不锈钢管等 1 cell sub-section)
#   套成都 D2 思路 (caption 抽 chapter_name), 但单列没 PHC 那种左右双 caption
#
#   table[9] 结构 (PDF 第 12 页):
#     row[0]   n=1  caption: chapter_name (单 cell 章节名, 放 remark 字段)
#     row[1]   n=5  header (材料编码|材料名称|规格|单位|税前综合价格) → skip
#     row[2+]  n=5  数据 (idx 0=编码, idx 1=名称自带, idx 2=规格, idx 3=单位, idx 4=单价)
#   名称自带 → 不需要 caption 补
# ============================================================
def _process_table_3(html):
    """单 cell caption + 单列 5 列处理 → list[dict] 数据行

    返回 (data_rows, stats) 跟 _process_table 一致格式
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0

    if len(rows_raw) < 3:
        return [], {"header_skip": 0, "chapter_skip": 0, "sub_header_skip": 0, "clean_skip": 0}

    # 1) 抽 caption (row[0] n=1, chapter_name)
    cap_cell = rows_raw[0][0].strip() if rows_raw[0] else ""
    chapter_name = cap_cell
    # 去 "税前综合价格" 后缀 (跟 PHC E_2 一致)
    for suffix in ("税前综合价", "税前综合价格", "税前综合价格(元)"):
        chapter_name = chapter_name.replace(suffix, "").strip()
    n_chapter_skip += 1

    # 2) header (row[1] n=5) → skip
    n_header_skip += 1

    # 3) 数据行处理 (row[2+] n=5, 自带名称)
    data_rows = []
    for row in rows_raw[2:]:
        if not row or all(c.strip() == "" for c in row):
            continue
        if len(row) != 5:
            continue
        # idx 0=编码(跳), idx 1=名称自带, idx 2=规格, idx 3=单位, idx 4=单价
        data_rows.append({
            "name": row[1].strip(),
            "spec": row[2].strip(),
            "unit": clean_unit(row[3]),
            "interval_value": "",
            "mean_value": "",
            "incl": "",
            "excl": _clean_price(row[4]),   # idx 4 单价 = 税前综合价格 = 除税价
            "remark": "",                    # 2026-08-06: 删 chapter_name 臆填, 备注只跟 PDF 原文一致
        })

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


# ============================================================
# E_4: 预拌混凝土 继承式 (Step 4 加, 2026-08-05)
#   适用 table[3] PDF 第 9 页 预拌混凝土税前综合价格（1）
#   套成都 D2 思路 (自带名 anchor → 缺名继承), 但 anchor 是 data row (不是 caption)
#
#   table[3] 结构 (PDF 第 9 页):
#     row[0]  n=10 表头 (5+5 重复, 材料编码|材料名称|规格|单位|税前综合价格) → skip
#     row[1]  n=4  sub-header 区间值/均值/区间值/均值 (C 类 n=4 全非空) → skip
#     row[2]  n=12 自带名 anchor (idx 1=名称 自带, idx 2=规格)
#       left:  idx 0=编码 idx 1=名称 idx 2=规格 idx 3=单位 idx 4=区间值 idx 5=均值
#       right: idx 6=编码 idx 7=名称 idx 8=规格 idx 9=单位 idx 10=区间值 idx 11=均值
#     row[3-11]  n=10 缺名 (左继承 right? left 用 left_anchor, right 用 right_anchor)
#       left:  idx 0=编码 idx 1=规格 idx 2=单位 idx 3=区间值 idx 4=均值 (idx 1 实际是规格「C20」)
#       right: idx 5=编码 idx 6=规格 idx 7=单位 idx 8=区间值 idx 9=均值
#     row[12] n=12 自带名 anchor (idx 1=「防水混凝土P6~P8」)
#     row[13-20] n=10 缺名 → 继承「防水混凝土P6~P8」
#     row[21] n=12 自带名 anchor (idx 1=「防水混凝土P10~P12」)
#     row[22-29] n=10 缺名 → 继承「防水混凝土P10~P12」
#
#   split_2d_table 处理:
#     n=12 → split=header_split+1=6 (左 6 + 右 6)
#     n=10 → idx 5 是 18 位编码 → split=eff_split=5 (左 5 + 右 5)
#   (utils_gz.py 已加 n=header_n_cols+2 分支)
#
#   继承逻辑: 维护 left_anchor_name / right_anchor_name, 遇 n=6 anchor 更新, n=5 缺名用
# ============================================================
def _process_table_4(html):
    """预拌混凝土继承式处理 → list[dict] 数据行

    返回 (data_rows, stats) 跟 _process_table 一致格式
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0

    if len(rows_raw) < 3:
        return [], {"header_skip": 0, "chapter_skip": 0, "sub_header_skip": 0, "clean_skip": 0}

    # 1) 跳表头/章首/sub-header
    data_rows_raw = []
    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        if _is_header_row(row):
            n_header_skip += 1
            continue
        if _is_chapter_or_caption(row):
            n_chapter_skip += 1
            continue
        if _is_sub_header_b(row) or _is_sub_header_c(row):
            n_sub_header_skip += 1
            continue
        if len(row) < 5:
            continue
        data_rows_raw.append(row)

    # 2) split_2d_table (复用 utils_gz.py)
    header_rows_for_split = []
    for r in rows_raw:
        if not r or all(c.strip() == "" for c in r):
            continue
        if data_rows_raw and r is data_rows_raw[0]:
            break
        header_rows_for_split.append(r)
    full_rows = header_rows_for_split + data_rows_raw
    left, right = split_2d_table(full_rows)

    # 3) left + right 分别抽 dict (自带名 anchor + 缺名继承, 跟 Step 1.6 一致「left 先 + right 后」)
    data_rows = []
    for half in (left, right):
        if not half:
            continue
        current_name = ""
        for r in half:
            n = len(r)
            if n == 6:
                # 自带名 anchor: idx 0=编码 idx 1=名称 idx 2=规格 idx 3=单位 idx 4=区间 idx 5=均值
                current_name = r[1].strip()
                data_rows.append({
                    "name": current_name,
                    "spec": r[2].strip(),
                    "unit": clean_unit(r[3]),
                    "interval_value": _clean_price(r[4]),
                    "mean_value": _clean_price(r[5]),
                    "incl": "",
                    "excl": "",
                    "remark": "",
                })
            elif n == 5:
                # 缺名继承: idx 0=编码 idx 1=规格 idx 2=单位 idx 3=区间 idx 4=均值
                data_rows.append({
                    "name": current_name,
                    "spec": r[1].strip(),
                    "unit": clean_unit(r[2]),
                    "interval_value": _clean_price(r[3]),
                    "mean_value": _clean_price(r[4]),
                    "incl": "",
                    "excl": "",
                    "remark": "",
                })
            else:
                n_clean_skip += 1

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


# ============================================================
# E_5: 沥青混凝土 8 列拼接 (Step 6 加, 2026-08-05)
#   适用 table[4] PDF 第 10 页 row[17-24] 沥青混凝土税前综合价格
#   (row[0-15] 是水下混凝土段, 跳过不处理)
#
#   table[4] 整体结构 (PDF 第 10 页, 水下混凝土 + 沥青混凝土拼同一 table):
#     row[0]   n=10 表头 → skip
#     row[1]   n=4  sub-header → skip
#     row[2-15] n=12/n=10 水下混凝土 → 跳 (水下混凝土未接)
#     row[16]  n=1  说明文字 → skip (chapter)
#     row[17]  n=1  「沥青混凝土税前综合价格」 → 沥青段 anchor (chapter_name)
#     row[18]  n=8  表头 材料编码|材料名称|单位|税前综合价格 × 2 (4+4) → skip
#     row[19-23] n=8 数据 (左 4 + 右 4, idx 1 名称 cell 合并「AC+花岗岩」)
#     row[24]  n=5  数据 (左 4 + 右 1 说明文字)
#
#   split_2d_table 现有 n=8 → idx 5 是右编码 18 位 → split=eff_split=4 ✓
#   row[24] n=5 → 左 4 (idx 0-3) 数据 + 右 1 (idx 4) 说明 → 左半进 sheet, 右半 skip (n<5)
# ============================================================
def _process_table_5(html):
    """table[4] (PDF p10) 双段: row[0-15] 水下混凝土 + row[17-24] 沥青混凝土

    2026-08-06 修复: 之前 E_5 只处理沥青段, 水下混凝土 (14 行) 被 caption dispatch 'asphalt' 覆盖
    而被静默 skip. 现在重构成双段处理, 输出 dict 跟 E_4/E_5 一致 schema (无臆填备注).

    返回 (data_rows, stats)
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0
    data_rows = []  # 2026-08-06: 双段输出 (水下 + 沥青), 共享 list

    # === 段 1: row[0-15] 水下混凝土 (anchor+inherit, 跟 E_4 同结构) ===
    # row[0] n=10 header → skip; row[1] n=4 sub-header → skip
    if len(rows_raw) >= 3:
        if _is_header_row(rows_raw[0]):
            n_header_skip += 1
        if _is_sub_header_b(rows_raw[1]) or _is_sub_header_c(rows_raw[1]):
            n_sub_header_skip += 1

        underwater_rows_raw = []
        for r in rows_raw[2:16]:  # row[2-15] 水下混凝土数据
            if not r or all(c.strip() == "" for c in r):
                continue
            if len(r) < 5:
                continue
            underwater_rows_raw.append(r)

        # 用 split_2d_table 切左右 (header_rows_for_split = row[0]+row[1] header+sub-header)
        header_rows_for_split = [rows_raw[0], rows_raw[1]]
        full_rows = header_rows_for_split + underwater_rows_raw
        u_left, u_right = split_2d_table(full_rows)

        # 抽水下段 data_rows (跟 E_4 一样的 anchor+inherit 逻辑)
        for half in (u_left, u_right):
            if not half:
                continue
            current_name = ""
            for r in half:
                n = len(r)
                if n == 6:
                    current_name = r[1].strip()
                    data_rows.append({
                        "name": current_name,
                        "spec": r[2].strip(),
                        "unit": clean_unit(r[3]),
                        "interval_value": _clean_price(r[4]),
                        "mean_value": _clean_price(r[5]),
                        "incl": "",
                        "excl": "",
                        "remark": "",
                    })
                elif n == 5:
                    data_rows.append({
                        "name": current_name,
                        "spec": r[1].strip(),
                        "unit": clean_unit(r[2]),
                        "interval_value": _clean_price(r[3]),
                        "mean_value": _clean_price(r[4]),
                        "incl": "",
                        "excl": "",
                        "remark": "",
                    })
                else:
                    n_clean_skip += 1

    # === 段 2: row[17+] 沥青混凝土 (n=8 拼接, 4+4) ===
    asphalt_anchor_idx = None
    for i, r in enumerate(rows_raw):
        if len(r) == 1 and "沥青混凝土" in r[0]:
            asphalt_anchor_idx = i
            break
    if asphalt_anchor_idx is not None:
        n_chapter_skip += 1
        # 沥青表头 row[asphalt_anchor_idx+1] n=8 → skip
        header_idx = asphalt_anchor_idx + 1
        if header_idx < len(rows_raw) and len(rows_raw[header_idx]) == 8:
            n_header_skip += 1

        # 沥青数据 row[asphalt_anchor_idx+2:] 手动切 left + right (header n=8 = 4+4, split_2d_table 不支持)
        data_rows_raw_asphalt = rows_raw[header_idx + 1:]
        a_left, a_right = [], []
        for r in data_rows_raw_asphalt:
            n = len(r)
            if n >= 8:
                a_left.append(r[:4])
                a_right.append(r[4:8])
            elif n >= 4:
                a_left.append(r[:4])
                if n > 4:
                    a_right.append(r[4:])
                else:
                    a_right.append([])

        # 抽沥青段 data_rows (idx 1 名称 cell 已合并, spec 空)
        for half in (a_left, a_right):
            if not half:
                continue
            for r in half:
                n = len(r)
                if n == 4:
                    # idx 0=编码 idx 1=名称+规格+石料(合并) idx 2=单位 idx 3=单价
                    data_rows.append({
                        "name": r[1].strip(),
                        "spec": "",                          # idx 1 已合并 AC+花岗岩, 没单独规格列
                        "unit": clean_unit(r[2]),
                        "interval_value": "",
                        "mean_value": "",
                        "incl": "",
                        "excl": _clean_price(r[3]),            # idx 3 单价 = 除税价
                        "remark": "",                         # 2026-08-06: 删 chapter_name 臆填, 备注只跟 PDF 原文一致
                    })
                else:
                    n_clean_skip += 1  # n=1 说明文字等 skip

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


def _number_rows_independently(rows):
    for i, r in enumerate(rows, 1):
        r["seq"] = i
    return rows


# ============================================================
# E_6: 干混/湿拌砂浆继承式 (Step 8 加, 2026-08-05)
#   适用 table[5] PDF 第 10-12 页 干混砂浆+湿拌砂浆 (3 段共用一个 extractor)
#   套成都 D2 + 武汉 mortar 思路 (anchor + 继承), 但砂浆有 4 种 anchor 长度 + 2 种 inherit 长度
#
#   table[5] 整体结构 (PDF 第 10-12 页, 3 段拼同一 table):
#     段 1 (row[0-25]):  干混砂浆税前综合价格 (1)
#       row[0]   n=8 表头 (材料编码|材料名称|性能指标|强度等级|单位|税前综合价格|适用范围|t/m³系数)
#       row[1]   n=2 sub-header (区间值|均值)
#       row[2]   n=9 anchor: 普通干混砌筑砂浆 M5 t 236~260 248.0 scope 1.60
#       row[3-6] n=6 缺名 inherit: M7.5/M10/M15/M20 (idx 5 = 1.60 t/m³系数)
#       row[7]   n=9 anchor: 薄层干混砌筑砂浆 M5
#       row[8]   n=6 缺名 inherit: M10
#       row[9]   n=9 anchor: 普通干混抹灰砂浆 M5
#       row[10-13] n=6 缺名 inherit: M7.5/M10/M15/M20
#       row[14]  n=9 anchor: 薄层干混抹灰砂浆 M5
#       row[15-16] n=6 缺名 inherit: M7.5/M10
#       row[17]  n=9 anchor: 干混地面砂浆 M15
#       row[18-19] n=6 缺名 inherit: M20/M25
#       row[20]  n=9 anchor: 干混防水砂浆:P6 M15 (scope + t/m³系数)
#       row[21]  n=6 缺名 inherit: M20
#       row[22]  n=8 anchor: 干混防水砂浆:P8 M15 (OCR 漏 scope → idx 7 是 t/m³系数 1.55)
#       row[23]  n=6 缺名 inherit: M20
#       row[24]  n=8 anchor: 干混防水砂浆:P10 M15 (同上 OCR 漏 scope)
#       row[25]  n=6 缺名 inherit: M20
#     段 2 (row[26-39]): 干混砂浆税前综合价格 (2) 保温砂浆
#       row[26-27] n=1 章节+段标题 → skip
#       row[28]   n=8 表头 → skip
#       row[29]   n=2 sub-header → skip
#       row[30-32] n=9 anchor 保温砂浆 3 行 (无 inherit, 每行自带)
#       row[33]   n=1 说明 → skip
#       row[34-39] n=5 配合比对照表 (横表, 不是材料) → skip
#     段 3 (row[40-68]): 湿拌砂浆税前综合价格
#       row[40]   n=1 段标题 → skip
#       row[41]   n=7 表头 (湿拌表头, 没 t/m³系数)
#       row[42]   n=2 sub-header → skip
#       row[43]   n=8 anchor: 湿拌砌筑砂浆 M5 m3 scope (idx 7 = scope)
#       row[44-47] n=5 缺名 inherit: M7.5/M10/M15/M20 (没 t/m³系数)
#       row[48]   n=8 anchor: 湿拌抹灰砂浆 M5
#       row[49-52] n=5 缺名 inherit: M7.5/M10/M15/M20
#       row[53]   n=8 anchor: 湿拌地面砂浆 M15
#       row[54-55] n=5 缺名 inherit: M20/M25
#       row[56]   n=8 anchor: 湿拌防水砂浆:P6 M15
#       row[57]   n=5 缺名 inherit: M20
#       row[58]   n=7 anchor: 湿拌防水砂浆:P8 M15 (OCR 漏 scope → 没 idx 7)
#       row[59]   n=5 缺名 inherit: M20
#       row[60]   n=7 anchor: 湿拌防水砂浆:P10 M15
#       row[61]   n=5 缺名 inherit: M20
#       row[62-68] n=1/n=5 说明+配合比 → skip
#
#   预期总行数: 22 (段1) + 3 (段2) + 18 (段3) = 43 行
#
#   算法:
#     - 4 列 anchor (n=9/8/7): idx 2=性能 idx 3=强度 idx 4=单位 idx 5=区间 idx 6=均值 idx 7=scope/t/m³系数
#     - 4 列 inherit (n=6/5): idx 1=强度 idx 2=单位 idx 3=区间 idx 4=均值 idx 5=t/m³系数(n=6) / 无(n=5)
#     - anchor 识别: idx 1 是「M数字」→ inherit; 否则 → anchor 更新 current_name
#     - scope vs t/m³系数: idx 7 含中文 → scope; 纯数字 → t/m³系数 → scope=""
#     - HTML 实体解码: scope 含 &lt; &gt; → 解码
# ============================================================
import html as _html_lib
_CHINESE_RE = re.compile(r"[一-鿿]")
_STRENGTH_RE = re.compile(r"^M\d+(\.\d+)?$")


def _decode_entities(s):
    """HTML 实体解码: &lt; &gt; &amp; → < > &"""
    return _html_lib.unescape(str(s))


_LATEX_WRAP_RE = re.compile(r"^\$(.*)\$$")           # $...$
_LATEX_TIMES_RE = re.compile(r"\\times")               # \times → ×
_LATEX_SUB_RE = re.compile(r"_\{(\d+)\}")              # _{N} 脚标 → N (如 VV_{22} → VV22)


def _clean_spec(s):
    """电缆 spec 清理: 解 HTML entity + 剥 LaTeX wrapper + \times→× + 去空格

    PDF 原文: "4×120+1×50"
    MinerU OCR: "$4 \\times 120 + 1 \\times 50$"
    清理后: "4×120+1×50"
    """
    s = _decode_entities(s).strip()
    # 剥 $...$ wrapper
    m = _LATEX_WRAP_RE.match(s)
    if m:
        s = m.group(1).strip()
    # \times → ×
    s = _LATEX_TIMES_RE.sub("×", s)
    # _{N} → N (脚标还原)
    s = _LATEX_SUB_RE.sub(r"\1", s)
    # 去空格 (4×120 + 1×50 → 4×120+1×50)
    s = re.sub(r"\s+", "", s)
    return s


_BRACKET_INNER_RE = re.compile(r"([\(\（])\s+|\s+([\)\）])")   # 括号内侧空格


def _clean_name(s):
    """产品名清理: 剥 LaTeX wrapper + 还原脚标 + 去括号内侧空格

    PDF 原文: "0.6/1kV铜芯聚氯乙烯绝缘钢带铠装聚氯乙烯护套电力电缆(VV22)"
    MinerU OCR: "0.6/1kV铜芯聚氯乙烯绝缘钢带铠装聚氯乙烯护套电力电缆( $VV_{22}$ )"
    清理后: "0.6/1kV铜芯聚氯乙烯绝缘钢带铠装聚氯乙烯护套电力电缆(VV22)"
    """
    if not s:
        return ""
    s = _decode_entities(str(s)).strip()
    # 剥所有 $...$ wrapper (R7: name 里 LaTeX 块可能不止 1 段, 用 replace 而非 match)
    s = s.replace("$", "")
    # _{N} → N
    s = _LATEX_SUB_RE.sub(r"\1", s)
    # 括号内侧空格去掉 (( VV22 ) → (VV22))
    s = _BRACKET_INNER_RE.sub(lambda m: m.group(1) or m.group(2), s)
    return s


def _is_strength_cell(s):
    """idx 1 是「M5/M7.5/M10/M15/M20/M25」强度 → True"""
    return bool(_STRENGTH_RE.match(s.strip()))


def _has_chinese(s):
    """字符串是否含中文 (区分 scope vs t/m³系数)"""
    return bool(_CHINESE_RE.search(str(s)))


def _process_table_6(html):
    """干混/湿拌砂浆继承式处理 (Step 8.1, 11 列 schema) → list[dict] 数据行

    字段映射 (按 PDF 原文 + 4 价格):
      anchor (n=9/8/7): idx 0=编码 1=名称 2=性能 3=强度 4=单位 5=区间 6=均值 7=scope/t/m³系数 8=coeff (n=9)
      inherit (n=6/5): idx 0=编码 1=强度(M数字) 2=单位 3=区间 4=均值 5=t/m³系数 (n=6) / 无 (n=5)

    inherit 行字段来源:
      名称 → 继承 anchor (current_name)
      性能指标 → 继承 anchor (current_perf)
      强度等级 → inherit 自带 (idx 1)
      适用范围 → 继承 anchor (current_scope)
      t/m³系数 → inherit 自带 (idx 5, n=6) / 空 (n=5)

    anchor idx 7 scope vs t/m³系数 自动判定:
      idx 7 含中文 → scope = idx 7 (HTML 实体解码); coeff = idx 8 (n=9) / 空 (n=8 湿拌)
      idx 7 纯数字 → scope = "" (OCR 漏); coeff = idx 7 (干拌 anchor OCR 漏 scope)
      n=7 → scope = "", coeff = "" (湿拌 anchor OCR 漏 scope)

    返回 (data_rows, stats)
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0
    n_horiz_skip = 0

    data_rows = []
    current_name = ""
    current_perf = ""
    current_scope = ""

    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)

        # n=1: 章节/段标题/说明 → skip
        if n == 1:
            n_chapter_skip += 1
            continue

        # n=2: sub-header (区间值/均值) → skip
        if n == 2:
            n_sub_header_skip += 1
            continue

        # 表头行: 含「材料编码」 → skip
        if n in (7, 8) and row[0] == "材料编码":
            n_header_skip += 1
            continue

        # 配合比对照表 (n=5, row[0] 不是 18 位编码而是中文) → skip
        if n == 5 and not (row[0].isdigit() and len(row[0]) >= 15):
            n_horiz_skip += 1
            continue

        # 数据行: row[0] 是 18 位数字编码
        if not (row[0].isdigit() and len(row[0]) >= 15):
            continue

        idx1 = row[1].strip() if n > 1 else ""
        is_strength = _is_strength_cell(idx1)

        if is_strength:
            # inherit 行: 名称/性能/适用范围 继承 anchor
            strength = idx1
            performance = current_perf
            scope = current_scope
            unit = clean_unit(row[2]) if n > 2 else ""
            interval_value = _clean_price(row[3]) if n > 3 else ""
            mean_value = _clean_price(row[4]) if n > 4 else ""
            # coeff: idx 5 (n=6) / "" (n=5)
            coeff = _clean_price(row[5]) if n > 5 else ""
        else:
            # anchor 行: 更新 3 个 state, 提取字段
            current_name = idx1
            current_perf = row[2].strip() if n > 2 else ""
            strength = row[3].strip() if n > 3 else ""
            unit = clean_unit(row[4]) if n > 4 else ""
            interval_value = _clean_price(row[5]) if n > 5 else ""
            mean_value = _clean_price(row[6]) if n > 6 else ""
            # scope vs coeff 自动判定
            scope_cell = row[7].strip() if n > 7 else ""
            if _has_chinese(scope_cell):
                # idx 7 是 scope (中文, HTML 实体解码)
                scope = _decode_entities(scope_cell)
                coeff = _clean_price(row[8]) if n > 8 else ""  # n=8 湿拌没 coeff
            else:
                # idx 7 是 t/m³系数数字 (OCR 漏 scope) 或 n=7 没 idx 7
                # 大类共享 scope (2026-08-06): 不覆盖 current_scope, 保留上一 anchor 的
                # 例: 防水砂浆 P6 anchor 有 scope, P8/P10 anchor idx 7 空 → 共享 P6 的 scope
                scope = current_scope
                coeff = _clean_price(scope_cell) if scope_cell else ""
            current_scope = scope  # 维护 state 给 inherit 行
            performance = current_perf  # anchor 自己就是 performance 来源

        # 4 价格任一有真值 → 保留; 全空 → 删
        if not (_has_real_price(interval_value) or _has_real_price(mean_value)):
            n_clean_skip += 1
            continue

        data_rows.append({
            "name": current_name,
            "performance": performance,
            "strength": strength,
            "unit": unit,
            "interval_value": interval_value,
            "mean_value": mean_value,
            "incl": "",
            "excl": "",
            "scope": scope,
            "coeff": coeff,
        })

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
        "horiz_skip": n_horiz_skip,
    }


# ============================================================
# E_6 蒸压加气混凝土板 + 铝合金门窗 (md[7], p15, 2026-08-06 新接 / 08-06 修)
#   md[7] 是「多子段拼表」: 一个 <table> 里塞了 2 张 PDF 表, 用 colspan=11 整行分割符隔开
#     子段 A (6 列): 蒸压加气混凝土板  → 3 行 (anchor n=6 + inherit n=3)
#     分割符 (n=1, td colspan=11): 说明 / 五、门窗制品 / 铝合金门窗税前综合价
#     子段 B (5+5 split): 铝合金门窗  → 7 行 × 左右 2 半 = 14 行 (header n=11, 数据 n=10)
#
#   ⚠️ 分割符判定必须看 colspan 而不是 cell 数:
#     子段 A 的 inherit 行 `<td>code</td><td>厚150mm</td><td colspan="6">489</td>` 也含 colspan,
#     但它是 3-cell 行; 只有「整行 1 个 td 且 colspan >= 11」才是子段分割符.
#
#   子段 A 字段映射 → sheet「金属材料」10 列:
#     序号 = 材料编码 (后续 _number_rows_independently 重编)
#     名称 = "蒸压加气混凝土板"; 规格 = inherit 行 idx 1 / anchor 拆规格段
#     单位 = m³; 除税价 = 单值; 执行标准 = "GB/T 15762-2020"
#   子段 B 复用 _process_table_dropdown (同 md[8] 铝扣板的 5+5 split + anchor/inherit)
# ============================================================
_T7_SEP_COLSPAN_MIN = 11  # 整行分割符最小 colspan (子段内最大 colspan=6)


def _tr_cell_tags(tr_html):
    """抽 <tr> 内所有 <td>/<th> 开标签 (保留属性, 用于读 colspan)"""
    return re.findall(r"<t[hd][^>]*>", tr_html)


def _td_colspan(tag):
    """读 <td colspan="11"> 的 colspan 值, 无则 1"""
    m = re.search(r'colspan\s*=\s*["\']?(\d+)', tag)
    return int(m.group(1)) if m else 1


def _is_t7_separator_tr(tr_html):
    """子段分割符判定: 整行只有 1 个 td 且 colspan >= 11 (说明/章节名/子表 caption)"""
    tags = _tr_cell_tags(tr_html)
    return len(tags) == 1 and _td_colspan(tags[0]) >= _T7_SEP_COLSPAN_MIN


def _split_t7_segments(html):
    """按 colspan=11 整行分割符把 md[7] 切成子段 → (segments, n_sep)

    segments: list[(kind, sub_html)], kind = "six" (6 列蒸压加气) / "split" (5+5 铝合金门窗)
    kind 判定: 子段内出现 >= 10 cell 的行 → "split", 否则 "six"
    n_sep: 分割符行数 (计入 chapter_skip)
    """
    trs = re.findall(r"<tr>.*?</tr>", html, re.DOTALL)
    groups = []
    cur = []
    n_sep = 0
    for tr in trs:
        if _is_t7_separator_tr(tr):
            n_sep += 1
            if cur:
                groups.append(cur)
                cur = []
            continue
        cur.append(tr)
    if cur:
        groups.append(cur)

    segments = []
    for g in groups:
        max_cells = max((len(_tr_cell_tags(tr)) for tr in g), default=0)
        kind = "split" if max_cells >= 10 else "six"
        segments.append((kind, "<table>" + "".join(g) + "</table>"))
    return segments, n_sep


def _process_table_7(html):
    """md[7] 多子段拼表 → list[dict] 数据行 (子段 A 6 列 + 子段 B 5+5 split, 按顺序合并)

    子段路由:
      kind="six"   → _process_t7_six        (蒸压加气 3 行)
      kind="split" → _process_table_dropdown (铝合金门窗 14 行, 复用 md[8] 铝扣板逻辑)
    """
    segments, n_sep = _split_t7_segments(html)

    data_rows = []
    stats = {
        "header_skip": 0,
        "chapter_skip": n_sep,  # colspan=11 分割符 (说明/章节名/caption)
        "sub_header_skip": 0,
        "clean_skip": 0,
        "horiz_skip": 0,
    }

    for kind, sub_html in segments:
        if kind == "split":
            rows, st = _process_table_dropdown(sub_html)
        else:
            rows, st = _process_t7_six(sub_html)
        data_rows.extend(rows)
        for k, v in st.items():
            stats[k] = stats.get(k, 0) + v

    return data_rows, stats


def _process_t7_six(html):
    """蒸压加气混凝土板子段 (md[7] 子段 A, 6 列) → list[dict] 数据行

    结构 (2026-08-06):
      Row 0: 表头 (n=6) — skip
      Row 1: anchor (n=6) — [code, name+spec描述, spec, unit, price单值, std]
        → name 拆: 「蒸压加气混凝土板」+spec 描述
      Row 2+: inherit (n=3) — [code, spec, price]
        → name inherit from anchor, spec = idx 1, price = idx 2
        → unit inherit from anchor (idx 3), std inherit from anchor (idx 5)

    输出 (sheet「金属材料」10 列):
      序号 = code (18位)
      名称 = "蒸压加气混凝土板"
      规格 = anchor: spec_part; inherit: idx 1
      单位 = "$m^3$" → "m³"
      区间值/均值/含税 = 空
      除税价 = anchor idx 4 / inherit idx 2 (单值, 用户拍板)
      执行标准 = "GB/T 15762-2020" (anchor only)
      备注 = 空
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0

    data_rows = []
    current_name = ""
    current_unit = ""
    current_std = ""

    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)

        # 表头行: n=6 + idx 0 = "材料编码" → skip
        if n == 6 and row[0] == "材料编码":
            n_header_skip += 1
            continue

        # 数据行判定: row[0] 是 18 位数字编码
        if not (row[0].isdigit() and len(row[0]) >= 15):
            n_chapter_skip += 1
            continue

        if n == 6:
            # anchor: name 拆 + unit/std 都取
            idx1 = row[1].strip()  # "蒸压加气混凝土板A5.0 B06隔墙板"
            spec_part = idx1.replace("蒸压加气混凝土板", "").strip()
            current_name = "蒸压加气混凝土板"
            current_unit = clean_unit(row[3])  # "$m^3$" → "m³"
            current_std = row[5].strip()  # "GB/T 15762-2020"
            price = row[4].strip()  # 单值 "496"
            spec = spec_part
            std = current_std
        elif n == 3:
            # inherit: name/unit/std inherit; spec/price 从本行 idx 1/idx 2
            spec = row[1].strip()  # "厚150mm"
            price = row[2].strip()  # "489"
            std = ""  # inherit 不重复执行标准
        else:
            n_chapter_skip += 1
            continue

        data_rows.append({
            "name": current_name,
            "spec": spec,
            "unit": current_unit,
            "interval_value": "",
            "mean_value": "",
            "incl": "",
            "excl": _clean_price(price),  # 单值放除税价 (用户拍板 2026-08-06)
            "execution_standard": std,
            "remark": "",
        })

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": 0,
        "clean_skip": 0,
        "horiz_skip": 0,
    }


# ============================================================
# E_7 装配式构件 (md[6], p14, 2026-08-06 新接)
#   schema (PDF 原文 6 列): 材料编码|项目名称|钢筋含量(kg/m3)|钢筋含量|单位|税前综合价格(元)
#   实际结构:
#     Row 0: 表头 n=6 → skip
#     Row 1+: 数据
#       anchor (n=8): [code, 项目名称, 钢筋含量总范围, 钢筋含量本段, 单位, price下限, "-", price上限]
#         → name=项目名称, spec=钢筋含量本段, interval="price下限~price上限"
#       inherit (n=5): [code, 钢筋含量本段, price下限, "-", price上限]
#         → name inherit, spec=本段, interval="price下限~price上限"
#
#   输出 (sheet「金属材料」10 列):
#     序号 = code
#     名称 = 项目名称 (anchor 才有)
#     规格 = 钢筋含量本段 (anchor idx 3 / inherit idx 1)
#     单位 = "m3"
#     区间值 = price下限~price上限 ("-" → "~")
#     均值/含税/除税/执行标准/备注 = 空
# ============================================================
def _process_table_8(html):
    """装配式构件 (md[6], p14) → list[dict] 数据行

    输出 (sheet「建筑装配式构件」6 列 schema, 2026-08-06 user 拍板独立 sheet):
      序号 = _number_rows_independently 自动 1-36
      项目名称 = anchor idx 1 (inherit inherit anchor)
      钢筋含量(kg/m3) = anchor idx 2 (总范围, inherit inherit anchor)
      钢筋含量 = anchor idx 3 / inherit idx 1 (本段)
      单位 = "m3"
      税前综合价格(元)区间值 = "price_low~price_high" ("-" 替换为 "~")
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0

    data_rows = []
    current_name = ""          # 项目名称 (anchor inherit)
    current_rebar_kg_m3 = ""   # 钢筋含量(kg/m3) 总范围 (anchor inherit)

    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)

        # 表头行 n=6 + idx 0 = "材料编码" → skip
        if n == 6 and row[0] == "材料编码":
            n_header_skip += 1
            continue

        # 数据行判定: row[0] 是 18 位数字编码
        if not (row[0].isdigit() and len(row[0]) >= 15):
            n_chapter_skip += 1
            continue

        if n == 8:
            # anchor: idx 0=code (丢), 1=项目名称, 2=钢筋含量总范围, 3=钢筋含量本段, 4=单位, 5=price_low, 6="-", 7=price_high
            current_name = row[1].strip()         # "预制外墙板"
            current_rebar_kg_m3 = row[2].strip()  # "85-145"
            rebar_segment = row[3].strip()        # "85-100" (本段)
            price_low = row[5].strip()            # "2101"
            price_high = row[7].strip()           # "2160"
        elif n == 5:
            # inherit: idx 0=code (丢), 1=钢筋含量本段, 2=price_low, 3="-", 4=price_high
            rebar_segment = row[1].strip()        # "100-115"
            price_low = row[2].strip()            # "2160"
            price_high = row[4].strip()           # "2236"
        else:
            n_chapter_skip += 1
            continue

        interval_value = f"{price_low}~{price_high}" if price_low and price_high else ""

        data_rows.append({
            "name": current_name,
            "rebar_kg_m3": current_rebar_kg_m3,    # anchor 自带, inherit inherit
            "rebar_segment": rebar_segment,         # 本段 (每行自带)
            "unit": "m3",
            "interval_value": interval_value,
        })

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": 0,
        "clean_skip": 0,
        "horiz_skip": 0,
    }


# ============================================================
# E_8 铝扣板 (md[8], p16, 2026-08-06 新接)
#   5+5 split + anchor n=10 / inherit n=8 (anchor name rowspan)
#   结构 (铝扣板 + 铝合金条扣板 两侧各 anchor + inherit):
#     Row 0: caption "铝扣板税前综合价格" n=1 → skip
#     Row 1: header n=11 (5+空+5)
#     Row 2: anchor n=10 [code1,name1,spec1,unit1,price1 | code2,name2,spec2,unit2,price2]
#     Row 3+: inherit n=8 [code1,spec1,unit1,price1 | code2,spec2,unit2,price2]
#
#   输出 (sheet「金属材料」10 列, 5 列 schema = excl 模式):
#     序号 = code
#     名称 = anchor.name (inherit inherit)
#     规格 = spec
#     单位 = m2 (clean_unit)
#     区间值/均值/含税 = 空
#     除税价 = price (单值, 5 列 schema)
#     执行标准/备注 = 空
# ============================================================
# md[10] 玻璃及玻璃制品 2026-08-07 加 n=9 mixed (5+4 / 4+5) 支持:
#   - n=10: 5+5 (左 anchor + 右 anchor)
#   - n=9: (5+4) 左 anchor + 右 inherit / (4+5) 左 inherit + 右 anchor
#   - n=8: 4+4 (左 inherit + 右 inherit)
#   - n=9 placeholder: (4+5) 左 inherit + 右全「—」占位 → 右半 skip
#   检测: idx 1 含中文+无 digits/+ → name (anchor); 否则 spec (inherit)
_NAME_LIKE_RE = re.compile(r"^[一-鿿][^0-9+]*$")  # 中文字符开头, 无数字/+


def _is_name_like(s):
    """判断 cell 是否为 name (anchor) 而非 spec (inherit) — 简单启发式
    用于 n=9 mixed split 决策
    """
    s = s.strip()
    if not s:
        return False
    return bool(_NAME_LIKE_RE.match(s))


def _process_table_dropdown(html):
    """铝扣板 (md[8]) 5+5 split + anchor/inherit → list[dict]

    2026-08-07 扩展: 支持 md[10] 玻璃及玻璃制品 n=9 mixed split
      - n=10: 5+5 (both anchor)
      - n=9: 5+4 / 4+5 mixed (name 决定 split 点)
      - n=8: 4+4 (both inherit)
      - n=9 placeholder: 4+5 with 右全「—」 → 右半 skip
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0
    n_placeholder_skip = 0

    # 跳 caption + header
    data_rows_raw = []
    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)
        if n == 1:  # caption "铝扣板税前综合价格"
            n_chapter_skip += 1
            continue
        if n >= 10 and row[0] == "材料编码":
            n_header_skip += 1
            continue
        data_rows_raw.append(row)

    # 手动 split + anchor/inherit 判定
    data_rows = []
    current_left_name = ""
    current_right_name = ""

    for row in data_rows_raw:
        n = len(row)
        if n == 10:
            # anchor: 左5+右5
            left = row[:5]
            right = row[5:10]
            current_left_name = left[1].strip()  # idx 1 = name
            current_right_name = right[1].strip()
            half_rows = [
                (left, current_left_name),
                (right, current_right_name),
            ]
        elif n == 8:
            # inherit: 左4+右4 (无 name, 插 "" 在 idx 1)
            left = [row[0], "", row[1], row[2], row[3]]
            right = [row[4], "", row[5], row[6], row[7]]
            half_rows = [
                (left, current_left_name),
                (right, current_right_name),
            ]
        elif n == 9:
            # 2026-08-07: n=9 mixed split (5+4 / 4+5)
            # 检测: idx 1 是否是 name (5+4) 或 idx 4 是否是 name (4+5)
            left_idx1_is_name = _is_name_like(row[1])
            right_idx5_is_name = len(row) > 5 and _is_name_like(row[5])
            if left_idx1_is_name:
                # 5+4: 左 anchor + 右 inherit
                left = row[:5]
                right = [row[5], "", row[6], row[7], row[8]]
                current_left_name = left[1].strip()
                # right 沿用 current_right_name
                half_rows = [
                    (left, current_left_name),
                    (right, current_right_name),
                ]
            elif right_idx5_is_name:
                # 4+5: 左 inherit + 右 anchor
                left = [row[0], "", row[1], row[2], row[3]]
                right = row[4:9]
                current_right_name = right[1].strip()
                # left 沿用 current_left_name
                half_rows = [
                    (left, current_left_name),
                    (right, current_right_name),
                ]
            else:
                # 4+5 placeholder: 左 inherit + 右全「—」 → 右半 skip
                n_placeholder_skip += 1
                left = [row[0], "", row[1], row[2], row[3]]
                price = _clean_price(row[3])
                if not price:
                    continue
                data_rows.append({
                    "name": current_left_name,
                    "spec": row[1].strip(),
                    "unit": clean_unit(row[2]),
                    "interval_value": "",
                    "mean_value": "",
                    "incl": "",
                    "excl": price,
                    "execution_standard": "",
                    "remark": "",
                })
                continue
        else:
            n_clean_skip += 1
            continue

        for half, anchor_name in half_rows:
            if len(half) != 5:
                continue
            price = _clean_price(half[4])
            if not price:
                n_clean_skip += 1
                continue
            data_rows.append({
                "name": anchor_name,
                "spec": half[2].strip(),
                "unit": clean_unit(half[3]),
                "interval_value": "",
                "mean_value": "",
                "incl": "",
                "excl": price,
                "execution_standard": "",
                "remark": "",
            })

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
        "placeholder_skip": n_placeholder_skip,
    }


# ============================================================
# E_9 钢管/管件 (md[11-14], 2026-08-07 新接, 4 表合并入 sheet「钢管」)
#   模式 1: md[11] 镀锌钢塑复合管 (per-row name, mixed anchor n=15 + inherit n=12)
#     row[0]   n=2  chapter "八、管材、管件及管道用器材税前综合价格" → skip
#     row[1]   n=2  sub-caption "镀锌钢塑复合管税前综合价格" → skip (name 来自 anchor)
#     row[2]   n=13 header (材料编码|材料名称|规格|壁厚(mm)|单位|税前综合价格) × 2 → skip
#     row[3]   n=4  sub-header (DN|英寸|DN|英寸) → skip
#     row[4]   n=15 anchor: left 7 + mid '' + right 7 (含 name 在 idx 1/8)
#     row[5-]  n=12 inherit: left 6 + right 6 (无 name, 继承 anchor)
#   模式 2: md[12-14] 镀锌钢管(水煤气管) (caption name, n=13/n=12 混合)
#     row[0]   n=2  caption "镀锌钢管(水煤气管)税前综合价格(1/2/3)" → 抽 name = "镀锌钢管(水煤气管)"
#     row[1]   n=13 header → skip
#     row[2]   n=13 anchor (有时, md[14] 没有) + row[3+] n=12 inherit
#
#   R7 最小改动: 复用 _clean_price / clean_unit (不复制 dropdown 代码)
#   模式判定: 第一个数据行 n=15 → 模式 1 (per-row name); n=13/n=12 → 模式 2 (caption name)
#   状态机: is_mixed_name (None/True/False) 决定 inherit 行 name 来源
#
#   预期行数:
#     md[11]: 12 data rows (1 anchor + 11 inherit) → 24 outputs
#     md[12]: 22 data rows (1 anchor + 21 inherit) → 44 outputs
#     md[13]: 22 data rows (2 anchor + 20 inherit) → 44 outputs
#     md[14]: 19 data rows (0 anchor + 19 inherit) → 38 outputs
#     合计: 150 rows
# ============================================================
def _process_table_pipe(html):
    """管材类 (md[11-14]) → list[dict] (统一进 sheet「钢管」6 列)

    字段: name | spec (DN) | wall_thickness | unit | price (5 字段)
    模式:
      1 (md[11]): 名称 per-row anchor, inherit 沿用 current_left/right_name
      2 (md[12-14]): 名称 = caption 抽 "镀锌钢管(水煤气管)"

    返回 (data_rows, stats) 跟其它 extractor 一致格式
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0

    data_rows = []
    current_left_name = ""    # 模式 1 state
    current_right_name = ""   # 模式 1 state
    current_table_name = ""   # 模式 2 state (从 caption 抽)
    is_mixed_name = None      # None=未确定, True=模式 1 (n=15), False=模式 2 (n=13/12)

    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)

        # n=2 caption: 章节/子段标题 → skip, 但抽 table_name (模式 2 用)
        if n == 2:
            cap_cell = row[0].strip()
            # 抽 table_name: 子段标题 (排除章节 "八、..." / "九、...")
            if cap_cell and "税前综合价格" in cap_cell and not re.match(r"^[一二三四五六七八九十]+、", cap_cell):
                name = re.sub(r"税前综合价格.*$", "", cap_cell).strip()
                if name:
                    current_table_name = name
            n_chapter_skip += 1
            continue

        # n=4 sub-header (DN|英寸|DN|英寸) → skip
        if n == 4:
            n_sub_header_skip += 1
            continue

        # n=11/n=13 header: row[0]="材料编码" → skip
        if n in (11, 13) and row[0] == "材料编码":
            n_header_skip += 1
            continue

        # 数据行处理
        if n == 15:
            # 模式 1 anchor: 7 + 1 + 7
            # left = idx 0-6 (code, name, DN, 英寸, 壁厚, 单位, 价格)
            # mid  = idx 7 ('' separator)
            # right = idx 8-14 (code, name, DN, 英寸, 壁厚, 单位, 价格)
            is_mixed_name = True
            current_left_name = row[1].strip()    # idx 1 = left name
            current_right_name = row[9].strip()   # idx 9 = right name (right half 的 idx 1)
            half_rows = [
                (row[:7], current_left_name),
                (row[8:15], current_right_name),
            ]
        elif n == 13 and row[0].isdigit() and len(row[0]) >= 15:
            # 模式 2 anchor: 6 + 1 + 6 (row[0] 是 18 位编码, 区分 header)
            is_mixed_name = False
            half_rows = [
                (row[:6], current_table_name),
                (row[7:13], current_table_name),
            ]
        elif n == 12:
            # inherit: 6 + 6
            if is_mixed_name is True:
                half_rows = [
                    (row[:6], current_left_name),
                    (row[6:12], current_right_name),
                ]
            else:
                # 模式 2 (或还未确定; 这种情况只在 md[14] 无 anchor 时)
                is_mixed_name = False
                half_rows = [
                    (row[:6], current_table_name),
                    (row[6:12], current_table_name),
                ]
        else:
            n_chapter_skip += 1
            continue

        for half, name in half_rows:
            if len(half) not in (6, 7):
                continue
            if len(half) == 7:
                # 模式 1 7-cell: code, name, DN, 英寸, 壁厚, 单位, 价格
                spec_dn = half[2]    # 2026-08-07 fix: 拆出英寸
                spec_inch_raw = half[3]
                wall = half[4]      # 壁厚 (mm)
                unit = half[5]
                price = half[6]
            else:
                # 6-cell (模式 2 + 模式 1 inherit): code, DN, 英寸, 壁厚, 单位, 价格
                spec_dn = half[1]    # 2026-08-07 fix: 拆出英寸
                spec_inch_raw = half[2]
                wall = half[3]      # 壁厚 (mm)
                unit = half[4]
                price = half[5]
            spec_dn = spec_dn.strip()
            spec_inch = _clean_inch(spec_inch_raw)   # 2026-08-07 加, 解 LaTeX+HTML
            wall = wall.strip()
            unit_clean = clean_unit(unit)
            price_clean = _clean_price(price)
            if not price_clean:
                n_clean_skip += 1
                continue
            data_rows.append({
                "name": name.strip(),
                "spec_dn": spec_dn,                  # 2026-08-07 fix: 从 spec 拆
                "spec_inch": spec_inch,              # 2026-08-07 fix: 新加
                "wall_thickness": wall,
                "unit": unit_clean,
                "price": price_clean,
                "spec": "",                          # 2026-08-07 fix: md[11-14] 无原始规格串 (DN/英寸已拆), 留空
            })

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


# ============================================================
# E_15: 不锈钢管/铸铁管/燃气焊管 (md[15], 2026-08-07 加)
#   md[15] 特殊结构 (与 md[11-14] 不同):
#     - 2 段 (不锈钢/铸铁 + 燃气焊管), 段间无独立 caption, 共用一个 caption "不锈钢管、铸铁管、燃气焊管"
#     - 段 1 header 用 "规格" 列名 (复合 DN壁厚 格式, e.g. "DN15壁厚(mm)0.8")
#     - 段 2 header 用 "规格(mm)" 列名 (燃气焊管, 简单 Φ159*6 格式)
#     - 数据行 n=10 (无 separator, rowspan 已消耗)
#     - 段 2 最后一行的右半可能是 "—" 占位符, 跳过
#   字段: name | spec_dn | spec_inch | wall_thickness | unit | price | spec (7 字段 + seq)
#   spec 解析 (regex):
#     "DN15壁厚(mm)0.8" → (DN=15, 壁厚=0.8), spec="DN15壁厚(mm)0.8" (保留原始)
#     "DN100"          → (DN=100, 壁厚=""), spec="DN100"
#     "Φ159*6"         → (DN="", 壁厚=6), spec="Φ159*6"
#   预期行数:
#     段 1 (stainless/cast): 13 data rows × 2 sides = 26 行
#     段 2 (gas): 4 data rows × 2 sides - 1 placeholder = 7 行
#     合计: 26 + 7 = 33 行
# ============================================================
_DN_WALL_RE = re.compile(r"^DN(\d+)壁厚\(mm\)(\d+\.?\d*)$")
_DN_SIMPLE_RE = re.compile(r"^DN(\d+)$")
_PHI_WALL_RE = re.compile(r"^Φ(\d+)\*(\d+\.?\d*)$")


def _parse_pipe_spec_full(spec_raw):
    """解析管材 spec → (dn, wall_thickness)

    "DN15壁厚(mm)0.8" → ("15", "0.8")
    "DN100"          → ("100", "")
    "Φ159*6"         → ("", "6")
    其他              → ("", "")
    """
    s = _decode_entities(spec_raw).strip()
    m = _DN_WALL_RE.match(s)
    if m:
        return m.group(1), m.group(2)
    m = _DN_SIMPLE_RE.match(s)
    if m:
        return m.group(1), ""
    m = _PHI_WALL_RE.match(s)
    if m:
        return "", m.group(2)
    return "", ""


def _process_table_15(html):
    """不锈钢管/铸铁管/燃气焊管 (md[15]) → list[dict] (统一进 sheet「钢管」8 列)

    字段: name | spec_dn | spec_inch | wall_thickness | unit | price | spec
    spec 解析:
      "DN15壁厚(mm)0.8" → DN=15, 壁厚=0.8
      "DN100"          → DN=100, 壁厚=""
      "Φ159*6"         → DN="", 壁厚=6

    返回 (data_rows, stats)
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0

    data_rows = []

    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)

        # n=1 caption/说明 → skip
        if n == 1:
            n_chapter_skip += 1
            continue

        # n=11 header (材料编码) → skip
        if n == 11 and row[0] == "材料编码":
            n_header_skip += 1
            continue

        # n=10 数据行: split 成 left 5 + right 5
        if n == 10:
            half_rows = [(row[:5], "left"), (row[5:10], "right")]
        else:
            n_chapter_skip += 1
            continue

        for half, side in half_rows:
            if len(half) != 5:
                continue
            code, name, spec_raw, unit, price = half
            # 占位符 "—" 跳过
            name_clean = name.strip()
            if not name_clean or name_clean == "—":
                n_clean_skip += 1
                continue
            spec_clean = _parse_pipe_spec(spec_raw)
            unit_clean = clean_unit(unit)
            price_clean = _clean_price(price)
            if not price_clean:
                n_clean_skip += 1
                continue
            data_rows.append({
                "name": name_clean,
                "spec_dn": "",                        # 2026-08-07 简化: 原文已含 DN, 不重复
                "spec_inch": "",                      # md[15] 没有英寸
                "wall_thickness": "",                 # 2026-08-07 简化: 原文已含壁厚, 不重复
                "unit": unit_clean,
                "price": price_clean,
                "spec": spec_clean,                   # 原始规格串 (e.g. "DN100壁厚(mm)2.0" / "Φ426*9")
            })

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


# ============================================================
# E_16: 电线电缆 (md[16-21], 2026-08-07 加)
#   6 张表 → sheet「电线电缆」6 列 schema:
#     [材料名称, 标称截面(mm²), 芯数, 单位, 税前综合价格(元), 备注]
#   模式 (5+5+1 split, 芯数从 R0 header 自动识别):
#     md[16] 12 列 = 5+1+5+1 (BV/BVV 电线, 中间 separator, 无线芯数)
#     md[17] 11 列 = 5+5+1 (单芯 vs 二芯电力电缆)
#     md[18] 11 列 = 5+5+1 (三芯 vs 四芯电力电缆)
#     md[19] 6 列  = 5+1   (五芯电力电缆, 单栏)
#     md[20] 11 列 = 5+5+1 (控制电缆含 N 线, 复合规格 $3×1.5+1×1$)
#     md[21] 11 列 = 5+5+1 (VV22 钢带铠装, 复合规格)
#   inherit 行 (无 name, 沿用 R1 anchor):
#     md[16] 9 列 = 4+1+4
#     md[17/18/20/21] 6 列 = 3+3
#     md[19] 4 列 = 4 (含 code 溯源)
#   备注: R1 右半/末列抓「加价说明」长串 → 每行都填 (用户原话"用到整个表")
#   R7: layout 从 header 列数 (12/11/6) 自动识别, 不传 raw_idx
# ============================================================
_CORE_RE = re.compile(r"[一二三四五六七八九]芯")    # 抓 header 里的「单芯/二芯/.../九芯」


def _cable_layout_from_n(n_anchor):
    """从 anchor 列数自动识别 layout (R7: 不依赖 raw_idx)

    返回 (n_inherit, remark_idx, has_sep, single_col, lhdr_off, rhdr_off)
    """
    if n_anchor == 12:
        # md[16]: 12 列 (5+1+5+1)
        return (9, 11, True, False, 4, 10)
    if n_anchor == 11:
        # md[17/18/20/21]: 11 列 (5+5+1)
        return (6, 10, False, False, 4, 9)
    if n_anchor == 6:
        # md[19]: 6 列 (5+1)
        return (4, 5, False, True, 4, None)
    return None


def _extract_core_from_header(s):
    """从 header cell 抓芯数 label: '单芯税前综合价格(元)' → '单芯', 其他 → '-'"""
    m = _CORE_RE.search(s)
    return m.group(0) if m else "-"


def _process_table_cable(html):
    """电线电缆 (md[16-21]) → list[dict] (统一进 sheet「电线电缆」6 列)

    字段: name | spec | core | unit | price | remark
    返回 (data_rows, stats)

    R7: layout 从 header 列数 (12/11/6) 自动识别, 芯数从 R0 header 自动识别, 不传 raw_idx
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_clean_skip = 0

    data_rows = []
    current_left_name = ""
    current_right_name = ""
    current_left_core = "-"
    current_right_core = "-"
    table_remark = ""
    has_anchor = False
    n_anchor = None
    n_inherit = remark_idx = lhdr_off = rhdr_off = None
    has_sep = single_col = False

    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)

        # header (R0): "材料编码" + 列数 ∈ {12, 11, 6} → 识别 layout + 抓芯数
        if row[0] == "材料编码" and n in (12, 11, 6) and n_anchor is None:
            n_anchor = n
            n_inherit, remark_idx, has_sep, single_col, lhdr_off, rhdr_off = _cable_layout_from_n(n)
            current_left_core = _extract_core_from_header(row[lhdr_off])
            if not single_col and rhdr_off is not None:
                current_right_core = _extract_core_from_header(row[rhdr_off])
            n_header_skip += 1
            continue

        # 等 header 出现后再处理
        if n_anchor is None:
            n_chapter_skip += 1
            continue

        # anchor 行: 列数 == n_anchor + 18 位材料编码
        is_anchor = (
            n == n_anchor
            and row[0]
            and row[0][0].isdigit()
            and len(row[0]) >= 15
        )
        # inherit 行: 列数 == n_inherit (无 code 3+3) 或 n_inherit+1 (左半含 18 位 code 4+3)
        # R7: 用 row[0] 是不是 18 位 code 区分 (md[17/18/21 大多 4+3, md[20] 全 3+3)
        is_inherit_code = (
            not is_anchor
            and n == n_inherit + 1
            and row[0]
            and row[0][0].isdigit()
            and len(row[0]) >= 15
        )
        is_inherit = (
            not is_anchor
            and (n == n_inherit or is_inherit_code)
        )

        if not (is_anchor or is_inherit):
            n_chapter_skip += 1
            continue

        if has_sep:
            left_half = row[:5]
            right_half = row[6:11]
        elif single_col:
            left_half = row[:5]
            right_half = None
        else:
            left_half = row[:5]
            # R7: 检测 row[5] 是不是 18 位 code (md[20] R1 缺右半 code, 起点直接是 name)
            # 注意: 产品名「0.6/1kV...」首字符 '0' 是数字, 必须整串是数字才认 code
            if is_anchor and row[5] and not (row[5].isdigit() and len(row[5]) >= 15):
                right_half = [None] + row[5:9]   # 补 None 给 code 占位
            else:
                right_half = row[5:10]

        # R7: inherit 行不切 (让下面的 is_inherit 分支统一按 6/7 列切)
        if is_inherit:
            left_half = None
            right_half = None

        remark_cell = row[remark_idx] if is_anchor and n > remark_idx else ""

        if is_anchor:
            current_left_name = _clean_name(left_half[1])
            if right_half is not None:
                current_right_name = _clean_name(right_half[1]) if right_half[1] else current_left_name
            else:
                current_right_name = current_left_name
            # 备注抓 R1 行最长 cell (md[17/18] idx=10, md[20/21] idx=9, md[16] idx=11; 备注都是 ≥100 字符长串)
            if not table_remark:
                for c in row:
                    cs = c.strip()
                    if len(cs) >= 100:
                        table_remark = _decode_entities(cs).strip()
                        break
                else:
                    # 兜底: 用 remark_idx
                    if remark_cell:
                        table_remark = _decode_entities(remark_cell).strip()
            has_anchor = True

        if not has_anchor:
            n_clean_skip += 1
            continue

        # inherit 行重切半 (half 应是 5 元素: [code/None, name/None, spec, unit, price])
        if is_inherit:
            if has_sep:
                # md[16] inherit: 9 列 = 4+1+4 (无 code) 或 10 列 = 5+1+4 (左有 name)
                if n == n_inherit + 1 and is_inherit_code:
                    # 10 列 = 5+1+4 (左 5 含 name)
                    left_half = row[:5]
                    right_half = [row[6], None] + row[7:10]
                else:
                    # 9 列 = 4+1+4
                    left_half = [row[0], None] + row[1:4]
                    right_half = [row[5], None] + row[6:9]
            elif single_col:
                # md[19/26.5 md[18] inherit: 4 列 = [code, spec, unit, price] (R1 后无 name inherit)
                # 2026-08-10 修 inherit half 错位: 原 [code, spec, unit, price, None] → emit half[2]=spec(=row[2]=unit), half[4]=price(=None)
                # 修: 头部补 None 给 name 占位, emit 取 half[1]=code, half[2]=spec, half[3]=unit, half[4]=price
                left_half = [None] + list(row)  # 5 元素: [None, code, spec, unit, price]
            else:
                # md[17/18/20/21] inherit: 6 列 = 3+3 (无 code) 或 7 列 = 4+3 (左有 code)
                if is_inherit_code:
                    # 7 列 = 4+3 (左 code+spec+unit+price), 补 None 给 name 列占位
                    left_half = [row[0], None] + row[1:4]   # 5 元素: [code, None, spec, unit, price]
                    right_half = [None, None] + row[4:7]    # 5 元素: [None, None, spec, unit, price]
                else:
                    # 6 列 = 3+3
                    left_half = [None, None] + row[:3]
                    right_half = [None, None] + row[3:6]

        halves_to_emit = []
        if single_col:
            halves_to_emit.append((left_half, current_left_name, current_left_core))
        else:
            halves_to_emit.append((left_half, current_left_name, current_left_core))
            halves_to_emit.append((right_half, current_right_name, current_right_core))

        for half, name, core in halves_to_emit:
            if len(half) < 5:
                continue
            spec_raw = half[2]
            unit_raw = half[3]
            price_raw = half[4]
            spec = _clean_spec(str(spec_raw)) if spec_raw else ""
            unit_clean = clean_unit(unit_raw)
            price_clean = _clean_price(price_raw)
            if not price_clean:
                n_clean_skip += 1
                continue
            data_rows.append({
                "name": name,
                "spec": spec,
                "core": core,
                "unit": unit_clean,
                "price": price_clean,
                "remark": table_remark,
            })

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "clean_skip": n_clean_skip,
    }


# ============================================================
# E_17: 镀锌桥架 (md[23-26], 2026-08-07 新接, 4 张表合并入 sheet「镀锌桥架」)
#   模式: 5+5 split + 表面积拆分 (单面|双面) + anchor/inherit + 备注整段复用
#     md[23] (1) R0 caption n=2 / R1 header n=15 / R2 sub-header n=4 (单面|双面|单面|双面)
#     md[24] (2) R0 caption n=1
#     md[25] (3) R0 caption n=1
#     md[26] (4) R0 直接 header n=15 (无 caption)
#     md[23-26] 共用 R1/R0 n=15 header: 材料编码|材料名称|规格(高×宽)|壁厚(mm)|单位|税前综合价格(元)|表面积(m²/m)[colspan=2]
#                                  |材料编码|材料名称|规格(高×宽)|壁厚(mm)|单位|税前综合价格(元)|表面积(m²/m)[colspan=2]|说明
#     R2 sub-header (n=4): 单面|双面|单面|双面 拆分 R1 表面积 colspan=2 列
#     R3/R2 anchor (n=17): idx 0-7 left + idx 8-15 right + idx 16 说明 (整段, 4 张表共用)
#     R4/R3+ inherit:
#       n=8  (md[23-26] 主):  4+4 split, 仅壁厚/单位/价格自带
#                          idx 0=code, 1=壁厚, 2=单位, 3=价格 | idx 4-7 right
#                          (无 spec/单面/双面 — 都继承 anchor)
#       n=14 (md[26] R5+):   7+7 split, 自带 spec/壁厚/单位/价格/单面/双面
#                          idx 0=code, 1=spec, 2=壁厚, 3=单位, 4=价格, 5=单面, 6=双面 | idx 7-13 right
#                          (名称继承 anchor, 其余自带)
#       n=13 (md[23-25] R+): 7+6 split (left 含 code, right 漏 code)
#                          idx 0=code, 1=spec, 2=壁厚, 3=单位, 4=价格, 5=单面, 6=双面 | idx 7-12 right
#                          (右半 6 cells: spec/壁厚/单位/价格/单面/双面, 无 code)
#   inherit 行字段来源 (R7):
#     inherit_8:  壁厚/单位/价格 自带, 名称/规格/单面/双面/备注 继承 anchor
#     inherit_13: 规格/壁厚/单位/价格/单面/双面 自带, 名称/备注 继承 anchor (7+6 split)
#     inherit_14: 规格/壁厚/单位/价格/单面/双面 自带, 名称/备注 继承 anchor (7+7 split)
#
#   预期行数:
#     md[23]: 1 anchor × 2 + 47 inherit × 2 = 96 行
#     md[24]: 96 行
#     md[25]: 96 行
#     md[26]: 1 anchor × 2 + 47 inherit × 2 (mix n=8/n=14) = 96 行
#     合计: 96 × 4 = 384 行
# ============================================================
def _process_table_tray(html):
    """镀锌桥架 (md[23-26]) → list[dict] (统一进 sheet「镀锌桥架」8 列)

    字段: name | spec | wall_thickness | unit | price | area_single | area_double | remark
    返回 (data_rows, stats) 跟其它 extractor 一致格式
    """
    rows_raw = _parse_html_table(html)
    n_header_skip = 0
    n_chapter_skip = 0
    n_sub_header_skip = 0
    n_clean_skip = 0
    data_rows = []

    # anchor state (anchor 自带所有字段, inherit 缺啥补啥)
    # spec 维护: anchor 自带, inherit_14 自带, inherit_8 继承
    state = {
        "L": {"name": "", "spec": "", "unit": "", "single": "", "double": ""},
        "R": {"name": "", "spec": "", "unit": "", "single": "", "double": ""},
    }
    table_remark = ""
    has_anchor = False

    for row in rows_raw:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = len(row)

        # n=1/n=2 caption (md[23] n=2 / md[24-25] n=1 / md[26] 无 caption)
        if n in (1, 2):
            n_chapter_skip += 1
            continue
        # n=15 header (R1/R0 含「材料编码」)
        if n == 15 and row[0] == "材料编码":
            n_header_skip += 1
            continue
        # n=4 sub-header (单面|双面|单面|双面)
        if n == 4:
            n_sub_header_skip += 1
            continue

        is_anchor = (n == 17 and row[0].isdigit() and len(row[0]) >= 15)
        is_inherit_8 = (n == 8 and row[0].isdigit() and len(row[0]) >= 15)
        is_inherit_13 = (n == 13 and row[0].isdigit() and len(row[0]) >= 15)
        is_inherit_14 = (n == 14 and row[0].isdigit() and len(row[0]) >= 15)
        # 2026-08-07 md[28] 不锈钢桥架(6) 新增 2 种 inherit 行变体:
        #   inherit_6 (n=6): 无 code 无 spec, 只有 wall/unit/price 各 3 (L+R)
        #     **row[0] 是 wall 不是 code** (如 '1.2'), 不能用 is_inherit_7 同款判别
        #     改判别: row[0] 含小数点 (wall 是 '1.0'/'1.2'/'2.5' 这种) 且长度 < 6
        #   inherit_7 (n=7): 1 code + L 3 (wall/unit/price) + R 3 (wall/unit/price)
        is_inherit_6 = (n == 6 and ('.' in row[0] or row[0].replace('.', '').isdigit()))
        is_inherit_7 = (n == 7 and row[0].isdigit() and len(row[0]) >= 15)

        if not (is_anchor or is_inherit_8 or is_inherit_13 or is_inherit_14 or is_inherit_6 or is_inherit_7):
            n_chapter_skip += 1
            continue

        # 字段映射 (按行类型分别处理)
        if is_anchor:
            # n=17 left: 0=code 1=name 2=spec 3=wall 4=unit 5=price 6=single 7=double
            # n=17 right: idx 8-15 (spec/wall/unit/price/single/double 在 idx 10-15 一致)
            # idx 16 = 说明
            # R 半 name/code 顺序有 2 种变体 (R7 修复):
            #   md[23-26] (1)-(4):  idx 8=code, 9=name
            #   md[27] (5) 铝合金:    idx 8=name, 9=code  (顺序相反)
            state["L"] = {
                "name": row[1].strip(),
                "spec": row[2].strip(),
                "unit": clean_unit(row[4]),
                "single": row[6].strip(),
                "double": row[7].strip(),
            }
            if row[8].isdigit() and len(row[8]) >= 15:
                # md[23-26] 模式: R 半 idx 8=code, 9=name
                state["R"] = {
                    "name": row[9].strip(),
                    "spec": row[10].strip(),
                    "unit": clean_unit(row[12]),
                    "single": row[14].strip(),
                    "double": row[15].strip(),
                }
            else:
                # md[27] 模式: R 半 idx 8=name, 9=code
                state["R"] = {
                    "name": row[8].strip(),
                    "spec": row[10].strip(),
                    "unit": clean_unit(row[12]),
                    "single": row[14].strip(),
                    "double": row[15].strip(),
                }
            table_remark = _decode_entities(row[16]).strip() if len(row) > 16 else ""
            has_anchor = True
            sides = [
                ("L", 0, state["L"]["name"], state["L"]["spec"], state["L"]["unit"], state["L"]["single"], state["L"]["double"]),
                ("R", 8, state["R"]["name"], state["R"]["spec"], state["R"]["unit"], state["R"]["single"], state["R"]["double"]),
            ]
            # anchor emit: wall=row[idx_base+3], price=row[idx_base+5]
            side_offsets = {"L": (3, 5), "R": (3, 5)}  # (wall_offset, price_offset)
        elif is_inherit_14:
            # n=14 left: 0=code 1=spec 2=wall 3=unit 4=price 5=single 6=double
            # n=14 right: idx 7-13
            # 自带 spec/unit/single/double, name 继承
            L_spec = row[1].strip()
            R_spec = row[8].strip()
            L_unit = clean_unit(row[3])
            R_unit = clean_unit(row[10])
            L_single = row[5].strip()
            L_double = row[6].strip()
            R_single = row[12].strip()
            R_double = row[13].strip()
            sides = [
                ("L", 0, state["L"]["name"], L_spec, L_unit, L_single, L_double),
                ("R", 7, state["R"]["name"], R_spec, R_unit, R_single, R_double),
            ]
            # inherit_14 emit: wall=row[idx_base+2], price=row[idx_base+4]
            side_offsets = {"L": (2, 4), "R": (2, 4)}
        elif is_inherit_13:
            # n=13 left: 0=code 1=spec 2=wall 3=unit 4=price 5=single 6=double (7 cells)
            # n=13 right: idx 7-12 (6 cells)
            #   **R 半 idx 7 有 2 种变体 (2026-08-07 md[28] 发现)**:
            #     - md[23-27]: idx 7 = code (18 位), R 半无 spec, 继承 anchor
            #     - md[28]:    idx 7 = spec (文本), R 半自带 spec
            #   区分: row[7].isdigit() and len(row[7]) >= 15 → 继承 anchor (md[23-27)
            #          否则 → 自带 spec (md[28])
            # **R half 偏移始终是 (1, 3) — 跟 spec 来源无关**, 因为 R half 永远是 6 cells:
            #   spec=idx_base+0 (=row[7] 自带 OR 继承 anchor)
            #   wall=idx_base+1=row[8], unit=idx_base+2=row[9]
            #   price=idx_base+3=row[10], single=idx_base+4=row[11], double=idx_base+5=row[12]
            L_spec = row[1].strip()
            L_unit = clean_unit(row[3])
            L_single = row[5].strip()
            L_double = row[6].strip()
            if row[7].isdigit() and len(row[7]) >= 15:
                # md[23-27] 模式: R 半 idx 7=code, R spec 继承 anchor
                R_spec_value = state["R"]["spec"]
                R_unit = clean_unit(row[9])
                R_single = row[11].strip()
                R_double = row[12].strip()
            else:
                # md[28] 模式: R 半 idx 7=spec (文本), R spec 自带
                R_spec_value = row[7].strip()
                R_unit = clean_unit(row[9])  # row[7+2]=row[9]
                R_single = row[11].strip()   # row[7+4]=row[11]
                R_double = row[12].strip()   # row[7+5]=row[12]
            sides = [
                ("L", 0, state["L"]["name"], L_spec, L_unit, L_single, L_double),
                ("R", 7, state["R"]["name"], R_spec_value, R_unit, R_single, R_double),
            ]
            # L: 7-cell (有 code), wall=idx_base+2, price=idx_base+4
            # R: 6-cell 偏移统一 (1, 3) — spec 来源决定 row[7] 是 code 还是 spec, 偏移不变
            side_offsets = {"L": (2, 4), "R": (1, 3)}
        elif is_inherit_7:
            # n=7: idx 0=code, idx 1-3 L (wall/unit/price), idx 4-6 R (wall/unit/price)
            #   无 spec/single/double, 全部继承 anchor
            L_unit = clean_unit(row[2])
            R_unit = clean_unit(row[5])
            sides = [
                ("L", 1, state["L"]["name"], state["L"]["spec"], L_unit, state["L"]["single"], state["L"]["double"]),
                ("R", 4, state["R"]["name"], state["R"]["spec"], R_unit, state["R"]["single"], state["R"]["double"]),
            ]
            # idx_base+0=wall, idx_base+2=price
            side_offsets = {"L": (0, 2), "R": (0, 2)}
        elif is_inherit_6:
            # n=6: idx 0-2 L (wall/unit/price), idx 3-5 R (wall/unit/price)
            #   无 code 无 spec/single/double, 全部继承 anchor
            L_unit = clean_unit(row[1])
            R_unit = clean_unit(row[4])
            sides = [
                ("L", 0, state["L"]["name"], state["L"]["spec"], L_unit, state["L"]["single"], state["L"]["double"]),
                ("R", 3, state["R"]["name"], state["R"]["spec"], R_unit, state["R"]["single"], state["R"]["double"]),
            ]
            side_offsets = {"L": (0, 2), "R": (0, 2)}
        else:
            # inherit_8: n=8 left [code, wall, unit, price], right idx 4-7
            # 无 spec, 继承 anchor 的 spec
            sides = [
                ("L", 0, state["L"]["name"], state["L"]["spec"], state["L"]["unit"], state["L"]["single"], state["L"]["double"]),
                ("R", 4, state["R"]["name"], state["R"]["spec"], state["R"]["unit"], state["R"]["single"], state["R"]["double"]),
            ]
            # inherit_8 emit: wall=row[idx_base+1], price=row[idx_base+3]
            side_offsets = {"L": (1, 3), "R": (1, 3)}

        if not has_anchor:
            n_clean_skip += 1
            continue

        for side, idx_base, name, spec, unit, single, double in sides:
            wall_off, price_off = side_offsets[side]
            wall = row[idx_base + wall_off].strip()
            price_clean = _clean_price(row[idx_base + price_off])
            if not price_clean:
                n_clean_skip += 1
                continue
            data_rows.append({
                "name": name,
                "spec": spec,
                "wall_thickness": wall,
                "unit": unit,
                "price": price_clean,
                "area_single": single,
                "area_double": double,
                "remark": table_remark,
            })

        # 关键修复 (2026-08-07 用户问「铝合金桥架有没有漏的」): inherit_14/13 emit 后必须更新 state[L/R]["spec"]
        # 否则后续 inherit_8 (无 spec 字段) 仍用 anchor spec, 错把后续产品的 spec 当成 anchor 的 spec
        # 例: md[27] R6 inherit_14 spec=30×60 → emit 后 state.L.spec 没更新 → R7 inherit_8 仍 spec=25×50 (anchor)
        # 修复: inherit_14/13 emit 后, state[L/R]["spec"] = 当前 emit 的 spec
        # 注: inherit_6/7/8 无自带 spec, 不更新 state (永远继承 anchor)
        # **v7 修复 (2026-08-08 用户问「表面积不对 200×1200 3.0 应是 2.86/5.72」)**:
        # inherit_14/13 emit 后还要更新 state[L/R]["single"] / state[L/R]["double"],
        # 否则后续 inherit_8 R half spec='200×1200' 但 area_single=1.46/area_double=2.92 (anchor 100×600 的值)
        if is_inherit_14 or is_inherit_13:
            state["L"]["spec"] = sides[0][3]
            state["R"]["spec"] = sides[1][3]
            state["L"]["single"] = sides[0][5]
            state["L"]["double"] = sides[0][6]
            state["R"]["single"] = sides[1][5]
            state["R"]["double"] = sides[1][6]

    return data_rows, {
        "header_skip": n_header_skip,
        "chapter_skip": n_chapter_skip,
        "sub_header_skip": n_sub_header_skip,
        "clean_skip": n_clean_skip,
    }


def _validate(sheets):
    """Step 1.6 验证: 对比实际 vs 预期行数 + 关键字段抽查

    返回 (ok: bool, msg: str)
    """
    actual = sum(len(rows) for rows in sheets.values())
    expected = _EXPECTED_T0_TOTAL
    if actual != expected:
        return False, f"[FAIL] table[0] 总行数 {actual} ≠ 预期 {expected}"
    # 抽查: sheet "金属材料" 应存在, 第一个 row 应是 row[2] 数据 (圆钢 Φ10 HPB300)
    if "金属材料" not in sheets:
        return False, "[FAIL] sheet '金属材料' 不存在"
    first = sheets["金属材料"][0]
    if first["name"] != "圆钢" or "HPB300" not in first["spec"]:
        return False, f"[FAIL] 第 1 行字段错: {first}"
    # 抽查: row[2] (6 列) 区间值 + 均值 都有值 (含税/除税 空)
    if not first["interval_value"] or not first["mean_value"]:
        return False, f"[FAIL] 第 1 行区间值/均值缺失: {first}"
    # 抽查: row[2] (6 列) 含税/除税 应该空 (避免重复)
    if first["incl"] or first["excl"]:
        return False, f"[FAIL] 第 1 行含税/除税应该空: {first}"
    # 抽查: 第 13 行 (left 半 row[14]) 应该是低松弛钢绞线 excl='4631.93', interval_value/mean_value 空
    if len(sheets["金属材料"]) < 13:
        return False, f"[FAIL] 行数不足 13, 实际 {len(sheets['金属材料'])}"
    thirteenth = sheets["金属材料"][12]
    if thirteenth.get("excl") != "4631.93" or thirteenth.get("interval_value") or thirteenth.get("mean_value"):
        return False, f"[FAIL] 第 13 行字段错 (应 excl='4631.93', interval_value/mean_value 空): {thirteenth}"
    return True, (
        f"[OK] table[0] → {expected} 行 (左 35 + 右 34, 1 占位符 '—' 被删), "
        f"第 1 行 = 圆钢 Φ10 HPB300 (区间={first['interval_value']} 均值={first['mean_value']} 含税/除税=空), "
        f"第 13 行 = 低松弛钢绞线 (除税={thirteenth.get('excl')} 区间/均值=空)"
    )


# ============================================================
# Step 2.1 验证标准: p7 table[1] 砌块/砂石/水泥数据行预期
#   - row[0] 表头 (n=11, idx 5 空) → skip (header_skip=1)
#   - row[1-9] n=11 (左 5 单价 + 右 6 双价)
#   - row[10-11] n=10 (左 5 + 右 5, 都只有单价)
#   - 11 行 × 2 半 = 22 行, 全部 4 价格任一有值, 0 占位符
# ============================================================
_EXPECTED_T1_TOTAL = 22


def _validate_step2_1(sheets):
    """Step 2.1 验证: 对比实际 vs 预期行数 + 关键字段抽查

    返回 (ok: bool, msg: str)
    """
    sheet_name = "砌块 砂石 水泥"
    actual = len(sheets.get(sheet_name, []))
    expected = _EXPECTED_T1_TOTAL
    if actual != expected:
        return False, f"[FAIL] table[1] sheet '{sheet_name}' 行数 {actual} ≠ 预期 {expected}"
    rows = sheets[sheet_name]
    # 抽查: 第 1 行 (left half row[1] = 普通混凝土空心砌块 390×190×190 千块 excl=2581.13)
    first = rows[0]
    if "砌块" not in first["name"] or "390×190×190" not in first["spec"]:
        return False, f"[FAIL] 第 1 行字段错 (应是 普通混凝土空心砌块 390×190×190): {first}"
    if first["excl"] != "2581.13":
        return False, f"[FAIL] 第 1 行 excl={first['excl']} ≠ 2581.93"
    # 抽查: 第 12 行 (right half row[1] = 中砂 细度模数3.0-2.3 m3 区间=176.74~202.14 均值=189.44)
    twelfth = rows[11]
    if "砂" not in twelfth["name"] or "3.0-2.3" not in twelfth["spec"]:
        return False, f"[FAIL] 第 12 行字段错 (应是 中砂 细度模数3.0-2.3): {twelfth}"
    if twelfth["interval_value"] != "176.74~202.14":
        return False, f"[FAIL] 第 12 行 interval_value={twelfth['interval_value']} ≠ 176.74~202.14"
    if twelfth["mean_value"] != "189.44":
        return False, f"[FAIL] 第 12 行 mean_value={twelfth['mean_value']} ≠ 189.44"
    # 抽查: 第 22 行 (right half row[11] = 毛石 综合 m3 excl=164.84) 单价行
    last = rows[-1]
    if last["name"] != "毛石" or last["excl"] != "164.84":
        return False, f"[FAIL] 第 22 行字段错 (应是 毛石 excl=164.84): {last}"
    return True, (
        f"[OK] table[1] → sheet '{sheet_name}' {expected} 行 (左 11 + 右 11, 0 占位符), "
        f"第 1 行 = 普通混凝土空心砌块 390×190×190 (除税=2581.13 区间/均值/含税=空), "
        f"第 12 行 = 中砂 细度模数3.0-2.3 (区间=176.74~202.14 均值=189.44 含税/除税=空), "
        f"第 22 行 = 毛石 综合 (除税=164.84 区间/均值/含税=空)"
    )


# ============================================================
# Step 2.2 验证标准: p8 table[2] PHC+PRC 嵌套表头数据行预期
#   - row[0] n=3 caption (idx 0 PHC, idx 2 PRC) → skip (chapter_skip=1)
#   - row[1] n=11 header → skip (header_skip=1)
#   - row[2] n=10 → 2 行 (PHC 自带名 + PRC 自带名)
#   - row[3-17] n=8 → 30 行 (PHC 15 + PRC 15, 名称 = caption + 型号规格)
#   - row[18-20] n=9 → 3 行 (左 5「—」删, 右 PRC 3)
#   - 预期 2 + 30 + 3 = 35 行
# ============================================================
_EXPECTED_T2_TOTAL = 35


def _validate_step2_2(sheets):
    """Step 2.2 验证: 对比实际 vs 预期行数 + 关键字段抽查

    返回 (ok: bool, msg: str)
    按 user「不管 sheet 名字」, PHC 进 sheet「金属材料」
    """
    sheet_name = "金属材料"
    actual = len(sheets.get(sheet_name, []))
    expected = _EXPECTED_T2_TOTAL
    if actual != expected:
        return False, f"[FAIL] table[2] sheet '{sheet_name}' 行数 {actual} ≠ 预期 {expected}"
    rows = sheets[sheet_name]
    # 抽查: 第 1 行 = row[2] PHC 自带名 = "预应力高强混凝土管桩(PHC)" + 规格 A型 Φ300×70 + 单价 71.82
    first = rows[0]
    if first["name"] != "预应力高强混凝土管桩(PHC)":
        return False, f"[FAIL] 第 1 行名称={first['name']!r} ≠ '预应力高强混凝土管桩(PHC)'"
    if "A型" not in first["spec"] or "Φ300×70" not in first["spec"]:
        return False, f"[FAIL] 第 1 行规格错 (应是 A型 Φ300×70): {first['spec']!r}"
    if first["excl"] != "71.82":
        return False, f"[FAIL] 第 1 行 excl={first['excl']} ≠ 71.82"
    # 抽查: 第 2 行 = row[2] PRC 自带名 = "混合配筋预应力混凝土管桩(PRC)-I型" + 规格 AB型Φ500×100 + 单价 194.91
    if len(rows) < 2:
        return False, f"[FAIL] 行数不足 2, 实际 {len(rows)}"
    second = rows[1]
    if second["name"] != "混合配筋预应力混凝土管桩(PRC)-I型":
        return False, f"[FAIL] 第 2 行名称={second['name']!r} ≠ '混合配筋预应力混凝土管桩(PRC)-I型'"
    if "AB型" not in second["spec"]:
        return False, f"[FAIL] 第 2 行规格错 (应是 AB型Φ500×100): {second['spec']!r}"
    if second["excl"] != "194.91":
        return False, f"[FAIL] 第 2 行 excl={second['excl']} ≠ 194.91"
    # 抽查: 第 3 行 = row[3] PHC 缺名称 → caption 补 = "预应力高强混凝土管桩(PHC)" (只全名, 不拼 A型)
    if len(rows) < 3:
        return False, f"[FAIL] 行数不足 3, 实际 {len(rows)}"
    third = rows[2]
    expected_name = "预应力高强混凝土管桩(PHC)"
    if third["name"] != expected_name:
        return False, f"[FAIL] 第 3 行名称={third['name']!r} ≠ {expected_name!r}"
    if "A型" not in third["spec"] or "Φ400×95" not in third["spec"]:
        return False, f"[FAIL] 第 3 行规格错 (应是 A型 Φ400×95): {third['spec']!r}"
    if third["excl"] != "105.98":
        return False, f"[FAIL] 第 3 行 excl={third['excl']} ≠ 105.98"
    # 抽查: 第 4 行 = row[3] PRC 缺名称 → caption 补 = "混合配筋预应力混凝土管桩(PRC)-I型" (只全名)
    if len(rows) < 4:
        return False, f"[FAIL] 行数不足 4, 实际 {len(rows)}"
    fourth = rows[3]
    expected_name_prc = "混合配筋预应力混凝土管桩(PRC)-I型"
    if fourth["name"] != expected_name_prc:
        return False, f"[FAIL] 第 4 行名称={fourth['name']!r} ≠ {expected_name_prc!r}"
    if "B型" not in fourth["spec"] or "Φ500×100" not in fourth["spec"]:
        return False, f"[FAIL] 第 4 行规格错 (应是 B型Φ500×100): {fourth['spec']!r}"
    if fourth["excl"] != "202.81":
        return False, f"[FAIL] 第 4 行 excl={fourth['excl']} ≠ 202.81"
    # 抽查: 第 35 行 (last) = row[20] PRC 缺名称 → excl=528.93
    last = rows[-1]
    if last["excl"] != "528.93":
        return False, f"[FAIL] 第 35 行 excl={last['excl']} ≠ 528.93"
    return True, (
        f"[OK] table[2] → sheet '{sheet_name}' +{expected} 行 (row[2] 2 + row[3-17] 30 + row[18-20] PRC 3, row[18-20] PHC「—」删), "
        f"第 1 行 = PHC A型 Φ300×70 (excl=71.82), "
        f"第 3 行 = PHC caption 补 A型 Φ400×95 (excl=105.98), "
        f"第 4 行 = PRC caption 补 B型Φ500×100 (excl=202.81), "
        f"第 35 行 = PRC caption 补 (excl=528.93)"
    )


# ============================================================
# Step 3 验证标准: p11 table[9] 铝单板 1 cell caption + 单列 5 列数据行预期
#   - row[0]   n=1  caption "铝单板税前综合价" → chapter_name 抽 "铝单板"
#   - row[1]   n=5  header → skip (header_skip=1)
#   - row[2+]  n=5  数据 (idx 0=编码, idx 1=名称自带, idx 2=规格, idx 3=单位, idx 4=单价)
#   - 预期 3 行 (氟碳喷涂铝单板 2.5mm + 铝单板 2mm + 铝单板 3mm)
# ============================================================
_EXPECTED_T9_TOTAL = 3


def _validate_step3(sheets):
    """Step 3 验证: 对比实际 vs 预期行数 + 关键字段抽查

    返回 (ok: bool, msg: str)
    按 user「水泥砌块能放 sheet1」, 铝单板进 sheet「金属材料」
    """
    sheet_name = "金属材料"
    actual = len(sheets.get(sheet_name, []))
    expected = _EXPECTED_T9_TOTAL
    if actual != expected:
        return False, f"[FAIL] table[9] sheet '{sheet_name}' 行数 {actual} ≠ 预期 {expected}"
    rows = sheets[sheet_name]
    # 抽查: 第 1 行 = 氟碳喷涂铝单板 2.5mm m2 excl=301.94
    first = rows[0]
    if "氟碳喷涂铝单板" not in first["name"]:
        return False, f"[FAIL] 第 1 行名称错 (应是 氟碳喷涂铝单板): {first['name']!r}"
    if "2.5mm" not in first["spec"]:
        return False, f"[FAIL] 第 1 行规格错 (应是 2.5mm): {first['spec']!r}"
    if first["excl"] != "301.94":
        return False, f"[FAIL] 第 1 行 excl={first['excl']} ≠ 301.94"
    if first["remark"] != "铝单板":
        return False, f"[FAIL] 第 1 行 remark={first['remark']!r} ≠ '铝单板'"
    # 抽查: 第 2 行 = 铝单板 2mm m2 excl=271.92
    second = rows[1]
    if "铝单板" not in second["name"] or second["name"] == "氟碳喷涂铝单板":
        return False, f"[FAIL] 第 2 行名称错 (应是 铝单板): {second['name']!r}"
    if "2mm" not in second["spec"]:
        return False, f"[FAIL] 第 2 行规格错 (应是 2mm): {second['spec']!r}"
    if second["excl"] != "271.92":
        return False, f"[FAIL] 第 2 行 excl={second['excl']} ≠ 271.92"
    # 抽查: 第 3 行 = 铝单板 3mm m2 excl=329.65
    third = rows[2]
    if "铝单板" not in third["name"]:
        return False, f"[FAIL] 第 3 行名称错 (应是 铝单板): {third['name']!r}"
    if "3mm" not in third["spec"]:
        return False, f"[FAIL] 第 3 行规格错 (应是 3mm): {third['spec']!r}"
    if third["excl"] != "329.65":
        return False, f"[FAIL] 第 3 行 excl={third['excl']} ≠ 329.65"
    return True, (
        f"[OK] table[9] → sheet '{sheet_name}' {expected} 行 (row[2-4] 3 行), "
        f"第 1 行 = 氟碳喷涂铝单板 2.5mm m2 (excl=301.94 remark=铝单板), "
        f"第 2 行 = 铝单板 2mm m2 (excl=271.92), "
        f"第 3 行 = 铝单板 3mm m2 (excl=329.65)"
    )


# ============================================================
# Step 4 验证标准: p9 table[3] 预拌混凝土继承式数据行预期
#   - row[0]  n=10 表头 → skip (header_skip=1)
#   - row[1]  n=4  sub-header 区间值/均值 → skip (sub_header_skip=1, C 类)
#   - row[2/12/21] n=12 自带名 anchor (3 个: 普通混凝土/防水混凝土P6~P8/防水混凝土P10~P12)
#   - row[3-29] n=10 缺名 → 继承上一 anchor
#   - split_2d_table 后 left 28 行 + right 28 行 = 56 行
# ============================================================
_EXPECTED_T3_TOTAL = 56


def _validate_step4(sheets):
    """Step 4 验证: 对比实际 vs 预期行数 + 关键 anchor 行字段抽查

    返回 (ok: bool, msg: str)
    按 user「不管 sheet 名字」, 进 sheet「金属材料」
    顺序: 「left 先 + right 后」(跟 Step 1.6 一致), left 28 + right 28 = 56 行
    """
    sheet_name = "金属材料"
    actual = len(sheets.get(sheet_name, []))
    expected = _EXPECTED_T3_TOTAL
    if actual != expected:
        return False, f"[FAIL] table[3] sheet '{sheet_name}' 行数 {actual} ≠ 预期 {expected}"
    rows = sheets[sheet_name]
    # 第 1 行 = left[0] 自带名 anchor = 普通混凝土 C15 m3 区间=321~370 均值=345.5
    first = rows[0]
    if first["name"] != "普通混凝土":
        return False, f"[FAIL] 第 1 行名称={first['name']!r} ≠ '普通混凝土'"
    if "C15" not in first["spec"]:
        return False, f"[FAIL] 第 1 行规格错 (应是 C15): {first['spec']!r}"
    if first["interval_value"] != "321~370":
        return False, f"[FAIL] 第 1 行区间值={first['interval_value']!r} ≠ '321~370'"
    if first["mean_value"] != "345.5":
        return False, f"[FAIL] 第 1 行均值={first['mean_value']!r} ≠ '345.5'"
    # 第 11 行 = left[10] 自带名 anchor = 防水混凝土P6~P8 C20 区间=342~393 均值=367.5
    if len(rows) < 11:
        return False, f"[FAIL] 行数不足 11, 实际 {len(rows)}"
    eleventh = rows[10]
    if eleventh["name"] != "防水混凝土P6~P8":
        return False, f"[FAIL] 第 11 行名称={eleventh['name']!r} ≠ '防水混凝土P6~P8'"
    if "C20" not in eleventh["spec"]:
        return False, f"[FAIL] 第 11 行规格错 (应是 C20): {eleventh['spec']!r}"
    if eleventh["interval_value"] != "342~393":
        return False, f"[FAIL] 第 11 行区间值={eleventh['interval_value']!r} ≠ '342~393'"
    if eleventh["mean_value"] != "367.5":
        return False, f"[FAIL] 第 11 行均值={eleventh['mean_value']!r} ≠ '367.5'"
    # 第 20 行 = left[19] 自带名 anchor = 防水混凝土P10~P12 C20 区间=349~399 均值=374
    if len(rows) < 20:
        return False, f"[FAIL] 行数不足 20, 实际 {len(rows)}"
    twentieth = rows[19]
    if twentieth["name"] != "防水混凝土P10~P12":
        return False, f"[FAIL] 第 20 行名称={twentieth['name']!r} ≠ '防水混凝土P10~P12'"
    if "C20" not in twentieth["spec"]:
        return False, f"[FAIL] 第 20 行规格错 (应是 C20): {twentieth['spec']!r}"
    if twentieth["interval_value"] != "349~399":
        return False, f"[FAIL] 第 20 行区间值={twentieth['interval_value']!r} ≠ '349~399'"
    if twentieth["mean_value"] != "374":
        return False, f"[FAIL] 第 20 行均值={twentieth['mean_value']!r} ≠ '374'"
    # 第 29 行 = right[0] 自带名 anchor = 普通泵送混凝土 C15 区间=329~378 均值=353.5
    if len(rows) < 29:
        return False, f"[FAIL] 行数不足 29, 实际 {len(rows)}"
    twenty_nineth = rows[28]
    if twenty_nineth["name"] != "普通泵送混凝土":
        return False, f"[FAIL] 第 29 行名称={twenty_nineth['name']!r} ≠ '普通泵送混凝土'"
    if twenty_nineth["interval_value"] != "329~378":
        return False, f"[FAIL] 第 29 行区间值={twenty_nineth['interval_value']!r} ≠ '329~378'"
    if twenty_nineth["mean_value"] != "353.5":
        return False, f"[FAIL] 第 29 行均值={twenty_nineth['mean_value']!r} ≠ '353.5'"
    # 第 56 行 (last) = right[27] 缺名继承 防水泵送混凝土P10~P12 C60 区间=479~533 均值=506
    last = rows[-1]
    if last["name"] != "防水泵送混凝土P10~P12":
        return False, f"[FAIL] 第 56 行名称={last['name']!r} ≠ '防水泵送混凝土P10~P12' (应继承)"
    if "C60" not in last["spec"]:
        return False, f"[FAIL] 第 56 行规格错 (应是 C60): {last['spec']!r}"
    if last["interval_value"] != "479~533":
        return False, f"[FAIL] 第 56 行区间值={last['interval_value']!r} ≠ '479~533'"
    if last["mean_value"] != "506":
        return False, f"[FAIL] 第 56 行均值={last['mean_value']!r} ≠ '506'"
    return True, (
        f"[OK] table[3] → sheet '{sheet_name}' {expected} 行 (left 28 + right 28, "
        f"3 个 anchor: 普通混凝土/防水混凝土P6~P8/防水混凝土P10~P12), "
        f"第 1 行 = 普通混凝土 C15 (区间=321~370 均值=345.5), "
        f"第 11 行 = 防水混凝土P6~P8 C20 (区间=342~393 均值=367.5), "
        f"第 20 行 = 防水混凝土P10~P12 C20 (区间=349~399 均值=374), "
        f"第 29 行 = 普通泵送混凝土 C15 (区间=329~378 均值=353.5), "
        f"第 56 行 = 防水泵送混凝土P10~P12 C60 (区间=479~533 均值=506, 继承)"
    )


# ============================================================
# Step 6 验证标准: table[4] row[17-24] 沥青混凝土 8 列拼接数据行预期
#   - row[17] n=1 章节标题「沥青混凝土税前综合价格」 → skip (chapter_skip=1)
#   - row[18] n=8 表头 → skip (header_skip=1)
#   - row[19-23] n=8 数据 (左 4 + 右 4) → 5 × 2 = 10 行
#   - row[24] n=5 数据 (左 4 数据 + 右 1 说明) → 1 行 (右半说明 skip)
#   - 预期 10 + 1 = 11 行
# ============================================================
_EXPECTED_T4_TOTAL = 39  # 2026-08-06: 水下 28 + 沥青 11 (修复前只 11 沥青, 水下被 'asphalt' 覆盖)


def _validate_step6(sheets, offset=0):
    """Step 6 验证: 对比实际 vs 预期行数 + 关键字段抽查

    2026-08-06 修复: table[4] 现在 = 水下段 28 行 (row[0-15] anchor+inherit) + 沥青段 11 行 (row[17-24])
    → 共 39 行. 顺序: 水下 left 先 + 水下 right 后 + 沥青 left 6 + 沥青 right 5 = 39

    offset (2026-08-06): 当 _process_table_4 在 full mode 下作为类型组跑时 (sheet 已有 E_4 idx=3 的 56 行在前),
    rows[28] 实际是 sheet 第 29 行 (offset=0) 还是 sheet 第 (28+56+1) = 85 行 (offset=56)?
    → 按设计: 单 idx 验证 offset=0 (rows 是 sheet 完整 rows); group 验证 offset=已累加行数 (rows 是该 group 输出)

    默认 offset=0 兼容 only_idx=4 调试模式 (单 idx 跑, sheet「金属材料」= table[4] 39 行)
    返回 (ok: bool, msg: str)
    按 user「不管 sheet 名字」, 进 sheet「金属材料」
    """
    sheet_name = "金属材料"
    actual = len(sheets.get(sheet_name, []))
    expected = _EXPECTED_T4_TOTAL
    if actual < offset + expected:
        return False, f"[FAIL] table[4] sheet '{sheet_name}' 行数 {actual} < offset+expected {offset + expected}"
    rows = sheets[sheet_name]
    # 第 (offset+1) 行 = 水下段 left[0] = 水下混凝土 C20 m3 区间=347~398 均值=372.5
    first = rows[offset]
    if first["name"] != "水下混凝土":
        return False, f"[FAIL] 第 {offset+1} 行名称={first['name']!r} ≠ '水下混凝土'"
    if "C20" not in first["spec"]:
        return False, f"[FAIL] 第 {offset+1} 行规格错 (应是 C20): {first['spec']!r}"
    if first["interval_value"] != "347~398":
        return False, f"[FAIL] 第 {offset+1} 行区间值={first['interval_value']!r} ≠ '347~398'"
    # 第 (offset+15) 行 = 水下段 left[14] = 水下防水混凝土P6~P8 C20 (继承 anchor 2)
    fifteenth = rows[offset + 14]
    if fifteenth["name"] != "水下防水混凝土P6~P8":
        return False, f"[FAIL] 第 {offset+15} 行名称={fifteenth['name']!r} ≠ '水下防水混凝土P6~P8' (应继承 anchor 2)"
    if "C20" not in fifteenth["spec"]:
        return False, f"[FAIL] 第 {offset+15} 行规格错 (应是 C20): {fifteenth['spec']!r}"
    # 第 (offset+29) 行 = 沥青段 left[0] = 粗粒式普通沥青砼AC花岗岩 m3 excl=1169.90
    twenty_nineth = rows[offset + 28]
    if "粗粒式普通沥青砼" not in twenty_nineth["name"]:
        return False, f"[FAIL] 第 {offset+29} 行名称错 (应是 粗粒式普通沥青砼AC花岗岩): {twenty_nineth['name']!r}"
    if twenty_nineth["excl"] != "1169.90":
        return False, f"[FAIL] 第 {offset+29} 行 excl={twenty_nineth['excl']} ≠ 1169.90"
    if twenty_nineth["remark"] != "":
        return False, f"[FAIL] 第 {offset+29} 行 remark={twenty_nineth['remark']!r} ≠ '' (应删臆填)"
    # 第 (offset+39) 行 (last) = 沥青段 right[4] = 细粒式沥青玛蹄脂碎石SMA-13辉绿岩 m3 excl=1576.03
    last = rows[offset + 38]
    if "SMA-13" not in last["name"] or "辉绿岩" not in last["name"]:
        return False, f"[FAIL] 第 {offset+39} 行名称错 (应是 细粒式沥青玛蹄脂碎石SMA-13辉绿岩): {last['name']!r}"
    if last["excl"] != "1576.03":
        return False, f"[FAIL] 第 {offset+39} 行 excl={last['excl']} ≠ 1576.03"
    return True, (
        f"[OK] table[4] → sheet '{sheet_name}' offset={offset} (水下 28 + 沥青 11), "
        f"第 {offset+1} 行 = 水下混凝土 C20 (区间=347~398), "
        f"第 {offset+15} 行 = 水下防水混凝土P6~P8 C20 (继承 anchor 2), "
        f"第 {offset+29} 行 = 粗粒式普通沥青砼AC花岗岩 m3 (excl=1169.90 remark=''), "
        f"第 {offset+39} 行 = 细粒式沥青玛蹄脂碎石SMA-13辉绿岩 m3 (excl=1576.03)"
    )


# ============================================================
# Step 8 验证标准: table[5] 干混/湿拌砂浆继承式 46 行预期
#   段 1 干混 (1) row[0-25]:  24 行 (9 anchor + 15 inherit)
#   段 2 干混 (2) row[26-39]: 3 行 (3 anchor 保温砂浆)
#   段 3 湿拌 row[40-68]:    19 行 (7 anchor + 12 inherit)
# ============================================================
_EXPECTED_T5_TOTAL = 46


def _validate_step8(sheets):
    """Step 8.1 验证 (11 列 schema): 性能/适用范围 inherit 共享 anchor

    返回 (ok: bool, msg: str)
    """
    sheet_name = "砂浆"
    actual = len(sheets.get(sheet_name, []))
    expected = _EXPECTED_T5_TOTAL
    if actual != expected:
        return False, f"[FAIL] table[5] sheet '{sheet_name}' 行数 {actual} ≠ 预期 {expected}"
    rows = sheets[sheet_name]

    # 第 1 行 = row[2] anchor: 普通干混砌筑砂浆 M5 t 区间=236~260 均值=248.0 适用范围=砌筑灰缝≥5mm
    first = rows[0]
    if first["name"] != "普通干混砌筑砂浆":
        return False, f"[FAIL] 第 1 行名称={first['name']!r} ≠ '普通干混砌筑砂浆'"
    if first["strength"] != "M5":
        return False, f"[FAIL] 第 1 行强度={first['strength']!r} ≠ 'M5'"
    if "保水率" not in (first.get("performance") or ""):
        return False, f"[FAIL] 第 1 行性能应含保水率: {first.get('performance')!r}"
    if first["unit"] != "t":
        return False, f"[FAIL] 第 1 行单位={first['unit']!r} ≠ t"
    if first["interval_value"] != "236~260":
        return False, f"[FAIL] 第 1 行区间值={first['interval_value']!r} ≠ '236~260'"
    if first["mean_value"] != "248.0":
        return False, f"[FAIL] 第 1 行均值={first['mean_value']!r} ≠ '248.0'"
    if "砌筑灰缝" not in first["scope"]:
        return False, f"[FAIL] 第 1 行适用范围应含 砌筑灰缝: {first['scope']!r}"
    if first["coeff"] != "1.60":
        return False, f"[FAIL] 第 1 行 t/m³系数={first['coeff']!r} ≠ '1.60'"

    # 第 2 行 = row[3] inherit: 名称/性能/适用范围 继承 anchor
    second = rows[1]
    if second["name"] != "普通干混砌筑砂浆":
        return False, f"[FAIL] 第 2 行名称={second['name']!r} (应继承)"
    if second["strength"] != "M7.5":
        return False, f"[FAIL] 第 2 行强度={second['strength']!r} ≠ 'M7.5'"
    if second.get("performance") != first["performance"]:
        return False, f"[FAIL] 第 2 行性能应继承 anchor: {second.get('performance')!r} ≠ {first['performance']!r}"
    if second["interval_value"] != "242~267":
        return False, f"[FAIL] 第 2 行区间值={second['interval_value']!r} ≠ '242~267'"
    if second["mean_value"] != "254.5":
        return False, f"[FAIL] 第 2 行均值={second['mean_value']!r} ≠ '254.5'"
    if second["scope"] != first["scope"]:
        return False, f"[FAIL] 第 2 行适用范围应继承 anchor: {second['scope']!r} ≠ {first['scope']!r}"
    if second["coeff"] != "1.60":
        return False, f"[FAIL] 第 2 行 t/m³系数={second['coeff']!r} ≠ '1.60'"

    # 第 6 行 = row[7] anchor: 薄层干混砌筑砂浆 M5 (idx 7 = '<5mm' 解码后)
    if len(rows) < 6:
        return False, f"[FAIL] 行数不足 6, 实际 {len(rows)}"
    sixth = rows[5]
    if sixth["name"] != "薄层干混砌筑砂浆":
        return False, f"[FAIL] 第 6 行名称={sixth['name']!r} ≠ '薄层干混砌筑砂浆'"
    if sixth["strength"] != "M5":
        return False, f"[FAIL] 第 6 行强度={sixth['strength']!r} ≠ 'M5'"
    if sixth.get("performance") != "保水率/% ≥99.0":
        return False, f"[FAIL] 第 6 行性能={sixth.get('performance')!r} ≠ '保水率/% ≥99.0'"
    if sixth["interval_value"] != "346~382":
        return False, f"[FAIL] 第 6 行区间值={sixth['interval_value']!r} ≠ '346~382'"
    if sixth["scope"] != "砌筑灰缝<5mm":
        return False, f"[FAIL] 第 6 行适用范围={sixth['scope']!r} ≠ '砌筑灰缝<5mm' (HTML 解码)"

    # 第 7 行 = row[8] inherit: 薄层干混砌筑砂浆 M10 (继承性能+scope)
    if len(rows) < 7:
        return False, f"[FAIL] 行数不足 7, 实际 {len(rows)}"
    seventh = rows[6]
    if seventh["name"] != "薄层干混砌筑砂浆":
        return False, f"[FAIL] 第 7 行名称={seventh['name']!r} (应继承)"
    if seventh["strength"] != "M10":
        return False, f"[FAIL] 第 7 行强度={seventh['strength']!r} ≠ 'M10'"
    if seventh.get("performance") != sixth.get("performance"):
        return False, f"[FAIL] 第 7 行性能应继承: {seventh.get('performance')!r}"
    if seventh["scope"] != "砌筑灰缝<5mm":
        return False, f"[FAIL] 第 7 行适用范围应继承: {seventh['scope']!r}"

    # 第 22 行 = row[23] inherit: 干混防水砂浆:P8 M20
    if len(rows) < 22:
        return False, f"[FAIL] 行数不足 22, 实际 {len(rows)}"
    twenty_second = rows[21]
    if twenty_second["name"] != "干混防水砂浆:P8":
        return False, f"[FAIL] 第 22 行名称={twenty_second['name']!r} ≠ '干混防水砂浆:P8'"
    if twenty_second["strength"] != "M20":
        return False, f"[FAIL] 第 22 行强度={twenty_second['strength']!r} ≠ 'M20'"
    if twenty_second["coeff"] != "1.55":
        return False, f"[FAIL] 第 22 行 t/m³系数={twenty_second['coeff']!r} ≠ '1.55'"

    # 第 23 行 = row[24] anchor: 干混防水砂浆:P10 (idx 7 OCR 漏 scope, 是 t/m³数字 1.55)
    # 2026-08-06 修复: 大类共享 scope (P6/P8/P10 同属 "防水砂浆") → P10 anchor scope = P6 的 scope
    twenty_third = rows[22]
    if twenty_third["name"] != "干混防水砂浆:P10":
        return False, f"[FAIL] 第 23 行名称={twenty_third['name']!r} ≠ '干混防水砂浆:P10'"
    if "用于有抗渗压力" not in twenty_third["scope"]:
        return False, f"[FAIL] 第 23 行 scope 应继承 P6 (大类共享): {twenty_third['scope']!r}"
    if twenty_third["coeff"] != "1.55":
        return False, f"[FAIL] 第 23 行 idx 7 漏 scope 当 t/m³ 填入: {twenty_third['coeff']!r} ≠ '1.55'"

    # 第 24 行 = row[25] inherit: 干混防水砂浆:P10 M20 (继承 anchor 性能+scope)
    twenty_fourth = rows[23]
    if twenty_fourth["name"] != "干混防水砂浆:P10":
        return False, f"[FAIL] 第 24 行名称={twenty_fourth['name']!r} ≠ '干混防水砂浆:P10'"
    if twenty_fourth["strength"] != "M20":
        return False, f"[FAIL] 第 24 行强度={twenty_fourth['strength']!r} ≠ 'M20'"
    if twenty_fourth.get("performance") != twenty_third.get("performance"):
        return False, f"[FAIL] 第 24 行性能应继承 anchor: {twenty_fourth.get('performance')!r}"
    # scope 继承自 anchor P10 → P6 大类共享 scope
    if "用于有抗渗压力" not in twenty_fourth["scope"]:
        return False, f"[FAIL] 第 24 行 scope 应继承 (大类共享): {twenty_fourth['scope']!r}"
    if twenty_fourth["coeff"] != "1.55":
        return False, f"[FAIL] 第 24 行 t/m³系数={twenty_fourth['coeff']!r} ≠ '1.55'"

    # 第 25 行 = row[30] anchor: 干混聚苯骨料保温砂浆一类 (段2 anchor 1/3)
    twenty_fifth = rows[24]
    if "聚苯骨料保温砂浆一类" not in twenty_fifth["name"]:
        return False, f"[FAIL] 第 25 行名称错: {twenty_fifth['name']!r}"
    if "B1" not in twenty_fifth["scope"]:
        return False, f"[FAIL] 第 25 行适用范围应含 B1级: {twenty_fifth['scope']!r}"

    # 第 28 行 = row[43] anchor: 湿拌砌筑砂浆 M5 m3 (湿拌表头没 t/m³系数, 段3 anchor 1/7)
    if len(rows) < 28:
        return False, f"[FAIL] 行数不足 28, 实际 {len(rows)}"
    twenty_eighth = rows[27]
    if twenty_eighth["name"] != "湿拌砌筑砂浆":
        return False, f"[FAIL] 第 28 行名称={twenty_eighth['name']!r} ≠ '湿拌砌筑砂浆'"
    if twenty_eighth["unit"] != "m3":
        return False, f"[FAIL] 第 28 行单位={twenty_eighth['unit']!r} ≠ m3"
    if twenty_eighth["interval_value"] != "296~330":
        return False, f"[FAIL] 第 28 行区间值={twenty_eighth['interval_value']!r} ≠ '296~330'"
    if twenty_eighth["mean_value"] != "313.0":
        return False, f"[FAIL] 第 28 行均值={twenty_eighth['mean_value']!r} ≠ '313.0'"
    if "砌筑灰缝" not in twenty_eighth["scope"]:
        return False, f"[FAIL] 第 28 行适用范围应含 砌筑灰缝: {twenty_eighth['scope']!r}"
    if twenty_eighth["coeff"] != "":
        return False, f"[FAIL] 第 28 行湿拌 anchor t/m³系数应空: {twenty_eighth['coeff']!r}"

    # 第 29 行 = row[44] inherit: 湿拌砌筑砂浆 M7.5 (湿拌 inherit n=5, 没 t/m³系数)
    twenty_ninth = rows[28]
    if twenty_ninth["name"] != "湿拌砌筑砂浆":
        return False, f"[FAIL] 第 29 行名称={twenty_ninth['name']!r} (应继承)"
    if twenty_ninth["strength"] != "M7.5":
        return False, f"[FAIL] 第 29 行强度={twenty_ninth['strength']!r} ≠ 'M7.5'"
    if twenty_ninth["coeff"] != "":
        return False, f"[FAIL] 第 29 行湿拌 inherit t/m³系数应空: {twenty_ninth['coeff']!r}"

    # 第 46 行 (last) = row[61] inherit: 湿拌防水砂浆:P10 M20
    last = rows[-1]
    if last["name"] != "湿拌防水砂浆:P10":
        return False, f"[FAIL] 第 46 行名称={last['name']!r} ≠ '湿拌防水砂浆:P10'"
    if last["strength"] != "M20":
        return False, f"[FAIL] 第 46 行强度={last['strength']!r} ≠ 'M20'"
    if last["interval_value"] != "358~393":
        return False, f"[FAIL] 第 46 行区间值={last['interval_value']!r} ≠ '358~393'"
    if last["mean_value"] != "375.5":
        return False, f"[FAIL] 第 46 行均值={last['mean_value']!r} ≠ '375.5'"

    return True, (
        f"[OK] table[5] → sheet '{sheet_name}' {expected} 行 (11 列 schema), "
        f"第 1 行 = 普通干混砌筑砂浆 M5 t (区间=236~260 均值=248.0 适用范围=砌筑灰缝≥5mm t/m³=1.60), "
        f"第 2 行 = 普通干混砌筑砂浆 M7.5 (继承 性能+scope), "
        f"第 6 行 = 薄层干混砌筑砂浆 M5 (anchor 性能='保水率/% ≥99.0'), "
        f"第 7 行 = 薄层干混砌筑砂浆 M10 (继承), "
        f"第 23 行 = 干混防水砂浆:P10 (OCR 漏 scope, t/m³=1.55), "
        f"第 24 行 = 干混防水砂浆:P10 M20 (继承 anchor scope=空), "
        f"第 25 行 = 干混聚苯骨料保温砂浆一类 (anchor, B1级), "
        f"第 28 行 = 湿拌砌筑砂浆 M5 m3 (区间=296~330 均值=313.0 t/m³=空), "
        f"第 46 行 = 湿拌防水砂浆:P10 M20 (继承, 区间=358~393 均值=375.5)"
    )


# ============================================================
# 验证 dispatch (Step 2.1 + Step 2.2 + Step 3 + Step 4 + Step 6 + Step 8: 按 only_idx 选对应验证函数)
# ============================================================
_VALIDATORS = {
    0: _validate,
    1: _validate_step2_1,
    2: _validate_step2_2,
    3: _validate_step4,
    4: _validate_step6,
    5: _validate_step8,
    9: _validate_step3,
}


# ============================================================
# 主入口
# ============================================================
def _fallback_extractor(raw_idx):
    """caption dispatch 未命中 → raw_idx fallback (2026-08-06 抽)
    返回 (extractor_name, sheet_name) 元组
    """
    if raw_idx == 2:
        return "_process_phc_table", "金属材料"
    if raw_idx in (3, 4):
        # table[3] 预拌混凝土 + table[4] 水下混凝土段 (row[0-15]) 都走 E_4
        return "_process_table_4", "金属材料"
    if raw_idx == 5:
        # Step 8: 干混+湿拌砂浆继承式 (3 段共用 E_6)
        return "_process_table_6", "砂浆"
    if raw_idx == 6:
        # 阶段2 (2026-08-06): md[6] 建筑装配式构件 (caption 不在 table HTML 内, raw_idx fallback)
        # 2026-08-06 user 拍板: 独立 sheet「建筑装配式构件」(6 列 schema, 跟金属材料分离)
        return "_process_table_8", "建筑装配式构件"
    if raw_idx == 7:
        # 阶段2 (2026-08-06): md[7] 蒸压加气混凝土板
        return "_process_table_7", "金属材料"
    if raw_idx == 8:
        # 阶段2 (2026-08-06): md[8] 铝扣板 5+5 split + anchor/inherit
        return "_process_table_dropdown", "金属材料"
    if raw_idx == 10:
        # 2026-08-07: md[10] 玻璃及玻璃制品 5+5 + 5+4 / 4+5 mixed (复用 _process_table_dropdown)
        return "_process_table_dropdown", "金属材料"
    if raw_idx == 9:
        # 阶段2 (2026-08-06): md[9] 铝单板 (单列 caption, 已用 caption dispatch; raw_idx 兜底)
        return "_process_table_3", "金属材料"
    if raw_idx in (11, 12, 13, 14):
        # 2026-08-07: md[11-14] 管材类 4 表合并入 sheet「钢管」(共用 _process_table_pipe)
        return "_process_table_pipe", "钢管"
    if raw_idx == 15:
        # 2026-08-07: md[15] 不锈钢管/铸铁管/燃气焊管 (2 段 + 占位符 skip, 共 ~33 行)
        return "_process_table_15", "钢管"
    if raw_idx in (16, 17, 18, 19, 20, 21):
        # 2026-08-07: md[16-21] 电线电缆 6 张表合并入 sheet「电线电缆」(6 列 schema, 含芯数+备注)
        return "_process_table_cable", "电线电缆"
    if raw_idx in (23, 24, 25, 26, 27, 28):
        # 2026-08-07: md[23-26] 镀锌桥架(1)-(4) + md[27] 铝合金桥架(5) + md[28] 不锈钢桥架(6)
        #   合并入 sheet「镀锌桥架」(8 列 schema, 含表面积单/双面+备注)
        #   md[27] R3 anchor R 半结构与 md[23-26] 相反 (idx 8=name, 9=code), 自动检测
        #   md[28] inherit 行变体最多: inherit_6 / inherit_7 / inherit_13 (R 半 spec 自带 vs 继承 anchor)
        return "_process_table_tray", "镀锌桥架"
    if raw_idx == 29:
        # 2026-08-08: md[29] 密集型铜导体母线槽(IP54)税前综合价格(1) — PHC 风格表头嵌套
        #   row[2] n=10 自带电流等级 (split=5) → emit L+R 各 1 行, 更新 state
        #   row[3-19] n=8 缺电流等级 (split=4) → 继承 state 电流等级
        #   row[20] n=10 切换新电流等级 (250A/630A) → emit + 更新 state
        #   row[21-37] n=8 缺等级 (继承 row[20])
        #   共 72 行 (18 产品 × 4 电流等级, L+R 各 36 行), 放 sheet1 金属材料
        return "_process_table_busway", "金属材料"
    if raw_idx == 30:
        # 2026-08-08: md[30] p37 母线槽(2) 800A/1250A — 跟 md[29] 同 PHC 模式, 但数据行多 1 空白列
        #   header n=11 (含空白列 idx 5), 锚行 n=11 (L:5 + BLANK + R:5), 数据行 n=9 (L:4 + BLANK + R:4)
        #   skip_col=5 → _process_table_busway 处理空白分隔列
        #   共 36 行 (18 产品 × 2 电流等级, L+R 各 18 行), 放 sheet1 金属材料
        return "_process_table_busway", "金属材料"
    if raw_idx == 31:
        # 2026-08-08: md[31] p38 母线槽(3) 2000A/3200A — 跟 md[29] 完全一致 (数据行 n=8, 无空白列)
        #   header n=11 但数据行 n=8 → 直接复用 _process_table_busway 不传 skip_col
        #   共 36 行 (18 产品 × 2 电流等级, L+R 各 18 行), 放 sheet1 金属材料
        return "_process_table_busway", "金属材料"
    if raw_idx == 32:
        # 2026-08-08: md[32] p39 母线槽(4) 5000A 单栏 — 跟 md[29]/md[30]/md[31] 不同
        #   row[0] n=5 header → skip
        #   row[1] n=5 锚行 (含电流等级 5000A) → emit 1 行
        #   row[2-18] n=4 数据行 (缺电流等级) → 继承 state
        #   row[19] n=1 说明文字 → 挂第一行 remark
        #   共 18 行 (18 产品 × 1 电流等级), 放 sheet1 金属材料
        return "_process_table_busway_single", "金属材料"
    # 通用 split_2d_table 流程
    return "_process_table", "金属材料"


# 2026-08-08: md[N] → _process_table_busway 额外 kwargs (None=旧路径默认无参数)
_BUSWAY_KWARGS = {
    30: {"skip_col": 5},   # md[30] p37 母线槽(2) 含空白分隔列 (800A/1250A + 1000A/1600A)
    31: {"skip_col": 5},   # md[31] p38 母线槽(3) 含空白分隔列 (2000A/3200A + 2500A/4000A)
}


def _dispatch_one(html, raw_idx, caption_dispatch):
    """单 table dispatch → (rows, stats, sheet_name, dispatch_key, extractor_name)
    复用 _extract_single_idx 和 _extract_by_type_group 都需要
    """
    rows_raw = _parse_html_table(html)
    dispatch_key = _find_caption_extractor(rows_raw, caption_dispatch)
    if dispatch_key != "default":
        cfg = caption_dispatch.get(dispatch_key, caption_dispatch["default"])
        extractor_name = cfg["extractor"]
        sheet_name = cfg.get("sheet", "金属材料")
        kwargs = cfg.get("kwargs", {})
    else:
        extractor_name, sheet_name = _fallback_extractor(raw_idx)
        # 2026-08-08: md[30] p37 母线槽(2) 需传 skip_col=5 (含空白分隔列)
        kwargs = _BUSWAY_KWARGS.get(raw_idx, {})
    rows, stats = globals()[extractor_name](html, **kwargs) if kwargs else globals()[extractor_name](html)
    return rows, stats, sheet_name, dispatch_key, extractor_name


def extract(result_json, only_idx=None, log_lines=None):
    """主入口: result.json → extract.json
    2026-08-06 重构: 拆 _extract_single_idx (--only=N 调试) + _extract_by_type_group (full mode 按类型分组)
    """
    result_path = Path(result_json)
    out_path = result_path.parent / "extract.json"
    print(f"[step3 广州] 输入: {result_path}")
    print(f"[step3 广州] 输出: {out_path}")
    if only_idx is not None:
        print(f"[step3 广州] --only={only_idx} 调试模式, 只处理 table[{only_idx}]")

    with result_path.open(encoding="utf-8") as f:
        data = json.load(f)
    md_content = data["results"]["upload"]["md_content"]

    tables_html = re.findall(r"<table>.*?</table>", md_content, re.DOTALL)
    print(f"[step3 广州] 找到 {len(tables_html)} 个 HTML <table>")

    # 阶段 0 (2026-08-06): parse_page_map 诊断 - 抽 pdf_info 拿每 table 物理页
    # (2026-08-06 v2): 改用新 API (含 md_content) 让 log 显示跨页 range (e.g. t[5]=p11-13)
    # 注: 下游 line 2248 用 page_map[i] + 1 假设 list[int], 所以保留 legacy page_map 不动;
    # 新 API 结果只用于 log 显示, 不影响下游.
    page_map = _parse_page_map(data["results"]["upload"]["middle_json"])
    page_map_display = _parse_page_map(
        data["results"]["upload"]["middle_json"],
        md_content,
        len(tables_html),
    )
    n_show = min(len(page_map_display), len(tables_html))
    if n_show > 0:
        # 新 API 返回 list[str] ("p11" / "p11-13"), 直接打印
        page_log = ", ".join(f"t[{i}]={p}" for i, p in enumerate(page_map_display[:n_show]))
        print(f"[step3 广州] 物理页映射 ({n_show}/{len(tables_html)} table): {page_log}")
        if len(page_map_display) > len(tables_html):
            print(f"[step3 广州] ⚠️ pdf_info 多 {len(page_map_display)-len(tables_html)} 个 table block (md_content regex 漏)")
        elif len(page_map_display) < len(tables_html):
            print(f"[step3 广州] ⚠️ md_content 多 {len(tables_html)-len(page_map_display)} 个 <table> (pdf_info 漏)")
    else:
        print(f"[step3 广州] ⚠️ page_map 为空或跟 tables_html 数不匹配")

    # Step 7: 加载 caption_dispatch.json (按武汉 caption_dispatch 机制)
    caption_dispatch = load_config("caption_dispatch")

    # 共享常量
    SKIP_DEFAULT = set(range(35)) - {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32}  # 2026-08-08 加 29/30/31/32=密集型铜导体母线槽(IP54)(1)(2)(3)(4)

    if only_idx is not None:
        # 改 4 (2026-08-06): --only=N 调试模式, 跳过分组, 走单 idx 流程
        return _extract_single_idx(
            only_idx, tables_html, page_map, caption_dispatch, out_path, log_lines
        )

    # 改 2 (2026-08-06): full mode 按类型分组跑
    return _extract_by_type_group(
        tables_html, page_map, page_map_display, caption_dispatch, SKIP_DEFAULT, out_path, log_lines
    )


def _extract_single_idx(only_idx, tables_html, page_map, caption_dispatch, out_path, log_lines):
    """--only=N 调试模式: 单 idx 跑, 保留原语义 (改 4 抽)"""
    tables_html_one = [tables_html[only_idx]]
    sheets = {name: [] for name in _TARGET_SHEETS}
    totals = {"header_skip": 0, "chapter_skip": 0, "sub_header_skip": 0, "clean_skip": 0, "horiz_skip": 0, "placeholder_skip": 0}

    for loop_i, html in enumerate(tables_html_one):
        raw_idx = only_idx  # --only 模式下 raw_idx 固定
        print(f"[step3 广州] === table[{raw_idx}] ===")

        rows, stats, sheet_name, dispatch_key, extractor_name = _dispatch_one(html, raw_idx, caption_dispatch)
        print(f"[step3 广州]   caption dispatch → '{dispatch_key}' ({extractor_name})")
        if dispatch_key == "default":
            # 未命中 WARN log
            rows_raw = _parse_html_table(html)
            first_caption = ""
            for r in rows_raw:
                for c in r:
                    t = c.strip()
                    if t:
                        first_caption = t[:40]
                        break
                if first_caption:
                    break
            print(f"[WARN] table[{raw_idx}] caption 未命中 → fallback default, first='{first_caption}'")

        sheets[sheet_name].extend(rows)
        for k, v in stats.items():
            totals[k] += v
        print(f"[step3 广州]   → sheet '{sheet_name}', 数据行: {len(rows)}, stats: {stats}")

    return _finalize_extract(sheets, totals, only_idx, out_path, log_lines)


def _extract_by_type_group(tables_html, page_map, page_map_display, caption_dispatch, skip_set, out_path, log_lines):
    """full mode: 按 extractor 类型分组跑 (改 1+2 核心, 2026-08-06)

    阶段 A: 给每个 idx 算 dispatch_key + extractor, 按 (extractor, sheet) 分组
    阶段 B: 按 group 跑, 同 group idx 合并到 sheet (一次跑完一种类型)
    """
    # 阶段 A: 分组
    type_groups = {}  # {(extractor_name, sheet_name): [idx, ...]}
    dispatch_log = {}  # {idx: (dispatch_key, extractor_name)} 用于 WARN log
    PENDING = []  # 改 3 (2026-08-06): 未接入 idx 集合 + 物理页

    for raw_idx, html in enumerate(tables_html):
        if raw_idx in skip_set:
            # 改 3: 收集未接入 idx, 末尾汇总输出, 避免遗忘
            PENDING.append((raw_idx, page_map[raw_idx] if raw_idx < len(page_map) else -1))
            print(f"[step3 广州] === table[{raw_idx}] === SKIP (未接入)")
            continue

        rows_raw = _parse_html_table(html)
        dispatch_key = _find_caption_extractor(rows_raw, caption_dispatch)
        if dispatch_key != "default":
            cfg = caption_dispatch.get(dispatch_key, caption_dispatch["default"])
            extractor_name = cfg["extractor"]
            sheet_name = cfg.get("sheet", "金属材料")
        else:
            extractor_name, sheet_name = _fallback_extractor(raw_idx)

        group_key = (extractor_name, sheet_name)
        type_groups.setdefault(group_key, []).append(raw_idx)
        dispatch_log[raw_idx] = (dispatch_key, extractor_name)

    # 阶段 A 输出: group 概览 + PENDING 汇总
    print(f"[step3 广州] === 按类型分组 ({len(type_groups)} group, {sum(len(v) for v in type_groups.values())} idx) ===")
    for (ext_name, sht), idxs in type_groups.items():
        # (2026-08-06 v2): 改用 page_map_display 让 group log 也显示跨页 (e.g. p11-13)
        pages = [page_map_display[i] for i in idxs if i < len(page_map_display)]
        print(f"[step3 广州]   {ext_name} → sheet '{sht}': idx={idxs} (物理页={pages})")
    if PENDING:
        print(f"[step3 广州] === PENDING ({len(PENDING)} 张未接入, 待 P1+ 接入) ===")
        for idx, page in PENDING:
            page_str = f"p{page+1}" if page >= 0 else "p?"
            print(f"[step3 广州]   table[{idx}] = {page_str}")

    # 阶段 B: 按 group 跑
    sheets = {name: [] for name in _TARGET_SHEETS}
    totals = {"header_skip": 0, "chapter_skip": 0, "sub_header_skip": 0, "clean_skip": 0, "horiz_skip": 0, "placeholder_skip": 0}

    for (ext_name, sht), idxs in type_groups.items():
        rows_all = []
        group_stats = {"header_skip": 0, "chapter_skip": 0, "sub_header_skip": 0, "clean_skip": 0, "horiz_skip": 0, "placeholder_skip": 0}
        for raw_idx in idxs:
            print(f"[step3 广州] === table[{raw_idx}] ===")
            dispatch_key = dispatch_log[raw_idx][0]
            html = tables_html[raw_idx]
            print(f"[step3 广州]   caption dispatch → '{dispatch_key}' ({ext_name})")
            if dispatch_key == "default":
                # 未命中 WARN log
                rows_raw = _parse_html_table(html)
                first_caption = ""
                for r in rows_raw:
                    for c in r:
                        t = c.strip()
                        if t:
                            first_caption = t[:40]
                            break
                    if first_caption:
                        break
                print(f"[WARN] table[{raw_idx}] caption 未命中 → fallback default, first='{first_caption}'")

            rows, stats = globals()[ext_name](html, **_BUSWAY_KWARGS.get(raw_idx, {})) if ext_name == "_process_table_busway" else globals()[ext_name](html)
            rows_all.extend(rows)
            for k, v in stats.items():
                group_stats[k] += v
                totals[k] += v
            print(f"[step3 广州]   → sheet '{sht}', 数据行: {len(rows)}, stats: {stats}")

        sheets[sht].extend(rows_all)
        print(f"[step3 广州] [group] {ext_name} → sheet '{sht}': 合并 {len(idxs)} idx, 共 {len(rows_all)} 行, "
              f"stats(合并)={group_stats}")

    return _finalize_extract(sheets, totals, only_idx=None, out_path=out_path, log_lines=log_lines)


def _finalize_extract(sheets, totals, only_idx, out_path, log_lines):
    """共用收尾: 编号 + 总 skip log + 验证 + 写 extract.json"""
    print(f"[step3 广州] 总表头 skip: {totals['header_skip']}")
    print(f"[step3 广州] 总章首 skip: {totals['chapter_skip']}")
    print(f"[step3 广州] 总 sub-header skip: {totals['sub_header_skip']}")
    print(f"[step3 广州] 总 clean skip (4 价格空/无效): {totals['clean_skip']}")
    if totals.get('horiz_skip', 0):
        print(f"[step3 广州] 总 横表 skip (配合比对照表): {totals['horiz_skip']}")

    # 每 sheet 独立编号
    for name in _TARGET_SHEETS:
        _number_rows_independently(sheets[name])
        print(f"[step3 广州] Sheet '{name}' ({len(_HEADERS[name])} 列): {len(sheets[name])} 行")

    # 按 only_idx dispatch 验证 (Step 1.6: only_idx=0, Step 2.1: only_idx=1)
    validator = _VALIDATORS.get(only_idx)
    if validator is not None:
        ok, msg = validator(sheets)
        print(f"[step3 广州] 验证 (table[{only_idx}]): {msg}")
        if not ok:
            print(f"[step3 广州] ❌ 验证失败, 停止写入 extract.json")
            return None
    elif only_idx is None:
        print(f"[step3 广州] (跳过验证, --only=N 才强制)")

    # 写 extract.json
    extract_data = []
    for name in _TARGET_SHEETS:
        extract_data.append({
            "sheet": name,
            "headers": _HEADERS[name],
            "rows": sheets[name],
        })
    out_path.write_text(json.dumps(extract_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[step3 广州] ✅ 写出: {out_path}")

    if log_lines is not None:
        for name in _TARGET_SHEETS:
            log_lines.append(f"[step3 广州] Sheet '{name}': {len(sheets[name])} 行")

    return str(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="广州字段提取 (Step 1.6)")
    parser.add_argument("result_json", help="OCR result.json 路径")
    parser.add_argument("--only", type=int, default=None, help="只跑第 N 个 table (调试)")
    args = parser.parse_args()
    extract(args.result_json, only_idx=args.only, log_lines=[])