# -*- coding: utf-8 -*-
"""
四川省建设工程工程量清单计价定额(房屋建筑工程)扫描版 PDF 解析管线
================================================================
输入: 扫描版定额 PDF(300dpi 图像页, 无正文文字层)
输出: dinge.json / dinge.sqlite  —— 结构化定额库

流程: PDF -> 页面图像 -> PaddleOCR PP-StructureV3(版面+表格识别)
      -> 页面分类(章节页/说明页/定额表页) -> 表格语义解析 -> 结构化落库

依赖(本地/联网环境安装):
    pip install "paddleocr>=2.7" paddlepaddle pymupdf
    # GPU: paddlepaddle-gpu

用法:
    python parse_dinge.py --pdf 定额.pdf --out ./output --pages 25-462
"""
import argparse
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------------------
# 1. PDF -> 页面图像
# ---------------------------------------------------------------------------

def pdf_to_images(pdf_path: str, out_dir: str, page_from: int, page_to: int, dpi: int = 300):
    """用 PyMuPDF 渲染页面(扫描件本身300dpi, 渲染300dpi即1:1)"""
    import fitz
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    paths = []
    zoom = dpi / 72
    for pno in range(page_from - 1, min(page_to, len(doc))):
        pix = doc[pno].get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
        p = os.path.join(out_dir, f"page_{pno+1:04d}.png")
        pix.save(p)
        paths.append((pno + 1, p))
    return paths

# ---------------------------------------------------------------------------
# 2. 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Resource:
    """人材机消耗行"""
    category: str          # 人工 | 材料 | 机械
    name: str              # 如: 混凝土工、聚乙烯薄膜
    spec: str = ""         # 规格型号(若名称中含规格可拆出)
    unit: str = ""         # 工日 / kg / m3 ...
    price: float = None    # 单价(元)
    quantity: float = None # 消耗量

@dataclass
class QuotaItem:
    """一个定额子目, 语义坐标的最小单元"""
    code: str                       # 定额编号, 如 AA0001
    name: str                       # 完整项目名称(多级表头拼接), 如: 人工挖一般土方 一、二类土 深度2m以内
    name_parts: list = field(default_factory=list)  # 各级表头 ["人工挖一般土方","一、二类土","深度2m以内"]
    unit: str = ""                  # 计量单位, 如 10m3
    base_price: float = None        # 基价(元)
    labor_cost: float = None        # 人工费
    material_cost: float = None     # 材料费
    machine_cost: float = None      # 机械费
    work_content: str = ""          # 工作内容(表格上方"工作内容:...")
    note: str = ""                  # 附注(表下"注:...")
    chapter_path: list = field(default_factory=list)   # ["A.1 土石方工程","A.1.1 土方工程"]
    list_code_prefix: str = ""      # 章节标注的清单编码前缀, 如 010101
    page: int = 0                   # 来源页码
    resources: list = field(default_factory=list)      # [Resource]

# ---------------------------------------------------------------------------
# 3. OCR + 版面/表格识别
# ---------------------------------------------------------------------------

class OcrEngine:
    def __init__(self, lang="ch", use_gpu=False):
        from paddleocr import PPStructureV3  # PaddleOCR >= 2.7 / 3.x
        self.engine = PPStructureV3(device="gpu" if use_gpu else "cpu")

    def parse_page(self, img_path: str):
        """返回 [{type: text|table|title, bbox, text|html}]"""
        result = self.engine.predict(img_path)
        blocks = []
        for res in result:
            for blk in res.get("parsing_res_list", res.get("layout_parsing_result", [])):
                blocks.append(blk)
        return blocks

# ---------------------------------------------------------------------------
# 4. 页面分类与文本解析
# ---------------------------------------------------------------------------

RE_CHAPTER   = re.compile(r"^([A-Z]\.\d+(?:\.\d+)*)\s*(\S.*?)\s*(?:[((]\s*编\s*码\s*[::]\s*(\d{6,9})\s*[))])?\s*$")
RE_QUOTA_CODE = re.compile(r"^[A-Z]{2}\d{4}$")           # AA0001
RE_WORK      = re.compile(r"工作内容\s*[::]\s*(.+)")
RE_NOTE      = re.compile(r"^注\s*[::]\s*(.+)")
RE_UNIT_HDR  = re.compile(r"(?:计量)?单位\s*[::]\s*(\S+)")
WATERMARK    = set("四川省住房和城乡建设厅信息公开浏览专用")

def clean(s: str) -> str:
    s = re.sub(r"\s+", "", s or "")
    # 去掉整块都是水印字符的碎片
    if s and all(c in WATERMARK for c in s):
        return ""
    return s

def parse_number(s: str):
    s = (s or "").replace(",", "").replace(" ", "").replace("—", "").replace("-", "")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None

