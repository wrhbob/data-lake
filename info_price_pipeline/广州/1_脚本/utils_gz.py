"""utils_gz.py - 广州专属工具函数 (2026-08-05 建, Step 1)

复用源 (按约束「不改其他城市」, 复制函数体):
  - 武汉 utils.py: load_config() (覆盖→合并机制)
  - 成都 step3_extract_cd.py: split_2d_table() (按列名重复检测切分点)

约束:
  - load_config: 根 + 广州 per-city caption_keywords.json / skip_keywords.json 合并
  - clean_cell: 简单清洗 (复 cd)
  - split_2d_table: 双列拼接表硬切
"""
import json
import re  # 2026-08-10 补: clean_unit 4 行 regex 用
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 广州/


def load_config(name):
    """读 6_配置/<name>.json (2026-08-05 复用武汉合并机制)

    加载顺序:
      1. 根 6_配置/{name}.json (跨城通用综合关键词, 必加载)
      2. per-city 6_配置/{name}.json (广州独有, 追加到根的)

    合并逻辑: list 追加不去重, dict 合并 kw, _ 开头的 key 整段替换

    参数 name 可带或不带 .json 后缀 (自动补)
    """
    project_root = ROOT.parent
    if not name.endswith(".json"):
        name = name + ".json"
    root_path = project_root / "6_配置" / name
    per_city_path = ROOT / "6_配置" / name

    root_data = {}
    if root_path.exists():
        root_data = json.loads(root_path.read_text(encoding="utf-8"))

    if per_city_path.exists() and per_city_path != root_path:
        per_city_data = json.loads(per_city_path.read_text(encoding="utf-8"))
        for key, val in per_city_data.items():
            if key.startswith("_"):
                root_data[key] = val
                continue
            if key not in root_data:
                root_data[key] = val
            elif isinstance(val, dict) and isinstance(root_data[key], dict):
                if "kw" in val and "kw" in root_data[key]:
                    root_data[key]["kw"] = list(root_data[key]["kw"]) + list(val["kw"])
                for k, v in val.items():
                    if k != "kw":
                        root_data[key][k] = v
            else:
                root_data[key] = val
    return root_data


def clean_cell(s):
    """简单清洗: 去标签+strip (复 cd)"""
    import re
    s = re.sub(r"<[^>]+>", "", str(s)).strip()
    return s


