"""定额覆盖矩阵 — 按 (年份 × 省) 统计档案数。

设计：
  行 = 年份（desc，缺年份落到 "未知" 行）
  列 = 省份（按 region_code 映射省名，按 GB/T 2260 升序）
  单元格 = 该 (年份 × 省) 的档案数（主数字）
         + parse_status 汇总（candidate_ready / qa_passed / 其他）
         + 该 cell 下所有档案的 archive_id + title（前端 hover 详情用）

年份来源（优先级降序）：
  1. QuotaPublicationSet.edition_year（最权威——档案列表 / facets 端点都基于这个字段）
     └ 兜底：QuotaPublicationSet.edition_label（字符串形式的 4 位年份）
  2. title 正则 20\d{2}（书名里出现的年份）
  3. metadata.year.value（manual 标注）
  4. 缺失 → "未知" 行

省来源：region_code 前 2 位映射省名（复用 info_price_coverage 已有的 regionLabel 风格）。

关键改动 (2026-08-18)：
  Archive 表本身没有 edition_year 列——quota 档案的版本年是挂在关联的
  QuotaPublicationSet 上的（Archive → QuotaArchiveProfile → QuotaPublicationSet）。
  这与档案列表 / facets 端点口径完全一致（quota_api.py:328 QuotaPublicationSet.edition_year）。
  旧版 `_extract_year` 只看 title 正则 + Archive.metadata.year，导致 7/19 条档案被误判为
  "未知"（湖南 1 + 广东 4 + 四川 2 — 这些 title 不带年份但都已入湖登记，年份在 pubset 上）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.archive_rules import metadata_value
from app.models import Archive, ArchiveFile, QuotaArchiveProfile, QuotaPublicationSet

# ── 年份正则 ──
# 容忍中英文括号：「（2024）」 / 「(2018)」 / 「_2018_」 / 「2020年版」 / 「2024」
YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?:\s*年\s*版)?(?!\d)")

# ── 区域名映射 ──
# 参考 info_price_coverage 已用的 regionLabel 风格。GB/T 2260 前 2 位 → 省名。
PROVINCE_NAMES: dict[str, str] = {
    "11": "北京", "12": "天津", "13": "河北", "14": "山西", "15": "内蒙古",
    "21": "辽宁", "22": "吉林", "23": "黑龙江",
    "31": "上海", "32": "江苏", "33": "浙江", "34": "安徽", "35": "福建", "36": "江西", "37": "山东",
    "41": "河南", "42": "湖北", "43": "湖南", "44": "广东", "45": "广西", "46": "海南",
    "50": "重庆", "51": "四川", "52": "贵州", "53": "云南", "54": "西藏",
    "61": "陕西", "62": "甘肃", "63": "青海", "64": "宁夏", "65": "新疆",
    "71": "台湾", "81": "香港", "82": "澳门",
}

# 列排序键：按省名拼音首字母（用户原话 "会比较长但可接受"，顺序固定即可）
PROVINCE_ORDER: dict[str, int] = {
    "北京": 1, "天津": 2, "上海": 3, "重庆": 4,
    "河北": 5, "山西": 6, "辽宁": 7, "吉林": 8, "黑龙江": 9,
    "江苏": 10, "浙江": 11, "安徽": 12, "福建": 13, "江西": 14, "山东": 15,
    "河南": 16, "湖北": 17, "湖南": 18, "广东": 19, "广西": 20, "海南": 21,
    "内蒙古": 22, "四川": 23, "贵州": 24, "云南": 25, "西藏": 26,
    "陕西": 27, "甘肃": 28, "青海": 29, "宁夏": 30, "新疆": 31,
    "台湾": 32, "香港": 33, "澳门": 34,
}

UNKNOWN_YEAR_LABEL = "未知"


@dataclass(frozen=True)
class QuotaCoverageCell:
    """矩阵的一个单元格 = (year, province_code) 组合下的所有档案。"""
    year: str | None               # None 表示 "未知"
    province_code: str             # 2 位省份代码（如 "42"）
    province_name: str             # "湖北"
    archive_count: int
    archive_ids: list[str]
    archive_titles: list[str]
    parse_statuses: dict[str, int] = field(default_factory=dict)  # {status: count}

    def to_dict(self) -> dict[str, object]:
        return {
            "year": self.year,
            "province_code": self.province_code,
            "province_name": self.province_name,
            "archive_count": self.archive_count,
            "archive_ids": self.archive_ids,
            "archive_titles": self.archive_titles,
            "parse_statuses": self.parse_statuses,
        }


def _extract_year(
    archive: Archive,
    pubset: QuotaPublicationSet | None,
) -> str | None:
    """从 pubset / title / metadata 取 4 位年份。

    优先级：
      1. pubset.edition_year（int 列；与 /api/data-lake/quota/archives?edition_year=2018 同源）
      2. pubset.edition_label（字符串兜底，例如 "2018"）
      3. archive.title 正则 20\\d{2}
      4. archive.metadata.year.value（manual 标注）
    """
    # 1) pubset.edition_year（最权威；archive list / facets 端点的 year 字段同源）
    if pubset is not None and pubset.edition_year is not None:
        return f"{int(pubset.edition_year):04d}"

    # 2) pubset.edition_label 兜底（字符串）
    if pubset is not None and pubset.edition_label:
        match = YEAR_RE.search(str(pubset.edition_label))
        if match:
            return match.group(1)
        try:
            year_int = int(str(pubset.edition_label).strip())
            if 1900 <= year_int <= 2099:
                return f"{year_int:04d}"
        except (ValueError, TypeError):
            pass

    # 3) title 正则（书名里出现的年份，例如《...（2024）》）
    if archive.title:
        match = YEAR_RE.search(archive.title)
        if match:
            return match.group(1)

    # 4) metadata.year.value（manual 标注）
    metadata = archive.metadata_payload or {}
    raw = metadata_value(metadata.get("year"))
    if raw is not None:
        text = str(raw).strip()
        match = YEAR_RE.search(text)
        if match:
            return match.group(1)
        try:
            year_int = int(text)
            if 1900 <= year_int <= 2099:
                return f"{year_int:04d}"
        except (ValueError, TypeError):
            pass
    return None


def _province_code_from_region(region_code: str | None) -> str | None:
    """region_code → 省代码（2 位）。"""
    if not region_code or len(region_code) < 2:
        return None
    return region_code[:2]


def _province_name(province_code: str | None) -> str:
    if not province_code:
        return UNKNOWN_YEAR_LABEL
    return PROVINCE_NAMES.get(province_code, province_code)


def build_quota_coverage_matrix(session: Session) -> dict[str, object]:
    """构建定额覆盖矩阵。

    返回结构（前端可直接消费）：
      {
        "years": ["2024", "2020", "2018", "未知"],   # 行顺序（desc，未知在末尾）
        "provinces": [                                # 列顺序（按 PROVINCE_ORDER 升序）
          {"code": "42", "name": "湖北"},
          {"code": "44", "name": "广东"},
          ...
        ],
        "cells": {                                    # 稀疏矩阵：(year, province_code) → cell dict
          ("2024", "42"): {...QuotaCoverageCell.to_dict()...},
          ("未知", "44"): {...},
          ...
        },
        "summary": {
          "total_archives": 19,
          "year_coverage": {"2026": 4, "2025": 1, "2024": 3, "2020": 3, "2018": 8, "未知": 0},
          "unknown_year_count": 0,
        }
      }
    """
    statement = (
        select(Archive, QuotaArchiveProfile, QuotaPublicationSet)
        .join(QuotaArchiveProfile, QuotaArchiveProfile.archive_id == Archive.archive_id, isouter=True)
        .join(
            QuotaPublicationSet,
            QuotaPublicationSet.publication_set_id == QuotaArchiveProfile.publication_set_id,
            isouter=True,
        )
        .options(selectinload(Archive.files).selectinload(ArchiveFile.file_asset))
        .where(Archive.domain_type == "quota")
        .where(Archive.is_current.is_(True))
        .where(Archive.is_withdrawn.is_(False))
    )
    rows = session.execute(statement).all()

    # ── 按 (year, province_code) 分组 ──
    bucket: dict[tuple[str | None, str | None], list[tuple[Archive, QuotaPublicationSet | None]]] = {}
    for archive, _profile, pubset in rows:
        year = _extract_year(archive, pubset)
        province_code = _province_code_from_region(archive.region_code)
        bucket.setdefault((year, province_code), []).append((archive, pubset))

    # ── 收集行 (year) 与列 (province_code) ──
    years_with_data: set[str | None] = {y for y, _ in bucket.keys() if bucket[(y, _)]}
    provinces_with_data: set[str | None] = {p for _, p in bucket.keys() if bucket[(_, p)]}

    # 行序：已知年份 desc + "未知" 末尾
    known_years = sorted((y for y in years_with_data if y is not None), reverse=True)
    years: list[str | None] = known_years + ([UNKNOWN_YEAR_LABEL] if None in years_with_data else [])

    # 列序：按 PROVINCE_ORDER 升序，缺失省（无 PROVINCE_NAMES 映射）落到末尾
    def province_sort_key(code: str | None) -> tuple[int, str]:
        if code is None:
            return (9999, "")
        name = _province_name(code)
        return (PROVINCE_ORDER.get(name, 9999), name)

    province_codes_sorted = sorted(provinces_with_data, key=province_sort_key)

    # ── 构造 cells（稀疏 dict） ──
    cells: dict[tuple[str | None, str | None], dict[str, object]] = {}
    year_summary: dict[str, int] = {}
    total_archives = 0

    for (year, province_code), items in bucket.items():
        if not items:
            continue
        archive_ids = sorted(a.archive_id for a, _ in items)
        titles = sorted(a.title for a, _ in items if a.title)
        statuses: dict[str, int] = {}
        for a, _ in items:
            key = a.parse_status or "unknown"
            statuses[key] = statuses.get(key, 0) + 1
        # 行标签：None → "未知"（用户视角）
        year_label = year if year is not None else UNKNOWN_YEAR_LABEL
        cell = QuotaCoverageCell(
            year=year_label,
            province_code=province_code or "",
            province_name=_province_name(province_code) if province_code else UNKNOWN_YEAR_LABEL,
            archive_count=len(items),
            archive_ids=archive_ids,
            archive_titles=titles,
            parse_statuses=statuses,
        )
        cells[(year_label, province_code or "")] = cell.to_dict()
        year_summary[year_label] = year_summary.get(year_label, 0) + len(items)
        total_archives += len(items)

    return {
        "years": years,
        "provinces": [
            {"code": c or "", "name": _province_name(c) if c else UNKNOWN_YEAR_LABEL}
            for c in province_codes_sorted
        ],
        "cells": {f"{y}|{p}": v for (y, p), v in cells.items()},
        "summary": {
            "total_archives": total_archives,
            "year_coverage": year_summary,
            # "未知" 行计数 == 无年份档案数；year_summary 已在循环里按 year_label 聚合
            "unknown_year_count": year_summary.get(UNKNOWN_YEAR_LABEL, 0),
        },
    }