# ---------------------------------------------------------------------------
# 5. 定额表解析(核心)
#    表结构(以扫描版实测为准):
#      行0        : 定额编号 | AA0001 | AA0002 | ...
#      行1..k     : 项  目  | 多级合并表头(纵向拼接为子目名称) ... 末级常含"单位:10m3"
#      基价行     : 基  价(元) | 数值...
#      其中费用行 : 人工费/材料费/机械费(元)
#      消耗矩阵   : [类别(人工/材料/机械)] 名称 | 单位 | 单价(元) | 每列子目的消耗量
# ---------------------------------------------------------------------------

def html_table_to_grid(html: str):
    """PP-Structure 输出 HTML 表格 -> 二维网格(展开合并单元格, 值向右/向下填充)"""
    from html.parser import HTMLParser

    class TP(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows, self.cur, self.cell = [], None, None
            self.rs, self.cs = 1, 1
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "tr":
                self.cur = []
            elif tag in ("td", "th"):
                self.cell = ""
                self.rs = int(a.get("rowspan", 1)); self.cs = int(a.get("colspan", 1))
        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cur is not None:
                self.cur.append((self.cell.strip(), self.rs, self.cs))
                self.cell = None
            elif tag == "tr" and self.cur is not None:
                self.rows.append(self.cur); self.cur = None
        def handle_data(self, d):
            if self.cell is not None:
                self.cell += d

    tp = TP(); tp.feed(html)
    # 展开合并单元格
    grid, spans = [], {}   # spans[(r,c)] = value 由上方 rowspan 占位
    for r, row in enumerate(tp.rows):
        out, c = [], 0
        for val, rs, cs in row:
            while (r, c) in spans:
                out.append(spans.pop((r, c))); c += 1
            for dc in range(cs):
                out.append(val)
                for dr in range(1, rs):
                    spans[(r + dr, c)] = val
                c += 1
        while (r, c) in spans:
            out.append(spans.pop((r, c))); c += 1
        grid.append(out)
    return grid

def parse_quota_table(grid, work_content, chapter_path, list_code_prefix, page):
    """把一个定额表网格解析成若干 QuotaItem"""
    items = {}
    # ---- 找定额编号行
    code_row = None
    for ri, row in enumerate(grid):
        codes = [c for c in (clean(x) for x in row) if RE_QUOTA_CODE.match(c)]
        if len(codes) >= 1 and any("定额编号" in clean(x) or "编号" == clean(x) for x in row):
            code_row = ri; break
        if len(codes) >= 2:      # 兜底: 一行里出现多个编号
            code_row = ri; break
    if code_row is None:
        return []

    header = [clean(x) for x in grid[code_row]]
    col_of = {}                  # 列索引 -> 定额编号
    for ci, v in enumerate(header):
        if RE_QUOTA_CODE.match(v):
            col_of[ci] = v
    for code in col_of.values():
        items[code] = QuotaItem(code=code, name="", work_content=work_content,
                                chapter_path=list(chapter_path),
                                list_code_prefix=list_code_prefix, page=page)

    # ---- 行分类游标
    unit_global = ""
    name_rows, price_rows, res_rows = [], [], []
    section = "name"
    for ri in range(code_row + 1, len(grid)):
        row = grid[ri]
        joined = clean("".join(row))
        first = clean(row[0]) if row else ""
        if "基价" in first or ("基价" in joined and parse_number("".join(row[1:]))):
            section = "price"; price_rows.append(ri); continue
        if section == "price" and re.search(r"(人工费|材料费|机械费)", first + clean(row[1] if len(row) > 1 else "")):
            price_rows.append(ri); continue
        if re.match(r"^(人工|材料|机械)$", first) or (section in ("price", "res") and len(row) >= 4):
            section = "res"; res_rows.append(ri); continue
        if section == "name":
            name_rows.append(ri)

    # ---- 项目名称: 纵向逐级拼接, 每列取自己的路径
    for ci, code in col_of.items():
        parts = []
        for ri in name_rows:
            if ci < len(grid[ri]):
                v = clean(grid[ri][ci])
                v = re.sub(r"^项目$", "", v)
                mu = RE_UNIT_HDR.search(v)
                if mu:
                    items[code].unit = mu.group(1); v = RE_UNIT_HDR.sub("", v)
                if v and v not in parts:
                    parts.append(v)
        it = items[code]
        it.name_parts = parts
        it.name = " ".join(parts)
        if not it.unit:
            it.unit = unit_global

    # ---- 基价 / 三费
    for ri in price_rows:
        row = grid[ri]
        label = clean(row[0]) + clean(row[1] if len(row) > 1 else "")
        for ci, code in col_of.items():
            val = parse_number(row[ci]) if ci < len(row) else None
            it = items[code]
            if "基价" in label: it.base_price = val
            elif "人工费" in label: it.labor_cost = val
            elif "材料费" in label: it.material_cost = val
            elif "机械费" in label: it.machine_cost = val

    # ---- 人材机消耗矩阵
    cur_cat = ""
    for ri in res_rows:
        row = [clean(x) for x in grid[ri]]
        if not any(row):
            continue
        if row[0] in ("人工", "材料", "机械"):
            cur_cat = row[0]
        # 结构: [类别] 名称 单位 单价 q1 q2 ...
        cells = row[1:] if row[0] in ("人工", "材料", "机械", "") else row
        if len(cells) < 3:
            continue
        name, unit, price = cells[0], cells[1], parse_number(cells[2])
        if not name or name in ("名称", "单位"):
            continue
        offset = len(row) - len(cells)
        for ci, code in col_of.items():
            idx = ci  # 消耗量列与编号列对齐
            q = parse_number(row[idx]) if idx < len(row) and idx >= offset + 3 else None
            if q is not None:
                items[code].resources.append(asdict(Resource(
                    category=cur_cat or "材料", name=name, unit=unit,
                    price=price, quantity=q)))
    return list(items.values())

# ---------------------------------------------------------------------------
# 6. 主流程: 逐页解析, 维护章节游标
# ---------------------------------------------------------------------------

def run(pdf, out_dir, page_from, page_to, use_gpu=False):
    os.makedirs(out_dir, exist_ok=True)
    img_dir = os.path.join(out_dir, "pages")
    pages = pdf_to_images(pdf, img_dir, page_from, page_to)
    ocr = OcrEngine(use_gpu=use_gpu)

    chapter_path, list_prefix = [], ""
    work_content = ""
    all_items, chapter_notes = [], []

    for pno, img in pages:
        blocks = ocr.parse_page(img)
        page_text_blocks = []
        for blk in blocks:
            btype = blk.get("type") or blk.get("label", "")
            if btype == "table":
                html = blk.get("html") or blk.get("res", {}).get("html", "")
                if not html:
                    continue
                grid = html_table_to_grid(html)
                items = parse_quota_table(grid, work_content, chapter_path, list_prefix, pno)
                all_items.extend(items)
            else:
                txt = clean(blk.get("text") or blk.get("res", ""))
                if not txt:
                    continue
                page_text_blocks.append(txt)
                m = RE_CHAPTER.match(txt)
                if m:
                    level = m.group(1).count(".")
                    chapter_path = chapter_path[:level] 
                    chapter_path = chapter_path[:level] + [f"{m.group(1)} {m.group(2)}"]
                    if m.group(3):
                        list_prefix = m.group(3)
                mw = RE_WORK.search(txt)
                if mw:
                    work_content = mw.group(1)
                mn = RE_NOTE.match(txt)
                if mn and all_items:
                    # 注: 归属本页刚解析的子目
                    for it in all_items:
                        if it.page == pno:
                            it.note = (it.note + ";" if it.note else "") + mn.group(1)
                if "说明" in txt[:4]:
                    chapter_notes.append({"chapter": list(chapter_path), "page": pno, "text": txt})

    # ---- 落盘
    data = [asdict(x) for x in all_items]
    with open(os.path.join(out_dir, "dinge.json"), "w", encoding="utf-8") as f:
        json.dump({"items": data, "chapter_notes": chapter_notes}, f, ensure_ascii=False, indent=1)
    save_sqlite(os.path.join(out_dir, "dinge.sqlite"), all_items)
    print(f"解析完成: {len(all_items)} 个定额子目 -> {out_dir}/dinge.json")

def save_sqlite(path, items):
    con = sqlite3.connect(path)
    con.executescript("""
    DROP TABLE IF EXISTS quota; DROP TABLE IF EXISTS resource;
    CREATE TABLE quota(code TEXT PRIMARY KEY, name TEXT, unit TEXT,
      base_price REAL, labor_cost REAL, material_cost REAL, machine_cost REAL,
      work_content TEXT, note TEXT, chapter_path TEXT, list_code_prefix TEXT, page INT);
    CREATE TABLE resource(quota_code TEXT, category TEXT, name TEXT, spec TEXT,
      unit TEXT, price REAL, quantity REAL);
    CREATE INDEX ix_res ON resource(quota_code);
    """)
    for it in items:
        con.execute("INSERT OR REPLACE INTO quota VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (it.code, it.name, it.unit, it.base_price, it.labor_cost, it.material_cost,
             it.machine_cost, it.work_content, it.note,
             "/".join(it.chapter_path), it.list_code_prefix, it.page))
        for r in it.resources:
            con.execute("INSERT INTO resource VALUES(?,?,?,?,?,?,?)",
                (it.code, r["category"], r["name"], r.get("spec",""), r["unit"],
                 r["price"], r["quantity"]))
    con.commit(); con.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default="./output")
    ap.add_argument("--pages", default="1-462", help="如 25-462, 跳过封面目录")
    ap.add_argument("--gpu", action="store_true")
    a = ap.parse_args()
    p1, p2 = (int(x) for x in a.pages.split("-"))
    run(a.pdf, a.out, p1, p2, a.gpu)