def split_2d_table(rows):
    """二维拼接表硬切 (按表头列名重复 + 数据行 idx 5 编码/价格自适应, 广州修订)

    切分依据 (Step 2.1 扩展, 2026-08-05):
      1) 找 header 行 (row[0]='材料编码'), header_n_cols
      2) header_split = 表头第一个重复列名位置 (跳过空 cell)
         - table[0] header n=10, idx 5 直接重复 → header_split=5
         - table[1] header n=11, idx 5 空 idx 6 重复 → header_split=6
      3) eff_split = 有效切点 (header idx 5 空时 - 1):
         - header_split=5 → eff_split=5
         - header_split=6 → eff_split=5
      4) 找 data_start (>= 15 位数字)
      5) 数据行按长度 + idx 5 内容自适应:
         - len == header_n_cols + 1 → split = header_split + 1 (左 6 双价 + 右 5)
         - len == header_n_cols:
           - idx 5 是 >=15 位数字 (右编码) → split = eff_split (左 5 + 右 n)
           - 否则 (idx 5 是价格/短数字) → split = header_split (左 6 + 右 5)
         - len == header_n_cols - 1 → split = eff_split (左 5 + 右 5)
         - 其他 → 默认 header_split

    适用:
      - 双列 5+5 (table[14+], table[1] row[10-11])
      - 双列 6+5 (table[2-13], 左多「均值」)
      - 双列 5+6 (table[1] row[1-9], 右多「均值」)
    """
    if not rows:
        return rows, None

    # 1) 找 header 行 (row[0]='材料编码')
    header_idx = -1
    for i, r in enumerate(rows):
        if r and r[0].strip() == "材料编码":
            header_idx = i
            break
    if header_idx < 0:
        return rows, None

    header_n_cols = len(rows[header_idx])
    if header_n_cols < 8:
        return rows, None

    # 2) header_split = 表头第一个重复列名位置 (跳过空 cell)
    header_split = -1
    seen = {}
    for i, col_name in enumerate(rows[header_idx]):
        col_name = col_name.strip()
        if not col_name:
            continue
        if col_name in seen:
            header_split = i
            break
        seen[col_name] = i
    if header_split <= 4:
        return rows, None

    # 3) 有效切点 (header idx 5 空时 - 1)
    eff_split = header_split if header_split <= 5 else header_split - 1

    # 4) 找 data_start (>= 15 位数字)
    data_start = -1
    for i in range(header_idx + 1, len(rows)):
        r = rows[i]
        if not r:
            continue
        cell = r[0].strip() if r[0] else ""
        if cell.isdigit() and len(cell) >= 15:
            data_start = i
            break
    if data_start < 0:
        return rows, None

    # 5) 切分 (按 idx 5 编码/价格自适应)
    data_rows = rows[data_start:]
    left = []
    right = []
    for r in data_rows:
        n = len(r)
        cell5 = r[5].strip() if len(r) > 5 else ""
        if n == header_n_cols + 1:
            # 多一列 → idx 5 是左双价 (均值列)
            split = header_split + 1
        elif n == header_n_cols + 2:
            # 多两列 → 左右各多 1 (左 idx 5 双价 + 右 idx 5 双价), Step 4 table[3] 混凝土
            split = header_split + 1
        elif n == header_n_cols:
            # 判断 idx 5 是右编码 (>=15 位) 还是左双价 (短数字)
            if cell5.isdigit() and len(cell5) >= 15:
                split = eff_split
            else:
                split = header_split
        elif n == header_n_cols - 1:
            # 少一列 → idx 5 是右编码
            split = eff_split
        else:
            split = header_split
        left.append(r[:split])
        right.append(r[split:n])

    # 6) 补列: 子表 < 5 列 -> 在 index=2 插空
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
# 单位 LaTeX 清洗 (复武汉 step3:50-58)
# ============================================================
_UNIT_LATEX_REPLACES = [
    ("${\\mathrm{m}}^{3}$", "m3"),
    ("${\\mathrm{m}}^{2}$", "m2"),
    ("$m^3$", "m3"),
    ("$m^2$", "m2"),
    ("$m^{3}$", "m3"),
    ("$m^{2}$", "m2"),
]


def clean_unit(u):
    """单位 LaTeX 清洗: $m^2$ / $m^3$ -> m2/m3

    2026-08-10 升级 4 行 regex 覆盖 \mathrm{m}^{N} / \text{m}^{N} / \mathrm m^{N} / {\mathrm m}^{N} 等变种
    输出保持 m2/m3 数字后缀 (广州格式), 跟原 6 行 replace 行为一致

    2026-08-11 增: 范围最广覆盖 (复北京/成都/重庆数学符号 + HTML 实体反转义)
    """
    import html as _html  # 2026-08-11 增
    s = str(u).strip()
    # 保留 6 行字面替换 (兜底 已有场景)
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
    # 2026-08-11 增: 数学符号 (复北京/成都/重庆)
    s = re.sub(r"\\Phi\b", "Φ", s)
    s = re.sub(r"\\times\b", "×", s)
    s = re.sub(r"\\cdot\b", "·", s)
    s = re.sub(r"\\pm\b", "±", s)
    s = re.sub(r"\\le\b", "≤", s)
    s = re.sub(r"\\ge\b", "≥", s)
    s = re.sub(r"\\ne\b", "≠", s)
    # 2026-08-11 增: HTML 实体反转义
    s = _html.unescape(s)
    # 2026-08-11 增: 英寸清洗 (复广州 _clean_inch, 广州 clean_unit 也覆盖)
    s = re.sub(r"\\?frac\{(\d+)\}\{(\d+)\}", r"\1/\2", s)
    s = s.replace("⁄", "/")
    s = s.replace("$", "")
    return s