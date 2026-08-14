"""一次性验证脚本（只读，不动代码）：查 MinerU 输出形态

目的：P0-2 / P0-3 实施前确认 4 件事：
  1. HTML 是否有 rowspan/colspan 属性
  2. 表头 reset 模式（"代号/产品名称/规格型号..." 是否在多张表重复）
  3. schema_id=None 那 4 张表长啥样（45 真实表 - 41 已匹 - 4 待查）
  4. 章节小标题（如 "1．黑色及有色金属"）是否混在表行里
"""
import json
import re
from pathlib import Path
from collections import Counter

RESULT_JSON = Path("D:/AI学习/vs code/信息价提取/北京/3_中间产物/bj_06_ocr/result.json")


def fetch_tables():
    data = json.load(open(RESULT_JSON, encoding="utf-8"))
    content_list = json.loads(data["results"]["upload"]["content_list"])
    return [(i, item) for i, item in enumerate(content_list) if item.get("type") == "table"]


def stat_rowspan():
    """Q1: rowspan/colspan 属性统计"""
    tables = fetch_tables()
    has_rowspan = has_colspan = 0
    rowspan_samples = []
    for idx, t in tables:
        body = t.get("table_body", "") or ""
        if re.search(r"<td[^>]*rowspan\s*=", body):
            has_rowspan += 1
            if len(rowspan_samples) < 3:
                m = re.search(r"<td[^>]*rowspan\s*=\s*[\"']?(\d+)", body)
                rowspan_samples.append(f"idx={idx} rowspan={m.group(1) if m else '?'}")
        if re.search(r"<td[^>]*colspan\s*=", body):
            has_colspan += 1
    return f"共 {len(tables)} 张表\n" \
           f"   含 rowspan: {has_rowspan}\n" \
           f"   含 colspan: {has_colspan}\n" \
           f"   rowspan 样本：{rowspan_samples}"


def stat_header_repeat():
    """Q2: 表头 repeat 模式 — "代号 产品名称 规格型号" 是否出现在多张表"""
    tables = fetch_tables()
    header_pattern = re.compile(r"代号|产品名称|规格型号")
    header_rows = []
    for idx, t in tables:
        body = t.get("table_body", "") or ""
        first_tr = re.search(r"<tr>(.*?)</tr>", body, re.DOTALL)
        if first_tr and header_pattern.search(first_tr.group(1)):
            cells = re.findall(r"<td[^>]*>([^<]*)</td>", first_tr.group(1))
            cells_clean = [c.strip()[:8] for c in cells if c.strip()]
            header_rows.append(cells_clean)
    counter = Counter([tuple(h) for h in header_rows])
    most = counter.most_common(5)
    return f"表头形态 (前 5 高频):\n" + "\n".join(f"   {h} × {n}" for h, n in most)


def stat_unmatched_tables():
    """Q3: 未匹配 schema_id 那 4 张表长啥样"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from utils import ROOT
    import yaml

    yaml_data = yaml.safe_load(open(ROOT / "6_配置" / "城市模板" / "北京市.yaml", encoding="utf-8"))
    schemas = list(yaml_data.get("table_schemas", {}).values())

    # 复制 step3 的 match_schema_by_header 逻辑（避开循环引用）
    def match(rows):
        if not rows: return None
        header = None
        for r in rows[:5]:
            if len([c for c in r if c and c.strip()]) >= 3:
                header = r
                break
        if not header: return None
        n_cols = max(len(r) for r in rows)
        header_strs = [c.strip() for c in header if c]
        best, best_score = None, -1
        for s in schemas:
            if abs(s.get("n_cols", 0) - n_cols) > 1:
                continue
            score = 0
            if s.get("n_cols") in (n_cols, n_cols - 1, n_cols + 1):
                score += 10
            for kw in s.get("header_keywords", []):
                if any(kw in h for h in header_strs):
                    score += 1
            if score > best_score:
                best, best_score = s, score
        return best if best_score >= 11 else None

    tables = fetch_tables()
    unmatched = []
    for idx, t in tables:
        body = t.get("table_body", "") or ""
        # 简版 parse_html_table
        trs = re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL)
        rows = []
        for tr in trs:
            cells = re.findall(r"<td[^>]*>([^<]*)</td>", tr)
            rows.append([c.strip() for c in cells])
        s = match(rows)
        if not s:
            cap = t.get("table_caption", [])
            cap = cap[0] if isinstance(cap, list) and cap else (cap if isinstance(cap, str) else "")
            unmatched.append({
                "idx": idx,
                "page": t.get("page_idx"),
                "n_cols": max((len(r) for r in rows), default=0),
                "header": rows[0] if rows else [],
                "n_rows": len(rows),
                "caption": cap[:30],
                "first_data_row": rows[1] if len(rows) > 1 else [],
            })
    return f"未匹配 schema 表: {len(unmatched)} 张\n" + "\n\n".join(
        f"   idx={u['idx']} page={u['page']} n_cols={u['n_cols']} n_rows={u['n_rows']} cap='{u['caption']}'\n"
        f"     header={u['header']}\n"
        f"     data[0]={u['first_data_row']}"
        for u in unmatched[:6]
    )


def stat_section_subtitle_in_table():
    """Q4: 章节小标题（如 "1．黑色及有色金属"）是否混在表行里"""
    tables = fetch_tables()
    sub_re = re.compile(r"^[一二三四五六七八九十\d]+[．、\.]\s*[一-鿿]")
    hit_rows = []
    for idx, t in tables:
        body = t.get("table_body", "") or ""
        trs = re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL)
        for ri, tr in enumerate(trs[:6]):
            cells = re.findall(r"<td[^>]*>([^<]*)</td>", tr)
            non_empty = [c.strip() for c in cells if c.strip()]
            if len(non_empty) == 1 and sub_re.match(non_empty[0]):
                hit_rows.append((idx, ri, non_empty[0][:30]))
    return f"表格内 1-cell 章节小标题行: {len(hit_rows)} 张表命中\n" + \
           "\n".join(f"   idx={h[0]} row={h[1]} '{h[2]}'" for h in hit_rows[:8])


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    print("=" * 60)
    print("Q1: rowspan/colspan 属性")
    print("=" * 60)
    print(stat_rowspan())
    print()
    print("=" * 60)
    print("Q2: 表头 repeat 模式")
    print("=" * 60)
    print(stat_header_repeat())
    print()
    print("=" * 60)
    print("Q3: schema_id=None 未匹配的表")
    print("=" * 60)
    print(stat_unmatched_tables())
    print()
    print("=" * 60)
    print("Q4: 表格内的章节小标题行")
    print("=" * 60)
    print(stat_section_subtitle_in_table())
