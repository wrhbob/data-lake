"""
phase2_match.py - Phase 2 多期合并 + 同义词库

算法流程（项目.md §2 核心规则 + §3 数据坑）：
  1. 加载所有 xlsx 数据（用 manifest + column_mapping）
  2. 字段归一化（NFKC + 去空格 + 单位同义）
  3. 同物匹配：
     - Jaccard ≥ 0.95 → 同物
     - 0.85 ≤ Jaccard < 0.95 → 进 pending_pool（人审）
     - < 0.85 → 不同物
  4. 输出 matches.csv + pending_pool.csv

输入:
  3_中间产物/manifest.csv
  3_中间产物/column_mapping.csv
  2_库/unit_synonyms.yaml
  2_库/material_synonyms.yaml
  2_库/known_different.yaml

输出:
  3_中间产物/matches.csv        ← 同物组
  5_日志/pending_pool.csv       ← 待审池
"""

import argparse
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import openpyxl
import yaml

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PROJECT_ROOT / "3_中间产物" / "manifest.csv"
COLUMN_MAP_PATH = PROJECT_ROOT / "3_中间产物" / "column_mapping.csv"
LIBRARY_DIR = PROJECT_ROOT / "2_库"
OUTPUT_MATCHES = PROJECT_ROOT / "3_中间产物" / "matches.csv"
PENDING_POOL_PATH = PROJECT_ROOT / "5_日志" / "pending_pool.csv"

# Jaccard 阈值（项目.md §4 决策 5/6）
JACCARD_SAME = 0.95
JACCARD_PENDING = 0.85

# pending_pool.csv 列（项目.md §9 共 13 列）
PENDING_COLUMNS = [
    'pending_id', 'file_a', 'spec_a', 'file_b', 'spec_b',
    'jaccard', 'material_hint', 'reason', 'decision',
    'decided_at', 'user_note', 'writeback_target', 'writeback_done',
]

MATCH_COLUMNS = [
    'match_id', 'canonical_material', 'canonical_spec',
    'members', 'file_count', 'period_count',
    'example_price', 'price_min', 'price_max',
]


def normalize_text(s: str) -> str:
    """
    文本归一化（项目.md §3 坑 3）
    1. NFKC Unicode 归一化
    2. 去空格（包括全角空格）
    3. 大小写统一（小写）
    4. 去掉末尾的 C 等级 (C10/C15/C20/.../C60)，避免把强度等级算进材料名
       （让 spec 单独承载 C 等级）
    5. 剥掉运费注（"含三环路以内工地运输费" 等）
       例: "C30碎石粒径5-31.5mm含三环路以内工地运输费" → "碎石粒径5-31.5mm"
       让同物不同期写法（带/不带运输费注）合并
    """
    if not s:
        return ''
    s = unicodedata.normalize('NFKC', str(s))
    s = s.replace('　', '').replace(' ', '').replace('\t', '')
    s = s.lower()
    # 2026-08-18: 容错 OCR "碎石?粒径" → "碎石粒径"（phase3 OCR 漏"石"字写"碎粒径"，导致 C20-C50 同物匹配不到独立 match_id）
    s = re.sub(r'碎石?粒径', '碎石粒径', s)
    # 去掉末尾的 C\d+ (c30, c10, c15 等)
    s = re.sub(r'c\d+$', '', s)
    # 剥运费注：必须以"含"/"不含"开头，前面不能是数字（避免吃掉"5-31.5mm含..."中间的5）
    s = re.sub(r'(?<![0-9])[含不含][^()\d]{0,20}?运输费', '', s)
    return s


def has_conflicting_numbers(spec_a: str, spec_b: str) -> bool:
    """
    数字强保护（项目.md §2 规则 2 通用版）
    两个 spec 的纯数字集合如果不同，判为不同物
    例:
      "C30碎石粒径5-31.5mm" vs "C20碎石粒径5-31.5mm" → 数字集 {30} vs {20} 不同 → 跳过
      "DN2000*2000" vs "DN2200*2000" → 数字集 {2000} vs {2200, 2000} 不同 → 跳过
      "Φ300mm" vs "Φ400mm" → 数字集 {300} vs {400} 不同 → 跳过
    返回 True 表示冲突（不同物）
    """
    nums_a = set(re.findall(r'\d+', spec_a or ''))
    nums_b = set(re.findall(r'\d+', spec_b or ''))
    if not nums_a or not nums_b:
        return False
    return nums_a != nums_b


