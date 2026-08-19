"""
phase3_query.py - Phase 3 4 类查询 + IDW 空间插值

4 类查询（项目.md §3 决策 8 + §10 §5）：
  1. 价格趋势（跨期）
  2. 横向对比（跨区县）
  3. 同物跨期（同物不同期的价格变化）
  4. 空间插值（IDW）

输入:
  3_中间产物/matches.csv
  3_中间产物/manifest.csv
  coords/chengdu_districts.yaml

输出:
  3_中间产物/idw_estimates.csv   ← IDW 估算（每材料 × 每区县）
  3_中间产物/query_results.json   ← 4 类查询结果
"""

import argparse
import csv
import json
import math
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import openpyxl
import yaml

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PROJECT_ROOT / "3_中间产物" / "manifest.csv"
COLUMN_MAP_PATH = PROJECT_ROOT / "3_中间产物" / "column_mapping.csv"
MATCHES_PATH = PROJECT_ROOT / "3_中间产物" / "matches.csv"
COORDS_PATH = PROJECT_ROOT / "coords" / "chengdu_districts.yaml"
OUTPUT_IDW = PROJECT_ROOT / "3_中间产物" / "idw_estimates.csv"
OUTPUT_QUERIES = PROJECT_ROOT / "3_中间产物" / "query_results.json"


def haversine(lat1, lon1, lat2, lon2) -> float:
    """
    Haversine 距离（km）
    """
    R = 6371.0  # 地球半径 km
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def idw_estimate(known_points: list, target_lat: float, target_lon: float, p: int = 2) -> float | None:
    """
    IDW 插值
    known_points: [(lat, lon, price), ...]
    返回估算价 或 None（无足够数据）
    """
    if len(known_points) < 3:
        return None  # 至少 3 个已知点

    weights = []
    prices = []
    for lat, lon, price in known_points:
        d = haversine(target_lat, target_lon, lat, lon)
        if d < 0.001:
            # 距离太近，直接用该价
            return price
        w = 1.0 / (d ** p)
        weights.append(w)
        prices.append(price)

    w_sum = sum(weights)
    if w_sum == 0:
        return None
    return sum(w * pr for w, pr in zip(weights, prices)) / w_sum


def load_all_prices(manifest_path: Path, column_map_path: Path) -> list:
    """
    加载所有 xlsx 的价格数据
    返回: list of {match_id, file, period, district, price, material_raw, spec_raw}
    """
    # 读 manifest
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest_rows = list(csv.DictReader(f))

    # 读 column_mapping
    with open(column_map_path, 'r', encoding='utf-8') as f:
        column_rows = {r['file_path']: r for r in csv.DictReader(f)}

    # 读 matches
    with open(MATCHES_PATH, 'r', encoding='utf-8') as f:
        matches = list(csv.DictReader(f))

    # 用 (material_raw, spec_raw) → match_id 索引
    match_lookup = {}
    for m in matches:
        key = (m['canonical_material'], m['canonical_spec'])
        match_lookup[key] = m['match_id']

    all_prices = []
    for m_row in manifest_rows:
        fp = m_row['file_path']
        period = m_row['period']
        col_map = column_rows[fp]

        def col_idx(col_name: str) -> int:
            if not col_name or not col_name.startswith('col_'):
                return -1
            return int(col_name.split('_')[1]) - 1

        idx_district = col_idx(col_map['district_col'])
        idx_material = col_idx(col_map['material_col'])
        idx_spec = col_idx(col_map['spec_col'])
        idx_price = col_idx(col_map['price_excl_tax_col'])

        wb = openpyxl.load_workbook(fp, data_only=True, read_only=True)
        ws = wb[col_map['sheet_name']]
        header_row = int(col_map['header_row'])

        for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
            mat_raw = row[idx_material] if idx_material >= 0 else ''
            spec_raw = row[idx_spec] if idx_spec >= 0 else ''
            district = row[idx_district] if idx_district >= 0 else ''
            price_raw = row[idx_price] if idx_price >= 0 else None

            try:
                price = float(str(price_raw).strip()) if price_raw else None
            except (ValueError, TypeError):
                price = None

            if not mat_raw or price is None:
                continue

            # 找 match_id（用 canonical 反向）
            # 注：这里用 raw 作 key 是不精确的，但因为材料名重复多，应该用 groupby 后再算
            all_prices.append({
                'file': Path(fp).name,
                'period': period,
                'district': str(district) if district else '',
                'material_raw': str(mat_raw),
                'spec_raw': str(spec_raw) if spec_raw else '',
                'price': price,
            })

        wb.close()

    return all_prices


