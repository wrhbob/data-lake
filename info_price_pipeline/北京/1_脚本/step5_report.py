"""step5_report.py - 残缺统计 + 写 5_日志

目的：
  clean.json → 5_日志/{city}/{period}_process.log

日志内容（按阶段分段）：
  [step2 classify] ...
  [step3 extract] ...
  [step4 clean] ...
  [step5 report] 残缺统计

残缺检测：
  - 空材料名称 / 规格 / 单位 / 价格
  - 空区县
  - 跨页合并数
  - PC 拆行数
"""
import json
import sys
from pathlib import Path
from datetime import datetime

# Windows GBK 编码修复
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ROOT, city_to_code


def report(clean_json, city, period, log_lines):
    """写残缺统计 + 合并日志"""
    code = city_to_code(city)
    log_dir = ROOT / "5_日志" / city
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{period}_process.log"

    with open(clean_json, encoding="utf-8") as f:
        data = json.load(f)

    # 统计
    n_market_rows = 0
    n_pc_rows = 0
    n_empty_material = 0
    n_empty_spec = 0
    n_empty_unit = 0
    n_empty_price = 0
    n_empty_district = 0

    n_market_tables = 0
    n_pc_tables = 0

    for table in data:
        section = table.get("section", "")
        rows = table.get("rows", [])
        if section == "MARKET":
            n_market_tables += 1
            for row in rows:
                n_market_rows += 1
                if not row.get("材料名称"):
                    n_empty_material += 1
                if not row.get("规格型号"):
                    n_empty_spec += 1
                if not row.get("单位"):
                    n_empty_unit += 1
                if not row.get("除税价格(元)"):
                    n_empty_price += 1
                if not row.get("district"):
                    n_empty_district += 1
        elif section == "PC":
            n_pc_tables += 1
            for row in rows:
                n_pc_rows += 1

    summary = f"""
==== {city} {period} 残缺统计 ====
生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}

MARKET 行数: {n_market_rows} (表: {n_market_tables})
PC 行数: {n_pc_rows} (表: {n_pc_tables})

空字段统计（MARKET）:
  空材料名称: {n_empty_material}
  空规格型号: {n_empty_spec}
  空单位: {n_empty_unit}
  空除税价格: {n_empty_price}
  空区县: {n_empty_district}

==== END ====
"""
    log_lines.append(summary)

    # 写日志
    with log_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    print(f"[step5] ✅ 写出: {log_path}")
    print(summary)
    return log_path


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: python step5_report.py <clean_json_path> <city> <period>", file=sys.stderr)
        sys.exit(1)
    report(sys.argv[1], sys.argv[2], sys.argv[3], [])