# 保留旧名以兼容（已替换所有调用）
has_conflicting_grade = has_conflicting_numbers


def load_yaml(path: Path) -> dict:
    """读 yaml 库（不存在返回空 dict）"""
    if not path.exists():
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def jaccard(s1: str, s2: str) -> float:
    """
    Jaccard 相似度（基于字符 bigram）
    """
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0
    # 字符 bigram
    def bigrams(s):
        return set(s[i:i+2] for i in range(len(s) - 1))

    b1 = bigrams(s1)
    b2 = bigrams(s2)
    if not b1 or not b2:
        return 0.0
    intersection = b1 & b2
    union = b1 | b2
    return len(intersection) / len(union)


def apply_synonyms(text: str, synonym_map: dict) -> str:
    """
    应用同义词替换
    synonym_map: {标准词: [变体1, 变体2, ...]}

    修复: 先按变体长度降序替换，避免短变体先匹配破坏长变体
    例: "普通商品混凝土" 中 "商品混凝土" 应优先于 "混凝土" 匹配
    """
    if not text or not synonym_map:
        return text

    # 收集所有变体 + 标准词，按长度降序
    all_replacements = []
    for canonical, variants in synonym_map.items():
        for variant in variants:
            if variant:
                all_replacements.append((variant, canonical))
    # 长度降序：长的先匹配
    all_replacements.sort(key=lambda x: -len(x[0]))

    normalized = text
    for variant, canonical in all_replacements:
        if variant and variant in normalized:
            normalized = normalized.replace(variant, canonical)
    return normalized


def normalize_unit(unit: str, unit_synonyms: dict) -> str:
    """单位归一化（含 unit_synonyms.yaml）"""
    if not unit:
        return ''
    normalized = normalize_text(unit)
    # 应用同义词（unit_synonyms.yaml 格式: {标准: [变体]})
    normalized = apply_synonyms(normalized, unit_synonyms)
    return normalized


def load_all_rows(manifest_path: Path, column_map_path: Path, unit_synonyms: dict):
    """
    加载所有 xlsx 行（按 manifest + column_mapping）
    返回: list of dict
      {file, period, material_raw, material_norm, spec_raw, spec_norm,
       unit_raw, unit_norm, district, price_raw, price (float)}
    """
    # 读 manifest
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest_rows = list(csv.DictReader(f))

    # 读 column_mapping
    with open(column_map_path, 'r', encoding='utf-8') as f:
        column_rows = {r['file_path']: r for r in csv.DictReader(f)}

    all_rows = []
    for m_row in manifest_rows:
        fp = m_row['file_path']
        period = m_row['period']
        col_map = column_rows[fp]

        # 列号解析: col_1 → index 0
        def col_idx(col_name: str) -> int:
            if not col_name or not col_name.startswith('col_'):
                return -1
            return int(col_name.split('_')[1]) - 1

        idx_district = col_idx(col_map['district_col'])
        idx_material = col_idx(col_map['material_col'])
        idx_spec = col_idx(col_map['spec_col'])
        idx_unit = col_idx(col_map['unit_col'])
        # 优先用除税价（唯一填数据的）
        idx_price = col_idx(col_map['price_excl_tax_col'])
        if idx_price < 0:
            idx_price = col_idx(col_map['price_with_tax_col'])

        wb = openpyxl.load_workbook(fp, data_only=True, read_only=True)
        ws = wb[col_map['sheet_name']]
        header_row = int(col_map['header_row'])

        for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
            mat_raw = row[idx_material] if idx_material >= 0 else ''
            if not mat_raw:
                continue
            spec_raw = row[idx_spec] if idx_spec >= 0 else ''
            unit_raw = row[idx_unit] if idx_unit >= 0 else ''
            district = row[idx_district] if idx_district >= 0 else ''
            price_raw = row[idx_price] if idx_price >= 0 else None

            # 价格转 float
            try:
                price = float(str(price_raw).strip()) if price_raw else None
            except (ValueError, TypeError):
                price = None

            all_rows.append({
                'file': Path(fp).name,
                'period': period,
                'district': str(district) if district else '',
                'material_raw': str(mat_raw),
                'material_norm': normalize_text(mat_raw),
                'spec_raw': str(spec_raw) if spec_raw else '',
                'spec_norm': normalize_text(spec_raw),
                'unit_raw': str(unit_raw) if unit_raw else '',
                'unit_norm': normalize_unit(str(unit_raw), unit_synonyms),
                'price_raw': price_raw,
                'price': price,
            })

        wb.close()

    return all_rows