def group_by_match(all_prices: list, matches: list) -> dict:
    """
    把 all_prices 按 match_id 分组
    用 (canonical_material, canonical_spec) 反向匹配
    注：这里做 fuzzy（让"普通商品砼"和"普通商品混凝土C30"归到同一 match_id）
    """
    # 用最常见写法匹配法
    # 简化版：用 material_norm + spec_norm 分组

    def normalize(s):
        if not s:
            return ''
        s = unicodedata.normalize('NFKC', str(s))
        s = s.replace('　', '').replace(' ', '').replace('\t', '')
        s = s.lower()
        import re
        s = re.sub(r'c\d+$', '', s)  # 去末尾 C 等级
        return s

    # match 索引：(mat_norm, spec_norm) → match_id
    match_lookup = {}
    for m in matches:
        mat_norm = normalize(m['canonical_material'])
        spec_norm = normalize(m['canonical_spec'])
        match_lookup[(mat_norm, spec_norm)] = m['match_id']

    # 分组
    grouped = defaultdict(list)
    unmatched = 0
    for row in all_prices:
        mat_norm = normalize(row['material_raw'])
        spec_norm = normalize(row['spec_raw'])
        key = (mat_norm, spec_norm)
        if key in match_lookup:
            match_id = match_lookup[key]
            row['match_id'] = match_id
            grouped[match_id].append(row)
        else:
            unmatched += 1
    print(f'  分组: {len(grouped)} 个 match_id 有数据')
    print(f'  未匹配: {unmatched} 行')

    return grouped


