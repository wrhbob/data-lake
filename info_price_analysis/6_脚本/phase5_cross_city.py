"""
phase5_cross_city.py - Phase 5 跨城比价 md 报告

输入（来自 phase2_match 产出）：
  3_中间产物/manifest.csv    （12 列，含 city 字段）
  3_中间产物/matches.csv     （同物组聚合）

输出：
  4_输出/reports/{city1+city2+...}_跨城_{period_range}_report_{ts}.md

报告结构（4 节）：
  §0 摘要         - 跨城 N 城 + 总物数 + 跨城独有/共有
  §1 跨城价差 Top  - 同物跨城 max-min 价差绝对值 Top 10
  §2 跨城均价对比 - 物 x 城 均价表（每物一行，每城一列）
  §3 跨城独有     - 仅在某城出现的物清单

设计原则（R10 城市模板）：不动 phase0_5/1/2/3/4；只读它们的 csv 产出 + manifest。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PROJECT_ROOT / "3_中间产物" / "manifest.csv"
MATCHES_PATH = PROJECT_ROOT / "3_中间产物" / "matches.csv"
ALL_PRICES_PATH = PROJECT_ROOT / "3_中间产物" / "all_prices.csv"
OUTPUT_DIR = PROJECT_ROOT / "4_输出" / "reports"


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_price(raw: str) -> float | None:
    """解析价格字符串 → float；失败返回 None。"""
    if not raw:
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def build_city_price_index(manifest: list[dict], matches: list[dict]) -> dict:
    """建立 city → {material+spec → [prices]} 索引。

    注：当前 phase2_match 的 matches.csv 字段格式需实际读取确认。
    本骨架版使用 phase3_query 输出的 all_prices.csv（如果存在），
    否则 fallback 从 manifest + matches 推断。
    """
    # 优先读 phase3 产出
    all_prices = load_csv(ALL_PRICES_PATH)
    if all_prices:
        index: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for row in all_prices:
            city = row.get("city") or row.get("district") or "未知"
            mat = row.get("material") or row.get("material_canonical") or ""
            spec = row.get("spec") or ""
            price = parse_price(row.get("price") or row.get("price_with_tax") or "")
            if not mat or price is None:
                continue
            key = f"{mat}|||{spec}"
            index[city][key].append(price)
        return dict(index)

    # fallback：从 matches.csv 聚合（matches 只有 match_id → canonical material）
    # 无价格信息时返回空 index（phase5 报 "无价格数据"）
    return {}


def section_zero_summary(cities: list[str], index: dict) -> str:
    total_items = sum(len(items) for items in index.values())
    # 跨城共有：出现在所有城的物
    common = None
    for city in cities:
        keys = set(index.get(city, {}).keys())
        common = keys if common is None else (common & keys)
    common_count = len(common) if common is not None else 0
    unique_total = total_items - len(cities) * common_count
    lines = [
        "## §0 摘要",
        "",
        f"**跨城 N={len(cities)}**：{', '.join(cities)}",
        f"**总物数**：{total_items}",
        f"**跨城共有**：{common_count}",
        f"**跨城独有**：{unique_total}（估算：总 - N×共有）",
        "",
    ]
    return "\n".join(lines)


def section_one_price_gap_top(index: dict, top_n: int = 10) -> str:
    """同物跨城 max-min 价差绝对值 Top 10。"""
    gaps = []
    # 取所有物 key
    all_keys = set()
    for items in index.values():
        all_keys.update(items.keys())
    for key in all_keys:
        prices_by_city = {}
        for city, items in index.items():
            if key in items:
                ps = items[key]
                if ps:
                    prices_by_city[city] = sum(ps) / len(ps)
        if len(prices_by_city) < 2:
            continue
        mn = min(prices_by_city.values())
        mx = max(prices_by_city.values())
        gaps.append((key, mn, mx, mx - mn, prices_by_city))

    gaps.sort(key=lambda x: x[3], reverse=True)
    lines = ["## §1 跨城价差 Top", "", "| 物（规格） | 最低均价 | 最高均价 | 价差 | 城市分布 |", "|---|---|---|---|---|"]
    for key, mn, mx, gap, pc in gaps[:top_n]:
        mat, spec = key.split("|||", 1)
        city_dist = ", ".join(f"{c}={p:.2f}" for c, p in sorted(pc.items(), key=lambda x: x[1]))
        lines.append(f"| {mat} {spec} | {mn:.2f} | {mx:.2f} | {gap:.2f} | {city_dist} |")
    if not gaps:
        lines.append("| (无跨城数据) | - | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def section_two_city_avg_table(index: dict, cities: list[str], top_n: int = 20) -> str:
    """物 x 城 均价表（每物一行，每城一列）。"""
    all_keys = set()
    for items in index.values():
        all_keys.update(items.keys())
    rows = []
    for key in all_keys:
        mat, spec = key.split("|||", 1)
        row = {"key": key, "mat": mat, "spec": spec}
        for city in cities:
            ps = index.get(city, {}).get(key, [])
            row[city] = sum(ps) / len(ps) if ps else None
        rows.append(row)
    # 按"已知城市数 × 平均价"排序（多城都有 + 价格高的优先）
    rows.sort(key=lambda r: sum(1 for c in cities if r[c] is not None), reverse=True)

    header = "| 物 | 规格 | " + " | ".join(cities) + " |"
    sep = "|---|---|" + "|".join(["---"] * len(cities)) + "|"
    lines = ["## §2 跨城均价对比", "", header, sep]
    for r in rows[:top_n]:
        cells = [r["mat"], r["spec"]]
        for c in cities:
            v = r[c]
            cells.append(f"{v:.2f}" if v is not None else "-")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def section_three_unique(index: dict, cities: list[str]) -> str:
    """跨城独有：仅在某城出现的物清单。"""
    lines = ["## §3 跨城独有", ""]
    for city in cities:
        my_keys = set(index.get(city, {}).keys())
        others = set()
        for c in cities:
            if c != city:
                others.update(index.get(c, {}).keys())
        unique = my_keys - others
        lines.append(f"### {city} 独有（{len(unique)} 项）")
        if not unique:
            lines.append("- (无)")
        else:
            for k in sorted(unique)[:20]:
                mat, spec = k.split("|||", 1)
                lines.append(f"- {mat} {spec}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 跨城比价 md 报告")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--matches", default=str(MATCHES_PATH))
    parser.add_argument("--all-prices", default=str(ALL_PRICES_PATH))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    print("加载 manifest + matches...")
    manifest = load_csv(Path(args.manifest))
    matches = load_csv(Path(args.matches))
    cities = sorted(set(m.get("city") for m in manifest if m.get("city")))
    print(f"  manifest: {len(manifest)} 文件，cities={cities}")

    if len(cities) < 2:
        print(f"[WARN] 跨城分析需要 ≥2 城，实际 {len(cities)} 城。生成空报告。")

    index = build_city_price_index(manifest, matches)
    total_prices = sum(len(v) for items in index.values() for v in items.values())
    print(f"  跨城价格索引：{len(index)} 城，共 {total_prices} 条价格")

    # 报告元数据
    periods = sorted(set(m["period"] for m in manifest if m.get("period")))
    period_range = f"{periods[0]}-{periods[-1]}期" if periods else "?"
    cities_label = "+".join(cities) if cities else "未知"

    sections = []
    sections.append("# 信息价跨城比价报告")
    sections.append("")
    sections.append(f"> 城市：{cities_label} | 期数：{period_range} | 文件：{len(manifest)} | 生成时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    sections.append("")
    sections.append("---")
    sections.append("")
    sections.append(section_zero_summary(cities, index))
    sections.append("---")
    sections.append("")
    sections.append(section_one_price_gap_top(index))
    sections.append("---")
    sections.append("")
    sections.append(section_two_city_avg_table(index, cities))
    sections.append("---")
    sections.append("")
    sections.append(section_three_unique(index, cities))
    sections.append("")
    sections.append("---")
    sections.append("")
    sections.append(f"**报告生成时间**：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    sections.append(f"**项目路径**：`{PROJECT_ROOT}/`")

    # 写文件
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{cities_label}_跨城_{period_range}_report_{timestamp}.md"
    report_path = output_dir / filename

    report_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"\n✓ 跨城报告已写: {report_path}")
    print(f"  大小: {report_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()