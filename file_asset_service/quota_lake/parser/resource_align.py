"""资源对齐 — 消耗行 → resource_master 对齐.

基于全链路技术规格 §3.5: 机械台班到台班一级为止。
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AlignedResource:
    """对齐后的资源."""
    name_raw: str             # 解析原文
    spec_raw: str = ""
    unit_raw: str = ""
    category: str = ""
    name_std: str = ""
    spec_std: str = ""
    unit_std: str = ""
    matched_id: str = None    # resource_master.id 或 None
    confidence: float = 0.0   # 匹配置信度 [0, 1]


# ── 规范化 ──

_UNIT_SYNONYMS = {
    "kg": "kg", "千克": "kg", "公斤": "kg",
    "t": "t", "吨": "t",
    "m3": "m³", "m³": "m³", "立方米": "m³",
    "m2": "m²", "m²": "m²", "平方米": "m²",
    "m": "m", "米": "m",
    "个": "个", "套": "套", "台班": "台班",
    "工日": "工日", "工": "工日",
    "元": "元",
    "千块": "千块",
    "L": "L", "升": "L",
}


def normalize(text: str) -> str:
    """规范化文本: 去空格 / 全半角转换 / 单位同义词."""
    if not text:
        return ""
    # 全角 → 半角
    result = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            result.append(" ")
        else:
            result.append(ch)
    normalized = "".join(result)
    # 去多余空白
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # 单位同义词
    for syn, std in _UNIT_SYNONYMS.items():
        if normalized == syn:
            normalized = std
            break
    return normalized


def normalize_name(name: str) -> str:
    """规范化资源名称."""
    name = normalize(name)
    # 去掉常见的括号注释中的空格
    name = re.sub(r"\(\s+", "(", name)
    name = re.sub(r"\s+\)", ")", name)
    return name


def normalize_unit(unit: str) -> str:
    """规范化单位."""
    unit = normalize(unit)
    return _UNIT_SYNONYMS.get(unit, unit)


# ── 相似度计算 ──

def _ngram_similarity(a: str, b: str, n: int = 2) -> float:
    """n-gram 相似度 [0, 1]."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    def ngrams(s):
        return set(s[i : i + n] for i in range(len(s) - n + 1))

    sa = ngrams(a)
    sb = ngrams(b)
    if not sa or not sb:
        return 0.0

    intersection = sa & sb
    return len(intersection) / max(len(sa), len(sb))


# ── 对齐主函数 ──

def align_resource(
    name: str,
    unit: str,
    category: str,
    spec: str = "",
    candidates: List[dict] = None,
    auto_threshold: float = 0.92,
) -> AlignedResource:
    """将一条消耗行对到 resource_master.

    Args:
        name: 解析的资源名称.
        unit: 解析的单位.
        category: 人工 | 材料 | 机械.
        spec: 规格.
        candidates: 候选 resource_master 行 [{id, name_std, spec_std, unit_std, category}, ...].
        auto_threshold: 自动对齐阈值.

    Returns:
        AlignedResource.
    """
    name_norm = normalize_name(name)
    unit_norm = normalize_unit(unit)

    result = AlignedResource(
        name_raw=name,
        spec_raw=spec,
        unit_raw=unit,
        category=category,
        name_std=name_norm,
        unit_std=unit_norm,
    )

    if not candidates:
        return result  # 无法对齐, 待人工

    best = None
    best_score = 0.0

    for c in candidates:
        if c.get("category") != category:
            continue

        # 名称相似度
        c_name = normalize_name(c.get("name_std", ""))
        name_score = _ngram_similarity(name_norm, c_name)

        # 单位检查 (不同单位扣分)
        c_unit = normalize_unit(c.get("unit_std", ""))
        unit_match = 1.0 if unit_norm == c_unit else 0.5

        score = name_score * 0.8 + unit_match * 0.2

        if score > best_score:
            best_score = score
            best = c

    if best is not None:
        result.confidence = best_score
        if best_score >= auto_threshold:
            result.matched_id = best.get("id")
            result.name_std = best.get("name_std", result.name_std)
            result.spec_std = best.get("spec_std", "")
            result.unit_std = best.get("unit_std", result.unit_std)

    return result


def batch_align(
    resource_rows: list,
    candidates: List[dict],
    auto_threshold: float = 0.92,
) -> List[AlignedResource]:
    """批量对齐消耗行.

    Args:
        resource_rows: [(name, unit, category, spec), ...].
        candidates: resource_master 候选行.
        auto_threshold: 自动对齐阈值.

    Returns:
        list[AlignedResource].
    """
    results = []
    for row in resource_rows:
        if len(row) == 4:
            name, unit, cat, spec = row
        elif len(row) == 3:
            name, unit, cat = row
            spec = ""
        else:
            name, unit, cat, spec = row[0], row[1] if len(row) > 1 else "", row[2] if len(row) > 2 else "", ""
        results.append(
            align_resource(name, unit, cat, spec, candidates, auto_threshold)
        )
    return results
