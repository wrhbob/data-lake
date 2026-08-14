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

    # 2026-07-31 增：从 classified.json 读 UNKNOWN 章节（脚本不依赖 AI，最后再给用户一次反馈）
    classified_json = Path(clean_json).parent / "classified.json"
    unknown_chapters = []
    if classified_json.exists():
        try:
            with open(classified_json, encoding="utf-8") as f:
                classified = json.load(f)
            unknown_chapters = classified.get("unknown_chapters", [])
        except Exception:
            pass

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

    # 2026-07-31 增：从 classified.json 读 SKIP 表统计（OCR 横向识别失败的）
    # 原因：clean.json 已经过滤 SKIP，统计要从源头（classified）读
    n_ocr_h_skip = 0
    ocr_h_skip_tables = []
    if classified_json.exists():
        try:
            with open(classified_json, encoding="utf-8") as f:
                _cls = json.load(f)
            for item in _cls.get("content_list", []):
                if item.get("type") != "table":
                    continue
                if item.get("section") != "SKIP":
                    continue
                cap = item.get("table_caption", [])
                cap_text = cap[0] if isinstance(cap, list) and cap else (cap if isinstance(cap, str) else "")
                # OCR 横向识别失败的特征：caption 含「X 不含税价格表」（PC 章节内的横向子表）
                # 兼容旧版：caption 含「砂浆价格表」（也是 OCR 横向失败）
                if "不含税价格表" in cap_text or ("价格表" in cap_text and "砂浆" in cap_text):
                    n_ocr_h_skip += 1
                    ocr_h_skip_tables.append({
                        "page": item.get("page_idx", 0),
                        "caption": cap_text[:50],
                    })
        except Exception:
            pass

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
"""
    # 2026-07-31 增：UNKNOWN 章节最终反馈（脚本不依赖 AI → 必须告诉用户哪些章节没识别）
    if unknown_chapters:
        summary += f"""
⚠️  UNKNOWN 章节: {len(unknown_chapters)} 个（section_titles.json 词库未覆盖）
"""
        for ch in unknown_chapters:
            summary += f"  - '{ch['name']}' (TOC页{ch['toc_page']}) → 建议: {ch['suggest']}\n"
        summary += """  修复方法：改 6_配置/section_titles.json，把关键词加到对应类的 kw 列表
"""

    summary += "==== END ====\n"
    log_lines.append(summary)

    # 2026-07-31 增：OCR 横向识别失败提示
    if n_ocr_h_skip > 0:
        log_lines.append(
            f"\n⚠️  OCR 横向识别失败 {n_ocr_h_skip} 张表（PC 砂浆不含税价格表）："
        )
        for t in ocr_h_skip_tables:
            log_lines.append(
                f"  - 页 {t['page']}: {t['caption']}"
            )
        log_lines.append(
            "  修法：当前 SKIP 跳过（不进 xlsx）。后续如需此数据，装 PaddleOCR 救回（v14 长寿区 p102 经验）。"
        )

    # 写日志
    with log_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    print(f"[step5] ✅ 写出: {log_path}")
    print(summary)
    # 2026-07-31 增：UNKNOWN 章节额外强调一次（用醒目符号）
    if unknown_chapters:
        print(f"\n[step5] ❗ UNKNOWN 章节 {len(unknown_chapters)} 个需人工处理：")
        for ch in unknown_chapters:
            print(f"   - '{ch['name']}' → 建议: {ch['suggest']}")
        print(f"[step5]    改 6_配置/section_titles.json 加关键词后重跑\n")
    return log_path


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: python step5_report.py <clean_json_path> <city> <period>", file=sys.stderr)
        sys.exit(1)
    report(sys.argv[1], sys.argv[2], sys.argv[3], [])