def compute_idw(grouped: dict, coords: dict) -> list:
    """
    对每个 match_id × 每个区县做 IDW 估算
    返回: list of {match_id, district, price, type, confidence, source_districts}
    """
    estimates = []

    for match_id, rows in grouped.items():
        # 1. 各区县中位数价格（先按 raw 聚合，再标准化为 coords key）
        district_prices = defaultdict(list)
        for r in rows:
            if r['district']:
                # 标准化：去 "成都市" 前缀
                clean = r['district'].replace('成都市', '').strip()
                if clean == '':  # 综合行
                    clean = '__CITY_SUM__'
                district_prices[clean].append(r['price'])

        # 2. 计算中位数
        district_median = {}
        for d, prices in district_prices.items():
            prices.sort()
            n = len(prices)
            if n % 2 == 0:
                median = (prices[n//2 - 1] + prices[n//2]) / 2
            else:
                median = prices[n//2]
            district_median[d] = median

        # 3. 对每个有坐标的区县，算 IDW
        known_points = []
        for d, price in district_median.items():
            if d == '__CITY_SUM__':
                # 综合行，用 5 主城平均坐标
                avg_lat = sum(coords[m]['lat'] for m in ['锦江区', '青羊区', '金牛区', '武侯区', '成华区']) / 5
                avg_lon = sum(coords[m]['lon'] for m in ['锦江区', '青羊区', '金牛区', '武侯区', '成华区']) / 5
                known_points.append((avg_lat, avg_lon, price, '成都市'))
            elif d in coords:
                lat = coords[d]['lat']
                lon = coords[d]['lon']
                known_points.append((lat, lon, price, d))

        # 4. 输出所有区县（已知 + 估算）
        all_districts = set(coords.keys()) | set(district_median.keys())
        for target_district in sorted(all_districts):
            if target_district not in coords:
                continue  # 跳过无坐标的

            target_lat = coords[target_district]['lat']
            target_lon = coords[target_district]['lon']

            # 是否已知
            is_known = target_district in district_median

            if is_known:
                estimates.append({
                    'match_id': match_id,
                    'district': target_district,
                    'price': round(district_median[target_district], 2),
                    'type': 'known',
                    'confidence': 'high',
                    'source': f"基于{len(district_prices[target_district])}个数据点中位数",
                    'distance_km': 0.0,
                })
            else:
                # IDW 估算
                estimate = idw_estimate(
                    [(lat, lon, pr) for lat, lon, pr, _ in known_points],
                    target_lat, target_lon,
                )
                if estimate is None:
                    estimates.append({
                        'match_id': match_id,
                        'district': target_district,
                        'price': '',
                        'type': 'unavailable',
                        'confidence': 'low',
                        'source': '已知区县 < 3，IDW 不可用',
                        'distance_km': 0.0,
                    })
                else:
                    # 找最近 3 个已知区县作为参考
                    dists = []
                    for lat, lon, pr, name in known_points:
                        d = haversine(target_lat, target_lon, lat, lon)
                        dists.append((d, name, pr))
                    dists.sort()
                    top3 = dists[:3]
                    conf = 'high' if len(known_points) >= 5 else 'medium'
                    estimates.append({
                        'match_id': match_id,
                        'district': target_district,
                        'price': round(estimate, 2),
                        'type': 'estimated',
                        'confidence': conf,
                        'source': f"IDW 基于 {','.join(d[1] for d in top3)}",
                        'distance_km': round(top3[0][0], 1),
                    })

    return estimates


def query_cross_period(grouped: dict) -> dict:
    """查询 3: 同物跨期趋势"""
    result = {}
    for match_id, rows in grouped.items():
        # 按 period 聚合
        period_prices = defaultdict(list)
        for r in rows:
            period_prices[r['period']].append(r['price'])
        # 计算每期中位数
        period_median = {}
        for p, prices in sorted(period_prices.items()):
            prices.sort()
            n = len(prices)
            if n % 2 == 0:
                median = (prices[n//2 - 1] + prices[n//2]) / 2
            else:
                median = prices[n//2]
            period_median[p] = round(median, 2)

        if len(period_median) >= 2:
            # 算波动
            values = list(period_median.values())
            max_v = max(values)
            min_v = min(values)
            volatility = round((max_v - min_v) / min_v * 100, 2) if min_v > 0 else 0

            # 跨期方向
            first_p = sorted(period_median.keys())[0]
            last_p = sorted(period_median.keys())[-1]
            trend_pct = round((period_median[last_p] - period_median[first_p]) / period_median[first_p] * 100, 2) if period_median[first_p] > 0 else 0

            material_name = rows[0]['material_raw'][:20]
            spec_name = rows[0]['spec_raw'][:20]
            result[match_id] = {
                'material': material_name,
                'spec': spec_name,
                'periods': period_median,
                'volatility_pct': volatility,
                'first_to_last_pct': trend_pct,
                'direction': 'up' if trend_pct > 0 else ('down' if trend_pct < 0 else 'flat'),
            }
    return result


def query_cross_district(grouped: dict) -> dict:
    """查询 2: 横向对比（每材料跨区县）"""
    result = {}
    for match_id, rows in grouped.items():
        district_prices = defaultdict(list)
        for r in rows:
            if r['district'] and r['district'] != '成都市':
                # 标准化
                clean = r['district'].replace('成都市', '').strip()
                if clean:
                    district_prices[clean].append(r['price'])

        if len(district_prices) >= 2:
            # 计算每区县中位数
            district_median = {}
            for d, prices in district_prices.items():
                prices.sort()
                n = len(prices)
                median = (prices[n//2 - 1] + prices[n//2]) / 2 if n % 2 == 0 else prices[n//2]
                district_median[d] = round(median, 2)

            values = list(district_median.values())
            max_v = max(values)
            min_v = min(values)
            spread_pct = round((max_v - min_v) / min_v * 100, 2) if min_v > 0 else 0

            result[match_id] = {
                'material': rows[0]['material_raw'][:20],
                'spec': rows[0]['spec_raw'][:20],
                'district_count': len(district_median),
                'min_district': min(district_median, key=district_median.get),
                'max_district': max(district_median, key=district_median.get),
                'spread_pct': spread_pct,
                'district_prices': district_median,
            }
    return result


def main():
    parser = argparse.ArgumentParser(description='Phase 3 4 类查询 + IDW')
    parser.add_argument('--manifest', default=str(MANIFEST_PATH))
    parser.add_argument('--column-map', default=str(COLUMN_MAP_PATH))
    parser.add_argument('--coords', default=str(COORDS_PATH))
    args = parser.parse_args()

    # 1. 加载坐标
    print('加载坐标...')
    with open(args.coords, 'r', encoding='utf-8') as f:
        coords_data = yaml.safe_load(f)
    coords = coords_data['districts']
    print(f'  {len(coords)} 个区县坐标')

    # 2. 加载所有价格
    print('\n加载所有 xlsx 价格数据...')
    all_prices = load_all_prices(Path(args.manifest), Path(args.column_map))
    print(f'  {len(all_prices)} 行有效价格')

    # 3. 读 matches
    with open(MATCHES_PATH, 'r', encoding='utf-8') as f:
        matches = list(csv.DictReader(f))
    print(f'  {len(matches)} 个 match_id')

    # 4. 按 match_id 分组
    print('\n按 match_id 分组...')
    grouped = group_by_match(all_prices, matches)

    # 5. IDW 估算
    print('\nIDW 估算...')
    estimates = compute_idw(grouped, coords)

    # 6. 写 idw_estimates.csv
    OUTPUT_IDW.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_IDW, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'match_id', 'district', 'price', 'type', 'confidence',
            'source', 'distance_km',
        ])
        writer.writeheader()
        for row in estimates:
            writer.writerow(row)
    print(f'✓ idw_estimates.csv: {len(estimates)} 行')

    # 统计 IDW 估算数
    n_known = sum(1 for e in estimates if e['type'] == 'known')
    n_est = sum(1 for e in estimates if e['type'] == 'estimated')
    n_un = sum(1 for e in estimates if e['type'] == 'unavailable')
    print(f'  已知: {n_known}, 估算: {n_est}, 不可用: {n_un}')

    # 7. 4 类查询
    print('\n4 类查询...')
    cross_period = query_cross_period(grouped)
    cross_district = query_cross_district(grouped)

    # 8. 写 query_results.json
    query_results = {
        'cross_period': cross_period,
        'cross_district': cross_district,
        'summary': {
            'total_matches': len(matches),
            'matches_with_prices': len(grouped),
            'cross_period_count': len(cross_period),
            'cross_district_count': len(cross_district),
            'idw_estimates': len(estimates),
            'idw_known': n_known,
            'idw_estimated': n_est,
            'idw_unavailable': n_un,
        },
    }
    with open(OUTPUT_QUERIES, 'w', encoding='utf-8') as f:
        json.dump(query_results, f, ensure_ascii=False, indent=2)
    print(f'✓ query_results.json: {OUTPUT_QUERIES}')

    # 抽样展示
    print(f'\n=== 跨期趋势 (前 5 条) ===')
    for i, (mid, info) in enumerate(list(cross_period.items())[:5]):
        print(f'  {mid}: {info["material"]} / {info["spec"]}')
        print(f'    期号价格: {info["periods"]}')
        print(f'    波动: {info["volatility_pct"]}%, 方向: {info["direction"]} ({info["first_to_last_pct"]}%)')

    print(f'\n=== 横向对比 (前 5 条) ===')
    for i, (mid, info) in enumerate(list(cross_district.items())[:5]):
        print(f'  {mid}: {info["material"]} / {info["spec"]}')
        print(f'    区县数: {info["district_count"]}, 极差: {info["min_district"]} ({info["district_prices"][info["min_district"]]}) ↔ {info["max_district"]} ({info["district_prices"][info["max_district"]]})')

    print(f'\n=== Phase 3 完成 ===')


if __name__ == '__main__':
    main()