def group_by_canonical(rows: list, material_synonyms: dict) -> dict:
    """
    按 (material_canonical, spec_canonical) 分组
    material_synonyms: {标准: [变体]}
    """
    groups = defaultdict(list)
    for row in rows:
        # 应用 material_synonyms
        canonical = apply_synonyms(row['material_norm'], material_synonyms)
        # spec 也归一化（含区间规格暂不展开，只去空格+小写）
        key = (canonical, row['spec_norm'])
        groups[key].append(row)
    return groups


def find_pending_matches(groups: dict, known_different: set) -> tuple:
    """
    在不同组之间做 Jaccard 匹配，找 pending
    优化：只比对 material_norm 接近的组（避免完全不同的材料互相比）
    返回 (matches, pending_rows)
    """
    # 把 group key 转 list
    keys = list(groups.keys())
    print(f'  共 {len(keys)} 个 (material, spec) 组')

    # 已知不同物：按 material 索引加速
    # known_different_material: {material_a: set of material_b}
    known_diff_mat = defaultdict(set)
    for entry in known_different:
        if len(entry) == 4:
            known_diff_mat[entry[0]].add(entry[2])
            known_diff_mat[entry[2]].add(entry[0])

    # 并查集
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # 按 material_norm 索引，加速比对
    by_material = defaultdict(list)
    for k in keys:
        by_material[k[0]].append(k)

    pending = []
    compare_count = 0

    # 第一轮：精确 material_norm 完全相同的组之间比较 spec
    for mat_norm, group_list in by_material.items():
        if len(group_list) < 2:
            continue
        # 同 material_norm 内，spec 两两比
        for k1, k2 in combinations(group_list, 2):
            compare_count += 1
            if find(k1) == find(k2):
                continue
            # 数字强保护：纯数字集不同直接跳过（不同物）
            if has_conflicting_numbers(k1[1], k2[1]):
                continue
            spec_j = jaccard(k1[1], k2[1])
            if spec_j >= JACCARD_SAME:
                union(k1, k2)
            elif JACCARD_PENDING <= spec_j < JACCARD_SAME:
                pending.append({
                    'pending_id': f'p{len(pending)+1:04d}',
                    'file_a': groups[k1][0]['file'],
                    'spec_a': groups[k1][0]['spec_raw'],
                    'file_b': groups[k2][0]['file'],
                    'spec_b': groups[k2][0]['spec_raw'],
                    'jaccard': round(spec_j, 3),
                    'material_hint': groups[k1][0]['material_raw'][:30],
                    'reason': '同材料不同规格模糊匹配',
                    'decision': '',
                    'decided_at': '',
                    'user_note': '',
                    'writeback_target': 'material_synonyms.yaml',
                    'writeback_done': False,
                })

    # 第二轮：material_norm 接近（Jaccard 0.5+）的组之间比较
    # 用 sorted keys 做粗筛，避免 O(n²)
    unique_mats = list(by_material.keys())
    print(f'  第一轮完成: {compare_count} 比对, {len(pending)} pending')
    print(f'  第二轮: {len(unique_mats)} 个 material_norm 之间比')

    for i, mat_a in enumerate(unique_mats):
        for mat_b in unique_mats[i+1:]:
            # 已知不同物跳过
            if mat_b in known_diff_mat.get(mat_a, set()):
                continue
            mat_j = jaccard(mat_a, mat_b)
            # material Jaccard 太低，完全不同材料，跳过
            if mat_j < 0.5:
                continue

            # material 接近：跨 material 比 spec
            for k1 in by_material[mat_a]:
                for k2 in by_material[mat_b]:
                    compare_count += 1
                    if find(k1) == find(k2):
                        continue
                    # 数字强保护：含不同 C 等级直接跳过
                    if has_conflicting_grade(k1[1], k2[1]):
                        continue
                    spec_j = jaccard(k1[1], k2[1])
                    if mat_j >= JACCARD_SAME and spec_j >= JACCARD_SAME:
                        union(k1, k2)
                    elif JACCARD_PENDING <= mat_j < JACCARD_SAME:
                        pending.append({
                            'pending_id': f'p{len(pending)+1:04d}',
                            'file_a': groups[k1][0]['file'],
                            'spec_a': groups[k1][0]['spec_raw'],
                            'file_b': groups[k2][0]['file'],
                            'spec_b': groups[k2][0]['spec_raw'],
                            'jaccard': round(mat_j, 3),
                            'material_hint': groups[k1][0]['material_raw'][:30],
                            'reason': '材料名模糊匹配',
                            'decision': '',
                            'decided_at': '',
                            'user_note': '',
                            'writeback_target': 'material_synonyms.yaml',
                            'writeback_done': False,
                        })
                    break  # 每个 k1 只对 mat_b 的第一个 k2 比一次，避免爆炸

    print(f'  共 {compare_count} 比对')

    # 收集并查集结果
    final_groups = defaultdict(list)
    for k in keys:
        root = find(k)
        final_groups[root].append(k)

    return final_groups, pending


