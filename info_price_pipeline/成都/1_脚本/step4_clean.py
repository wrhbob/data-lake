"""step4_clean.py - 区县识别（4 步优先级）

目的：
  extract.json → clean.json（每行加 city_detail / district）

区县提取（4 步优先级）：
  1. 表格前最近的 title block 匹配 districts 清单
  2. caption 匹配 districts 清单
  3. 章节标题匹配 districts 清单
  4. 全无 → 空

匹配方式：字符串包含（不是空格切分）
长字符串优先：sorted(districts, key=len, reverse=True)
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


def extract_district(text, districts_sorted):
    """从 text 里提取匹配的 district（长字符串优先）"""
    if not text:
        return ""
    for d in districts_sorted:
        if d and d in text:
            return d
    return ""


def clean(extract_json, city, log_lines=None):
    """extract.json → clean.json

    给每行加：
      - district: 区县
      - city_detail: 四川省成都市XX区
    """
    if log_lines is None:
        log_lines = []

    yaml_data = load_city_yaml(city)
    districts = yaml_data.get("districts", [])
    province = yaml_data.get("province", "")
    city_short = yaml_data.get("city", city)  # 城市简称（成都市）

    # 长字符串优先
    districts_sorted = sorted(districts, key=len, reverse=True)

    with open(extract_json, encoding="utf-8") as f:
        data = json.load(f)

    n_filled = n_empty = 0
    prev_district = ""  # 上一张成功 district
    prev_page = -999

    # 2026-08-03 增：MARKET 章节末段强制标「成都市」兜底
    # 根因：成都 MARKET 章节通用结构=各区县单独列 → 末段「成都市」统价
    #       末段 caption 在 OCR 阶段常缺失 → prev_district 继承错（蒲江污染 / 末段空）
    # 修法：找 MARKET 章节中"前一张是 MARKET caption 非空表"的空 caption 表 = 末段「成都市」统价
    # 阈值：必须 rows > 100（避免参考价专栏或苗木小表误命中）
    last_market_cities_idx = None
    market_indexes = [i for i, t in enumerate(data) if t.get("section") == "MARKET"]
    for i, idx in enumerate(market_indexes):
        cap = data[idx].get("caption", "")
        n_rows = len(data[idx].get("rows", []))
        if not cap and n_rows > 100:
            # 关键：前一张必须是 MARKET 非空 caption（确认是 MARKET 章节末段，不是另一个章节错标的表）
            if i > 0:
                prev_idx = market_indexes[i - 1]
                prev_cap = data[prev_idx].get("caption", "")
                if prev_cap:
                    last_market_cities_idx = idx

    for table in data:
        caption = table.get("caption", "")
        section = table.get("section", "")
        rows = table.get("rows", [])
        page = table.get("page", 0)

        # 跨章节/跨页距重置（避免把末章 district 错继承给末表）
        if page - prev_page > 5:
            prev_district = ""

        # 1. caption 提取
        district = extract_district(caption, districts_sorted)

        # 5. 2026-08-03 增：MARKET 章节末段「空 caption」表强制标「成都市」
        # 解决：2025-7 蒲江污染（page 92 蒲江县→page 94 末段继承蒲江）、2024-8/2023-5 末段 None
        # 必须放在 prev_district 继承之前，否则末段会被错继承覆盖
        if not district and section == "MARKET":
            idx = data.index(table)
            if idx == last_market_cities_idx:
                district = city_short  # = "成都市"
                log_lines.append(
                    f"  [step4] 🔧 末段兜底 district={district!r}: page {page}, caption='{caption[:40]}'"
                )
            # 2026-08-03 增：MARKET 章节「参考价专栏」等无区名 caption → 标「成都市」
            elif any(kw in caption for kw in ["参考价", "专栏", "价格行情"]):
                district = city_short
                log_lines.append(
                    f"  [step4] 🔧 MARKET 兜底 district={district!r}: page {page}, caption='{caption[:40]}'"
                )

        # 2. caption 空 + 页距 ≤ 15 → 继承上一张表 district
        if not district and prev_district:
            district = prev_district
            log_lines.append(
                f"  [step4] 继承 district={district!r}: page {page}, caption='{caption[:40]}'"
            )

        # 3. 章节标题（MARKET 通常是 caption 包含区名）
        # 4. 表格前 title block：v1 暂不做（依赖外部传 title）

        if not district:
            log_lines.append(
                f"  [step4] WARN: 区县识别失败: page {page}, caption='{caption[:40]}'"
            )
            n_empty += 1
        else:
            n_filled += 1
            prev_district = district
            prev_page = page

        # 构造 city_detail（避免 city 与 district 重复，如「四川省成都市成都市」）
        if district and district != city_short:
            city_detail = f"{province}{city_short}{district}"
        elif district:
            city_detail = f"{province}{city_short}"
        else:
            city_detail = ""

        # 过滤章节小标题（"1、玻璃"/"1.2 钢筋"）— 真删除
        filtered_rows = []
        n_dropped = 0
        for row in rows:
            name = row.get("材料名称", "")
            if re.match(r"^\d+[、\.]", name):
                n_dropped += 1
                continue
            filtered_rows.append(row)

        if n_dropped:
            log_lines.append(
                f"  [step4] 过滤小标题行: page {table.get('page')}, 删除 {n_dropped} 行"
            )
        table["rows"] = filtered_rows
        rows = filtered_rows

        for row in rows:
            # 2026-07-29 加：区/县名前面加「成都市」前缀（district 是「成都市」时保留原样，避免「成都市成都市」）
            if district:
                if district.startswith(city_short) or district == "成都市":
                    row["district"] = district  # 保留原样（含「成都市」本体的不加）
                else:
                    row["district"] = f"{city_short}{district}"
            else:
                row["district"] = ""
            row["city_detail"] = city_detail

    print(f"[step4] 区县识别: {n_filled} 表成功 / {n_empty} 表空")

    out_path = Path(extract_json).parent / "clean.json"
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[step4] ✅ 写出: {out_path}")

    return out_path, log_lines


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python step4_clean.py <extract_json_path> <city>", file=sys.stderr)
        sys.exit(1)
    clean(sys.argv[1], sys.argv[2])
