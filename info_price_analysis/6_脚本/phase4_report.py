"""
phase4_report.py - Phase 4 md 总报告

6 节结构（项目.md §10）：
  §0 摘要         - 造价员只看这一节
  §1 数据盘点     - 文件清单 + 总行数 + 空列
  §2 同物匹配     - 同物组数 + pending + 黑名单
  §3 价格分布     - 各材料 4 数
  §4 跨期趋势     - 同物跨期价格变化
  §5 区县空间插值 - IDW 估算
  §6 待人工复核   - pending + 异常价格 + 缺失数据

输入:
  3_中间产物/manifest.csv
  3_中间产物/matches.csv
  5_日志/pending_pool.csv
  3_中间产物/idw_estimates.csv
  3_中间产物/query_results.json

输出:
  4_输出/reports/{city}_{period_range}_report_{timestamp}.md
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PROJECT_ROOT / "3_中间产物" / "manifest.csv"
MATCHES_PATH = PROJECT_ROOT / "3_中间产物" / "matches.csv"
PENDING_PATH = PROJECT_ROOT / "5_日志" / "pending_pool.csv"
IDW_PATH = PROJECT_ROOT / "3_中间产物" / "idw_estimates.csv"
QUERIES_PATH = PROJECT_ROOT / "3_中间产物" / "query_results.json"
OUTPUT_DIR = PROJECT_ROOT / "4_输出" / "reports"


def load_csv(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def section_zero_summary(manifest: list, matches: list, pending: list,
                        idw_rows: list, queries: dict) -> str:
    """§0 摘要：造价员只看这一节"""
    lines = []
    lines.append('## §0 摘要')
    lines.append('')

    # 关键指标
    n_files = len(manifest)
    periods = sorted(set(m['period'] for m in manifest))
    period_range = f"{periods[0]}-{periods[-1]}期" if periods else "?"
    cities = set(m['city'] for m in manifest if m.get('city'))
    city = ','.join(cities) if cities else '未知'

    total_rows = sum(int(m.get('row_count', 0)) for m in manifest)
    n_matches = len(matches)
    n_pending = len(pending)
    n_idw_known = sum(1 for r in idw_rows if r['type'] == 'known')
    n_idw_est = sum(1 for r in idw_rows if r['type'] == 'estimated')
    n_idw_un = sum(1 for r in idw_rows if r['type'] == 'unavailable')

    cross_period_count = len(queries.get('cross_period', {}))
    cross_district_count = len(queries.get('cross_district', {}))

    lines.append(f'**范围**：{city} | {period_range} | {n_files} 个文件 | {total_rows:,} 行')
    lines.append('')
    lines.append(f'**同物匹配**：{n_matches:,} 个同物组（{n_pending} 条 pending 待审）')
    lines.append('')
    lines.append(f'**空间插值**：{n_idw_known:,} 已知 + {n_idw_est:,} 估算 + {n_idw_un:,} 不可用（区县 < 3）')
    lines.append('')

    # 跨期趋势 Top 5（最显著波动）
    cross_period = queries.get('cross_period', {})
    if cross_period:
        sorted_periods = sorted(cross_period.items(),
                               key=lambda x: abs(x[1]['first_to_last_pct']),
                               reverse=True)
        lines.append('**价格波动 Top 5（02→06 期）**：')
        for mid, info in sorted_periods[:5]:
            arrow = '↑' if info['direction'] == 'up' else ('↓' if info['direction'] == 'down' else '→')
            lines.append(f'- {arrow} {info["material"]} / {info["spec"]}: '
                        f'{info["first_to_last_pct"]:+.2f}% (波动 {info["volatility_pct"]}%)')
        lines.append('')

    # 横向对比 Top 5（最大极差）
    cross_district = queries.get('cross_district', {})
    if cross_district:
        sorted_districts = sorted(cross_district.items(),
                                key=lambda x: x[1]['spread_pct'],
                                reverse=True)
        lines.append('**区县价差 Top 5**：')
        for mid, info in sorted_districts[:5]:
            lines.append(f'- {info["material"]} / {info["spec"]}: '
                        f'{info["min_district"]} ({info["district_prices"][info["min_district"]]}) ↔ '
                        f'{info["max_district"]} ({info["district_prices"][info["max_district"]]}) '
                        f'极差 {info["spread_pct"]}%')
        lines.append('')

    # 数据质量问题
    lines.append('**数据质量提示**：')
    if any(r['excel_period_ref'] != r['period'] for r in manifest):
        lines.append('- Excel 元数据期号与文件名不符（已脱钩，仅供参考）')
    if any(r['excel_month_ref'] not in ('', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12')
           for r in manifest):
        lines.append('- 部分期数据月份异常（如 06 期 Excel 标 1 月）')
    lines.append('- 5 主城（锦江/青羊/金牛/武侯/成华）仅"成都市"综合行，单独无区县行')
    lines.append('')

    return '\n'.join(lines)


def section_one_data_inventory(manifest: list) -> str:
    """§1 数据盘点"""
    lines = []
    lines.append('## §1 数据盘点')
    lines.append('')
    lines.append(f'**总文件数**：{len(manifest)}')
    lines.append('')
    lines.append('| 文件 | 年份 | 期号 | 期号来源 | 行数 | Excel 参考期 | Excel 参考月 |')
    lines.append('|---|---|---|---|---|---|---|')
    total_rows = 0
    for m in manifest:
        fname = Path(m['file_path']).name
        rows = int(m.get('row_count', 0))
        total_rows += rows
        excel_p = m.get('excel_period_ref', '')
        excel_m = m.get('excel_month_ref', '')
        src = m.get('filename_period_source', '')
        match_p = m.get('period', '')
        # 检查 Excel ref vs 实际
        flag = ' ⚠️' if excel_p and excel_p != match_p else ''
        lines.append(f'| {fname[:40]} | {m.get("year","")} | {match_p} | {src}{flag} | {rows:,} | {excel_p} | {excel_m} |')
    lines.append('')
    lines.append(f'**总行数**：{total_rows:,}')
    lines.append('')
    return '\n'.join(lines)


def section_two_match_results(matches: list, pending: list) -> str:
    """§2 同物匹配结果"""
    lines = []
    lines.append('## §2 同物匹配结果')
    lines.append('')
    lines.append(f'**同物组数**：{len(matches):,}')
    lines.append(f'**待审池**：{len(pending):,} 条')
    lines.append('')

    # 按成员数分组
    member_dist = defaultdict(int)
    for m in matches:
        n = int(m.get('members', 1))
        member_dist[n] += 1
    lines.append('**成员数分布**：')
    for n in sorted(member_dist.keys())[:5]:
        lines.append(f'- {n} 成员: {member_dist[n]:,} 组')
    lines.append('')

    # 多成员合并组 Top 10
    merged = sorted(matches, key=lambda x: int(x.get('members', 1)), reverse=True)
    if merged and int(merged[0].get('members', 1)) > 1:
        lines.append('**多成员合并 Top 10**：')
        lines.append('')
        lines.append('| match_id | 材料 | 规格 | 成员数 | 跨期数 |')
        lines.append('|---|---|---|---|---|')
        for m in merged[:10]:
            lines.append(f'| {m["match_id"]} | {m["canonical_material"][:20]} | '
                        f'{m["canonical_spec"][:25]} | {m["members"]} | {m["period_count"]} |')
        lines.append('')

    # pending 类型分布
    if pending:
        reason_dist = defaultdict(int)
        for p in pending:
            reason_dist[p.get('reason', '')] += 1
        lines.append('**pending 原因分布**：')
        for reason, cnt in sorted(reason_dist.items(), key=lambda x: -x[1]):
            lines.append(f'- {reason}: {cnt}')
        lines.append('')

    return '\n'.join(lines)


def section_three_price_distribution(matches: list) -> str:
    """§3 价格分布"""
    lines = []
    lines.append('## §3 价格分布')
    lines.append('')
    lines.append('**Top 30 高价材料**：')
    lines.append('')
    lines.append('| 材料 | 规格 | 最低 | 最高 | 中位 |')
    lines.append('|---|---|---|---|---|')

    # 按 price_max 排序
    sorted_matches = sorted(
        [m for m in matches if m.get('price_max')],
        key=lambda x: float(x['price_max']),
        reverse=True,
    )
    for m in sorted_matches[:30]:
        lines.append(f'| {m["canonical_material"][:18]} | '
                    f'{m["canonical_spec"][:25]} | '
                    f'{m.get("price_min","")} | {m.get("price_max","")} | {m.get("example_price","")} |')
    lines.append('')
    return '\n'.join(lines)


def section_four_cross_period(queries: dict) -> str:
    """§4 跨期趋势"""
    lines = []
    lines.append('## §4 跨期趋势')
    lines.append('')

    cross_period = queries.get('cross_period', {})
    if not cross_period:
        lines.append('无跨期数据')
        return '\n'.join(lines)

    # 按波动排序
    sorted_cp = sorted(cross_period.items(),
                      key=lambda x: abs(x[1]['first_to_last_pct']),
                      reverse=True)

    lines.append('**价格波动 Top 20（同物 02→06 期变化）**：')
    lines.append('')
    lines.append('| 材料 | 规格 | 02 | 03 | 04 | 05 | 06 | 变化% | 方向 |')
    lines.append('|---|---|---|---|---|---|---|---|---|')
    for mid, info in sorted_cp[:20]:
        periods = info['periods']
        p02 = periods.get('02', '-')
        p03 = periods.get('03', '-')
        p04 = periods.get('04', '-')
        p05 = periods.get('05', '-')
        p06 = periods.get('06', '-')
        direction = '↑' if info['direction'] == 'up' else ('↓' if info['direction'] == 'down' else '→')
        lines.append(f'| {info["material"][:18]} | {info["spec"][:25]} | '
                    f'{p02} | {p03} | {p04} | {p05} | {p06} | '
                    f'{info["first_to_last_pct"]:+.2f}% | {direction} |')
    lines.append('')
    return '\n'.join(lines)


def section_five_idw(idw_rows: list, matches: list) -> str:
    """§5 区县空间插值（IDW）"""
    lines = []
    lines.append('## §5 区县空间插值（IDW）')
    lines.append('')

    n_known = sum(1 for r in idw_rows if r['type'] == 'known')
    n_est = sum(1 for r in idw_rows if r['type'] == 'estimated')
    n_un = sum(1 for r in idw_rows if r['type'] == 'unavailable')

    lines.append(f'**统计**：已知 {n_known:,} | 估算 {n_est:,} | 不可用 {n_un:,}')
    lines.append('')

    # 抽样 Top 10 同物组的 IDW 结果
    match_dict = {m['match_id']: m for m in matches}

    # 选 members>=2 且 IDW 有结果的 Top 10
    sample_match_ids = []
    for r in idw_rows:
        if r['type'] in ('known', 'estimated'):
            mid = r['match_id']
            if mid not in sample_match_ids and int(match_dict.get(mid, {}).get('members', 1)) >= 2:
                sample_match_ids.append(mid)
                if len(sample_match_ids) >= 10:
                    break

    if sample_match_ids:
        lines.append('**抽样：多成员合并组的 IDW 估算（按材料）**：')
        lines.append('')
        lines.append('| match_id | 材料 | 规格 | 已知区县 | 估算区县 | 估算示例 |')
        lines.append('|---|---|---|---|---|---|')
        for mid in sample_match_ids:
            mat = match_dict.get(mid, {})
            mat_name = mat.get('canonical_material', '')[:18]
            spec_name = mat.get('canonical_spec', '')[:25]
            rows_for_mid = [r for r in idw_rows if r['match_id'] == mid]
            known = [r['district'] for r in rows_for_mid if r['type'] == 'known']
            est = [r['district'] for r in rows_for_mid if r['type'] == 'estimated']
            est_sample = next((r for r in rows_for_mid if r['type'] == 'estimated'), None)
            est_str = f"{est_sample['district']}={est_sample['price']}" if est_sample else '-'
            lines.append(f'| {mid} | {mat_name} | {spec_name} | '
                        f'{len(known)} | {len(est)} | {est_str} |')
        lines.append('')
        lines.append('**说明**：')
        lines.append('- **已知**：直接来自该区县的 xlsx 数据')
        lines.append('- **估算**：基于 ≥3 个已知区县，按 IDW 距离权重计算')
        lines.append('- **不可用**：已知区县 < 3，IDW 无法计算')
        lines.append('')

    return '\n'.join(lines)


def section_six_pending(pending: list, idw_rows: list) -> str:
    """§6 待人工复核项"""
    lines = []
    lines.append('## §6 待人工复核项')
    lines.append('')

    # 6.1 pending_pool
    lines.append(f'### 6.1 同物匹配待审（{len(pending)} 条）')
    lines.append('')
    if pending:
        lines.append('**抽样（前 10 条）**：')
        lines.append('')
        lines.append('| p_id | Jaccard | 材料提示 | spec_a | spec_b | 原因 |')
        lines.append('|---|---|---|---|---|---|')
        for p in pending[:10]:
            lines.append(f'| {p["pending_id"]} | {p["jaccard"]} | '
                        f'{p["material_hint"][:20]} | {p["spec_a"][:20]} | '
                        f'{p["spec_b"][:20]} | {p["reason"]} |')
        lines.append('')
        lines.append(f'完整列表见：`5_日志/pending_pool.csv`')
        lines.append(f'审核命令：`python 6_脚本/phase2_5_pending.py --interactive`')
        lines.append('')

    # 6.2 异常价格
    lines.append('### 6.2 异常价格（同物跨期极差 > 10%）')
    lines.append('')
    # 这里复用 cross_period
    # 但我们没传进来，重新算
    lines.append('见 §4 跨期趋势表中"变化%"列绝对值 > 10% 的行')
    lines.append('')

    # 6.3 缺失数据
    lines.append('### 6.3 缺失数据')
    lines.append('')
    lines.append('- 5 主城（锦江/青羊/金牛/武侯/成华）单独区县行缺失 → IDW 用综合行兜底')
    lines.append('- 部分材料仅 1-2 个区县有数据 → IDW 不可用')
    lines.append('- Excel 元数据"数据月份"错位（06 期 Excel 标 1 月，05 期标 5 月）→ 仅展示')
    lines.append('')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Phase 4 md 总报告')
    parser.add_argument('--manifest', default=str(MANIFEST_PATH))
    parser.add_argument('--matches', default=str(MATCHES_PATH))
    parser.add_argument('--pending', default=str(PENDING_PATH))
    parser.add_argument('--idw', default=str(IDW_PATH))
    parser.add_argument('--queries', default=str(QUERIES_PATH))
    parser.add_argument('--output-dir', default=str(OUTPUT_DIR))
    parser.add_argument('--filter', default='', help='材料过滤关键词，逗号分隔（如 砖,混凝土,苗木），透传给 phase4_report_v3.py')
    args = parser.parse_args()

    # 加载数据
    print('加载数据...')
    manifest = load_csv(Path(args.manifest))
    matches = load_csv(Path(args.matches))
    pending = load_csv(Path(args.pending))
    idw_rows = load_csv(Path(args.idw))
    with open(args.queries, 'r', encoding='utf-8') as f:
        queries = json.load(f)

    print(f'  manifest: {len(manifest)} 文件')
    print(f'  matches: {len(matches)} 同物组')
    print(f'  pending: {len(pending)} 待审')
    print(f'  idw: {len(idw_rows)} 行')

    # 生成各节
    print('\n生成报告各节...')
    sections = []
    sections.append('# 信息价分析报告')
    sections.append('')

    # 报告元数据
    periods = sorted(set(m['period'] for m in manifest))
    cities = set(m['city'] for m in manifest if m.get('city'))
    city = ','.join(cities) if cities else '未知'
    period_range = f"{periods[0]}-{periods[-1]}期" if periods else "?"
    sections.append(f'> 城市：{city} | 期数：{period_range} | '
                   f'文件：{len(manifest)} | 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}')
    sections.append('')
    sections.append('---')
    sections.append('')

    sections.append(section_zero_summary(manifest, matches, pending, idw_rows, queries))
    sections.append('---')
    sections.append('')

    sections.append(section_one_data_inventory(manifest))
    sections.append('---')
    sections.append('')

    sections.append(section_two_match_results(matches, pending))
    sections.append('---')
    sections.append('')

    sections.append(section_three_price_distribution(matches))
    sections.append('---')
    sections.append('')

    sections.append(section_four_cross_period(queries))
    sections.append('---')
    sections.append('')

    sections.append(section_five_idw(idw_rows, matches))
    sections.append('---')
    sections.append('')

    sections.append(section_six_pending(pending, idw_rows))
    sections.append('')
    sections.append('---')
    sections.append('')
    sections.append(f'**报告生成时间**：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    sections.append(f'**报告路径**：`4_输出/reports/`')
    sections.append(f'**项目路径**：`{PROJECT_ROOT}/`')

    # 写文件
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = output_dir / f'{city}_{period_range}_report_{timestamp}.md'

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sections))

    print(f'\n✓ md 报告已写: {report_path}')
    print(f'  大小: {report_path.stat().st_size:,} bytes')

    # ── xlsx 输出（2026-08-18: 委托 phase4_report_v3.py，sheet 名按 phase3 原样不分类）────
    import subprocess
    period_arg = ",".join(periods) if periods else "02"
    v3_script = Path(__file__).parent / "phase4_report_v3.py"
    v3_cmd = [sys.executable, str(v3_script), "--period", period_arg]
    if args.filter:
        v3_cmd += ["--filter", args.filter]
    try:
        result = subprocess.run(
            v3_cmd,
            capture_output=True, text=True, encoding='utf-8',
            cwd=str(PROJECT_ROOT),
            timeout=300,
        )
        if result.returncode == 0:
            print(f'\n✓ xlsx 报告已由 phase4_report_v3.py 生成（--period {period_arg}{" --filter "+args.filter if args.filter else ""}）')
            print(f'  stdout: {result.stdout.strip().splitlines()[-3:]}')
        else:
            print(f'\n[WARN] phase4_report_v3.py 返回 {result.returncode}')
            print(f'  stderr: {result.stderr[-500:]}')
    except Exception as e:
        print(f'\n[WARN] phase4_report_v3.py 调用失败: {e}')

    # 预览 §0
    print(f'\n=== 报告 §0 预览 ===')
    for line in sections[6:30]:
        print(line)


if __name__ == '__main__':
    main()