def main():
    parser = argparse.ArgumentParser(description='Phase 2 多期合并 + 同义词库')
    parser.add_argument('--manifest', default=str(MANIFEST_PATH))
    parser.add_argument('--column-map', default=str(COLUMN_MAP_PATH))
    args = parser.parse_args()

    # 1. 加载同义词库
    print('加载同义词库...')
    unit_synonyms = load_yaml(LIBRARY_DIR / 'unit_synonyms.yaml')
    material_synonyms = load_yaml(LIBRARY_DIR / 'material_synonyms.yaml')
    known_different_data = load_yaml(LIBRARY_DIR / 'known_different.yaml')
    # known_different: list of [mat_a, spec_a, mat_b, spec_b] 或 [mat_a, mat_b]
    known_different = set()
    for entry in known_different_data.get('pairs', []):
        if len(entry) == 4:
            known_different.add(tuple(entry))
    print(f'  unit_synonyms: {sum(len(v) for v in unit_synonyms.values())} 变体')
    print(f'  material_synonyms: {sum(len(v) for v in material_synonyms.values())} 变体')
    print(f'  known_different: {len(known_different)} 对')

    # 2. 加载所有行
    print('\n加载所有 xlsx 行...')
    rows = load_all_rows(Path(args.manifest), Path(args.column_map), unit_synonyms)
    print(f'  共 {len(rows)} 行')

    # 3. 按 (material, spec) 分组
    print('\n按 (material, spec) 分组...')
    groups = group_by_canonical(rows, material_synonyms)

    # 4. 同物匹配 + pending
    print('\n同物匹配 + pending 识别...')
    final_groups, pending = find_pending_matches(groups, known_different)

    # 5. 写 matches.csv
    print(f'\n合并后共 {len(final_groups)} 个同物组')
    OUTPUT_MATCHES.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_MATCHES, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=MATCH_COLUMNS)
        writer.writeheader()
        for i, (root, members) in enumerate(final_groups.items(), 1):
            # 合并所有 member 的行
            all_member_rows = []
            for m in members:
                all_member_rows.extend(groups[m])
            prices = [r['price'] for r in all_member_rows if r['price']]
            files = set(r['file'] for r in all_member_rows)
            periods = set(r['period'] for r in all_member_rows)

            # canonical 取最常见的 material_raw
            mat_counter = defaultdict(int)
            for r in all_member_rows:
                mat_counter[r['material_raw']] += 1
            canonical_material = max(mat_counter, key=mat_counter.get)

            spec_counter = defaultdict(int)
            for r in all_member_rows:
                spec_counter[r['spec_raw']] += 1
            canonical_spec = max(spec_counter, key=spec_counter.get)

            writer.writerow({
                'match_id': f'm{i:04d}',
                'canonical_material': canonical_material,
                'canonical_spec': canonical_spec,
                'members': len(members),
                'file_count': len(files),
                'period_count': len(periods),
                'example_price': f'{prices[0]:.2f}' if prices else '',
                'price_min': f'{min(prices):.2f}' if prices else '',
                'price_max': f'{max(prices):.2f}' if prices else '',
            })

    print(f'✓ matches.csv 已写: {OUTPUT_MATCHES}')

    # 6. 写 pending_pool.csv
    if pending:
        PENDING_POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PENDING_POOL_PATH, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=PENDING_COLUMNS)
            writer.writeheader()
            for row in pending:
                writer.writerow(row)
        print(f'✓ pending_pool.csv 已写: {PENDING_POOL_PATH} ({len(pending)} 条)')
    else:
        print(f'✓ pending_pool 无新条目')

    # 总结
    print(f'\n=== Phase 2 完成 ===')
    print(f'总行数: {len(rows)}')
    print(f'原始 (material, spec) 组: {len(groups)}')
    print(f'合并后同物组: {len(final_groups)}')
    print(f'待审 (pending): {len(pending)}')


if __name__ == '__main__':
    main()
