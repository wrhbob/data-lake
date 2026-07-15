from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.archive_rules import metadata_value
from app.models import Archive, ArchiveFile, DataSource


ACTIVE_ARCHIVE_STATUSES = {"pending_tag", "collected", "archived", "ready_for_governance"}
PUBLICATION_PENDING_NOTE = "未发布或发布节奏待核，不按采集失败处理"
BACKFILL_PENDING_NOTE = "历史期次待回填核验，不按采集失败处理"
MONTH_PERIOD_PATTERN = re.compile(r"(?<!\d)(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])")
CHINESE_MONTH_PERIOD_PATTERN = re.compile(r"(?<!\d)(?P<year>\d{4})年(?P<month>0?[1-9]|1[0-2])月")
SLASH_MONTH_PERIOD_PATTERN = re.compile(r"(?<!\d)(?P<year>\d{4})[/.](?P<month>0?[1-9]|1[0-2])")
ISSUE_LABEL_PATTERN = re.compile(r"第\s*(?P<issue>[0-9０-９一二三四五六七八九十]+)\s*期")


@dataclass(frozen=True)
class IssueGroup:
    issue_key: str
    coverage_region_code: str | None
    period: str
    evidence_count: int
    archive_ids: list[str]
    business_keys: list[str]


@dataclass(frozen=True)
class CoverageMatrixRow:
    province_code: str | None
    coverage_region_code: str | None
    coverage_region_name: str | None
    target_level: str | None
    period: str
    period_label: str | None
    evidence_titles: list[str]
    evidence_archive_ids: list[str]
    primary_file_id: str | None
    primary_file_name: str | None
    primary_download_url: str | None
    file_count: int
    business_coverage_status: str
    source_completeness_status: str
    source_audit_status: str | None
    source_audit_note: str | None
    city_source_url: str | None
    source_visit_url: str | None
    province_source_count: int
    city_source_count: int
    source_ids: list[str]
    coverage_note: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "province_code": self.province_code,
            "coverage_region_code": self.coverage_region_code,
            "coverage_region_name": self.coverage_region_name,
            "target_level": self.target_level,
            "period": self.period,
            "period_label": self.period_label,
            "evidence_titles": self.evidence_titles,
            "evidence_archive_ids": self.evidence_archive_ids,
            "primary_file_id": self.primary_file_id,
            "primary_file_name": self.primary_file_name,
            "primary_download_url": self.primary_download_url,
            "file_count": self.file_count,
            "business_coverage_status": self.business_coverage_status,
            "source_completeness_status": self.source_completeness_status,
            "source_audit_status": self.source_audit_status,
            "source_audit_note": self.source_audit_note,
            "city_source_url": self.city_source_url,
            "source_visit_url": self.source_visit_url,
            "province_source_count": self.province_source_count,
            "city_source_count": self.city_source_count,
            "source_ids": self.source_ids,
            "coverage_note": self.coverage_note,
        }


def build_monthly_periods(start_period: str, end_period: str) -> list[str]:
    start_year, start_month = _split_period(start_period)
    end_year, end_month = _split_period(end_period)
    periods: list[str] = []
    year = start_year
    month = start_month
    while (year, month) <= (end_year, end_month):
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return periods


def build_issue_groups(
    session: Session,
    *,
    start_period: str = "2026-01",
    end_period: str | None = None,
) -> list[IssueGroup]:
    archives = _active_cost_info_archives(session)
    periods = _period_filter(archives, start_period=start_period, end_period=end_period)
    groups: dict[tuple[str | None, str], list[Archive]] = {}
    for archive in archives:
        period = _archive_period(archive)
        if period not in periods:
            continue
        groups.setdefault((_archive_coverage_region_code(archive), period), []).append(archive)

    rows: list[IssueGroup] = []
    for (region_code, period), items in sorted(groups.items(), key=lambda row: (row[0][0] or "", row[0][1])):
        rows.append(
            IssueGroup(
                issue_key=f"cost_info:{region_code or ''}:{period}",
                coverage_region_code=region_code,
                period=period,
                evidence_count=len(items),
                archive_ids=sorted(item.archive_id for item in items),
                business_keys=sorted(item.business_key for item in items),
            )
        )
    return rows


def build_coverage_matrix(
    session: Session,
    *,
    start_period: str = "2026-01",
    end_period: str | None = None,
    province_code: str | None = None,
) -> list[CoverageMatrixRow]:
    sources = _cost_info_sources(session)
    archives = _active_cost_info_archives(session, province_code=province_code)
    source_by_id = {source.source_id: source for source in sources}
    targets = _coverage_targets(sources, province_code=province_code)
    periods = _matrix_period_filter(
        archives,
        targets,
        start_period=start_period,
        end_period=end_period,
    )
    archive_groups = _archives_by_issue(archives, periods)
    all_archive_groups = _archives_by_issue(archives, _observed_archive_periods(archives))

    rows: list[CoverageMatrixRow] = []
    for target in targets:
        earliest_coverage_period, latest_coverage_period = _coverage_period_bounds(target, all_archive_groups)
        for period in sorted(periods):
            evidence = archive_groups.get((target["coverage_region_code"], period), [])
            primary_file = _primary_file_payload(evidence)
            declared_province_sources = _declared_source_ids(target, period, scope="province")
            declared_city_sources = _declared_source_ids(target, period, scope="city")
            province_sources = _combined_source_ids(
                _combined_source_ids(
                    _evidence_source_ids(evidence, source_by_id, scope="province"),
                    declared_province_sources,
                ),
                _registered_source_ids(target, scope="province"),
            )
            city_sources = _combined_source_ids(
                _combined_source_ids(
                    _evidence_source_ids(evidence, source_by_id, scope="city"),
                    declared_city_sources,
                ),
                _registered_source_ids(target, scope="city"),
            )
            source_ids = province_sources + city_sources
            blocked = target["source_completeness_status"] == "source_blocked"
            has_coverage = bool(evidence or declared_province_sources or declared_city_sources)
            pending_publication = not has_coverage and _period_after(period, latest_coverage_period)
            pending_backfill = not has_coverage and _period_before(period, earliest_coverage_period)
            rows.append(
                CoverageMatrixRow(
                    province_code=target["province_code"],
                    coverage_region_code=target["coverage_region_code"],
                    coverage_region_name=target["coverage_region_name"],
                    target_level=target["target_level"],
                    period=period,
                    period_label=_period_label(evidence),
                    evidence_titles=_evidence_titles(evidence),
                    evidence_archive_ids=sorted(archive.archive_id for archive in evidence),
                    primary_file_id=primary_file["file_id"],
                    primary_file_name=primary_file["file_name"],
                    primary_download_url=primary_file["download_url"],
                    file_count=primary_file["file_count"],
                    business_coverage_status=_business_status(
                        has_coverage,
                        blocked,
                        pending_publication or pending_backfill,
                    ),
                    source_completeness_status=_source_status(
                        province_count=len(province_sources),
                        city_count=len(city_sources),
                        requires_city_source=target["requires_city_source"],
                        blocked=blocked,
                    ),
                    source_audit_status=target["source_audit_status"],
                    source_audit_note=target["source_audit_note"],
                    city_source_url=target["city_source_url"],
                    source_visit_url=_source_visit_url(target, city_sources + province_sources, source_by_id),
                    province_source_count=len(province_sources),
                    city_source_count=len(city_sources),
                    source_ids=source_ids,
                    coverage_note=_coverage_note(
                        target["coverage_note"],
                        pending_publication=pending_publication,
                        pending_backfill=pending_backfill,
                    ),
                )
            )
    return sorted(rows, key=lambda row: (row.province_code or "", row.coverage_region_code or "", row.period))


def _split_period(period: str) -> tuple[int, int]:
    normalized = _normalize_month_period(period)
    if normalized is None:
        raise ValueError(f"Invalid month period: {period!r}")
    year, month = normalized.split("-", 1)
    return int(year), int(month)


def _period_key(period: str | None) -> tuple[int, int] | None:
    if period is None:
        return None
    normalized = _normalize_month_period(period)
    if normalized is None:
        return None
    return _split_period(normalized)


def _normalize_month_period(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    for pattern in (MONTH_PERIOD_PATTERN, CHINESE_MONTH_PERIOD_PATTERN, SLASH_MONTH_PERIOD_PATTERN):
        match = pattern.search(text)
        if match:
            return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}"
    return None


def _cell_value(value: object) -> object:
    return metadata_value(value)


def _archive_period(archive: Archive) -> str | None:
    metadata = archive.metadata_payload or {}
    for key in ("period", "period_start", "period_raw"):
        value = _cell_value(metadata.get(key))
        if period := _normalize_month_period(value):
            return period
    return _normalize_month_period(getattr(archive, "publish_date", None))


def _issue_label_from_text(value: object) -> str | None:
    if value is None:
        return None
    match = ISSUE_LABEL_PATTERN.search(str(value))
    if not match:
        return None
    return f"第{match.group('issue')}期"


def _archive_issue_label(archive: Archive) -> str | None:
    metadata = archive.metadata_payload or {}
    for key in ("period_label", "period_issue_label", "issue_label", "period_raw", "period"):
        if label := _issue_label_from_text(_cell_value(metadata.get(key))):
            return label
    return _issue_label_from_text(archive.title)


def _period_label(archives: list[Archive]) -> str | None:
    labels = sorted({label for archive in archives if (label := _archive_issue_label(archive))})
    return labels[0] if labels else None


def _evidence_titles(archives: list[Archive]) -> list[str]:
    return sorted({archive.title for archive in archives if archive.title})


def _primary_file_payload(archives: list[Archive]) -> dict[str, object]:
    mounted_files = []
    for archive in archives:
        for mounted in archive.files:
            if mounted.file_id and mounted.file_asset:
                mounted_files.append(mounted)
    if not mounted_files:
        return {"file_id": None, "file_name": None, "download_url": None, "file_count": 0}
    mounted_files.sort(
        key=lambda mounted: (
            not mounted.is_primary,
            mounted.representation_role != "primary",
            mounted.sort_order,
            mounted.added_at,
        )
    )
    primary = mounted_files[0]
    file_id = primary.file_id
    return {
        "file_id": file_id,
        "file_name": primary.file_asset.file_name if primary.file_asset else primary.display_name,
        "download_url": f"/api/file-assets/{file_id}/download" if file_id else None,
        "file_count": len(mounted_files),
    }


def _archive_coverage_region_code(archive: Archive) -> str | None:
    value = _cell_value((archive.metadata_payload or {}).get("coverage_region_code"))
    return str(value) if value else archive.region_code


def _archive_coverage_region_codes_for_matrix(archive: Archive) -> list[str | None]:
    region_code = _archive_coverage_region_code(archive)
    if not region_code:
        return [None]
    codes: list[str | None] = [region_code]
    if "-" in region_code:
        parent_region_code = region_code.split("-", 1)[0]
        if len(parent_region_code) == 6 and parent_region_code.isdigit():
            codes.append(parent_region_code)
    return list(dict.fromkeys(codes))


def _archive_scope(archive: Archive, source_by_id: dict[str, DataSource]) -> str | None:
    value = _cell_value((archive.metadata_payload or {}).get("publisher_scope"))
    if value:
        return str(value)
    return _source_scope(source_by_id.get(archive.source_id))


def _source_scope(source: DataSource | None) -> str | None:
    if source is None:
        return None
    stable = source.config.get("stable") if isinstance(source.config, dict) else None
    if isinstance(stable, dict) and stable.get("publisher_scope"):
        return str(stable["publisher_scope"])
    return None


def _normalized_source_scope(source: DataSource | None) -> str | None:
    # 直辖市(municipality)按市级源登记，使其能挂进覆盖目标并参与补爬。
    scope = _source_scope(source)
    return "city" if scope == "municipality" else scope


def _province_code_for(region_code: str | None) -> str | None:
    if not region_code or len(region_code) < 2:
        return None
    return f"{region_code[:2]}0000"


def _active_cost_info_archives(session: Session, *, province_code: str | None = None) -> list[Archive]:
    statement = (
        select(Archive)
        .options(selectinload(Archive.files).selectinload(ArchiveFile.file_asset))
        .where(Archive.domain_type == "cost_info")
        .where(Archive.status.in_(ACTIVE_ARCHIVE_STATUSES))
        .where(Archive.is_current.is_(True))
        .where(Archive.is_withdrawn.is_(False))
    )
    if province_code and len(province_code) >= 2:
        # 覆盖目标已经按省份筛选；不必再预加载其他省份的档案和 NAS 文件关系。
        statement = statement.where(Archive.region_code.like(f"{province_code[:2]}%"))
    return list(session.scalars(statement).all())


def _cost_info_sources(session: Session) -> list[DataSource]:
    return list(
        session.scalars(
            select(DataSource)
            .where(DataSource.data_domain == "cost_info")
            .where(DataSource.source_type == "info_price")
            .where(DataSource.source_scope == "platform_public")
        ).all()
    )


def _period_filter(archives: list[Archive], *, start_period: str, end_period: str | None) -> set[str]:
    if end_period is None:
        observed = sorted(period for archive in archives if (period := _archive_period(archive)))
        end_period = observed[-1] if observed else start_period
    return set(build_monthly_periods(start_period, end_period))


def _matrix_period_filter(
    archives: list[Archive],
    targets: list[dict[str, object]],
    *,
    start_period: str,
    end_period: str | None,
) -> set[str]:
    if end_period is not None:
        return set(build_monthly_periods(start_period, end_period))

    target_region_codes = {str(target["coverage_region_code"]) for target in targets}
    start_key = _period_key(start_period)
    observed = {
        period
        for archive in archives
        if (period := _archive_period(archive))
        and any(str(region_code) in target_region_codes for region_code in _archive_coverage_region_codes_for_matrix(archive))
        and (start_key is None or (_period_key(period) or (0, 0)) >= start_key)
    }
    for target in targets:
        declarations = target.get("coverage_declarations")
        if not isinstance(declarations, list):
            continue
        for declaration in declarations:
            if isinstance(declaration, dict) and isinstance(declaration.get("periods"), set):
                observed.update(
                    period
                    for period in declaration["periods"]
                    if start_key is None or (_period_key(period) or (0, 0)) >= start_key
                )

    scoped_end_period = max(observed, key=_split_period) if observed else start_period
    return set(build_monthly_periods(start_period, scoped_end_period))


def _archives_by_issue(archives: list[Archive], periods: set[str]) -> dict[tuple[str | None, str], list[Archive]]:
    groups: dict[tuple[str | None, str], list[Archive]] = {}
    for archive in archives:
        period = _archive_period(archive)
        if period not in periods:
            continue
        for region_code in _archive_coverage_region_codes_for_matrix(archive):
            groups.setdefault((region_code, period), []).append(archive)
    return groups


def _coverage_targets(sources: list[DataSource], *, province_code: str | None) -> list[dict[str, object]]:
    targets: dict[tuple[str | None, str | None], dict[str, object]] = {}
    for source in sources:
        stable = source.config.get("stable") if isinstance(source.config, dict) else {}
        source_province = _province_code_for(str(stable.get("publisher_region_code") or source.region_code or ""))
        if province_code and source_province != province_code:
            continue
        coverage = source.config.get("coverage_expectation") if isinstance(source.config, dict) else {}
        source_targets = coverage.get("target_regions") if isinstance(coverage, dict) else None
        has_explicit_targets = isinstance(source_targets, list) and bool(source_targets)
        if not isinstance(source_targets, list) or not source_targets:
            default_region_code = stable.get("coverage_region_code") or source.region_code
            source_targets = [
                {
                    "region_code": default_region_code,
                    "region_name": source.city or source.province or source.name,
                    "requires_city_source": _normalized_source_scope(source) == "city",
                    "source_completeness_status": "pending_source_audit",
                    "coverage_note": source.remark,
                }
            ]
        for target in source_targets:
            if not isinstance(target, dict):
                continue
            region_code = str(target.get("region_code") or source.region_code or "")
            key = (source_province, region_code)
            explicit_region_name = bool(has_explicit_targets and target.get("region_name"))
            entry = targets.setdefault(
                key,
                {
                    "province_code": source_province,
                    "coverage_region_code": region_code,
                    "coverage_region_name": target.get("region_name"),
                    "coverage_region_name_explicit": explicit_region_name,
                    "target_level": _normalize_target_level(target.get("target_level"), region_code=region_code),
                    "requires_city_source": bool(target.get("requires_city_source")),
                    "source_completeness_status": target.get("source_completeness_status") or "pending_source_audit",
                    "source_audit_status": target.get("source_audit_status"),
                    "source_audit_note": target.get("source_audit_note"),
                    "city_source_url": target.get("city_source_url"),
                    "coverage_note": target.get("coverage_note"),
                    "coverage_declarations": [],
                    "registered_province_source_ids": [],
                    "registered_city_source_ids": [],
                },
            )
            scope = _normalized_source_scope(source)
            source_blocked = target.get("source_completeness_status") == "source_blocked"
            if not source_blocked and scope == "province":
                _append_unique(entry["registered_province_source_ids"], source.source_id)
            elif not source_blocked and scope == "city":
                _append_unique(entry["registered_city_source_ids"], source.source_id)
            if explicit_region_name and not entry.get("coverage_region_name_explicit"):
                entry["coverage_region_name"] = target.get("region_name")
                entry["coverage_region_name_explicit"] = True
            entry["requires_city_source"] = bool(entry["requires_city_source"] or target.get("requires_city_source"))
            if target.get("source_completeness_status") == "source_blocked":
                entry["source_completeness_status"] = "source_blocked"
            if not entry.get("target_level") and target.get("target_level"):
                entry["target_level"] = _normalize_target_level(target.get("target_level"), region_code=region_code)
            if not entry.get("source_audit_status") and target.get("source_audit_status"):
                entry["source_audit_status"] = target.get("source_audit_status")
            if not entry.get("source_audit_note") and target.get("source_audit_note"):
                entry["source_audit_note"] = target.get("source_audit_note")
            if not entry.get("city_source_url") and target.get("city_source_url"):
                entry["city_source_url"] = target.get("city_source_url")
            if not entry.get("coverage_note") and target.get("coverage_note"):
                entry["coverage_note"] = target.get("coverage_note")
            declared_periods = _declared_periods(target)
            if declared_periods:
                entry["coverage_declarations"].append(
                    {
                        "source_id": source.source_id,
                        "scope": _normalized_source_scope(source),
                        "periods": declared_periods,
                    }
                )
    return list(targets.values())


MUNICIPALITY_PROVINCE_CODES = {"110000", "120000", "310000", "500000"}


def _normalize_target_level(value: object, *, region_code: str | None = None) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"city", "prefecture", "prefecture_city", "municipality"}:
        return "city"
    # 北京、天津、上海、重庆的省级官方刊物就是本市信息价。覆盖矩阵按“地市”
    # 展示时，不能因为配置沿用了 province 而将它们全部筛掉。
    if text == "province" and str(region_code or "") in MUNICIPALITY_PROVINCE_CODES:
        return "city"
    if text in {"subregion", "county", "district", "county_district"}:
        return "subregion"
    return text


def _observed_archive_periods(archives: list[Archive]) -> set[str]:
    return {period for archive in archives if (period := _archive_period(archive))}


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _evidence_source_ids(
    archives: list[Archive],
    source_by_id: dict[str, DataSource],
    *,
    scope: str,
) -> list[str]:
    return sorted(
        {
            archive.source_id
            for archive in archives
            if _archive_scope(archive, source_by_id) == scope
        }
    )


def _declared_periods(target: dict) -> set[str]:
    value = target.get("declared_periods")
    if not isinstance(value, list):
        return set()
    return {period for item in value if (period := _normalize_month_period(item))}


def _declared_source_ids(target: dict, period: str, *, scope: str) -> list[str]:
    declarations = target.get("coverage_declarations")
    if not isinstance(declarations, list):
        return []
    return sorted(
        {
            str(declaration["source_id"])
            for declaration in declarations
            if isinstance(declaration, dict)
            and declaration.get("scope") == scope
            and period in declaration.get("periods", set())
            and declaration.get("source_id")
        }
    )


def _registered_source_ids(target: dict, *, scope: str) -> list[str]:
    key = "registered_province_source_ids" if scope == "province" else "registered_city_source_ids"
    values = target.get(key)
    if not isinstance(values, list):
        return []
    return sorted(str(value) for value in values if value)


def _combined_source_ids(first: list[str], second: list[str]) -> list[str]:
    return sorted({*first, *second})


def _source_visit_url(
    target: dict,
    source_ids: list[str],
    source_by_id: dict[str, DataSource],
) -> str | None:
    city_source_url = target.get("city_source_url")
    if city_source_url:
        return str(city_source_url)
    for source_id in source_ids:
        if url := _data_source_visit_url(source_by_id.get(source_id)):
            return url
    return None


def _data_source_visit_url(source: DataSource | None) -> str | None:
    if source is None:
        return None
    stable = source.config.get("stable") if isinstance(source.config, dict) else None
    if isinstance(stable, dict):
        for key in ("entry_url", "official_url", "public_url"):
            value = stable.get(key)
            if value:
                return str(value)
    for value in (source.url, source.base_url):
        if value:
            return str(value)
    return None


def _coverage_period_bounds(
    target: dict,
    archive_groups: dict[tuple[str | None, str], list[Archive]],
) -> tuple[str | None, str | None]:
    region_code = target["coverage_region_code"]
    periods = {period for (archive_region_code, period), items in archive_groups.items() if archive_region_code == region_code and items}
    declarations = target.get("coverage_declarations")
    if isinstance(declarations, list):
        for declaration in declarations:
            if isinstance(declaration, dict) and isinstance(declaration.get("periods"), set):
                periods.update(period for item in declaration["periods"] if (period := _normalize_month_period(item)))
    if not periods:
        return None, None
    return min(periods, key=_split_period), max(periods, key=_split_period)


def _period_after(period: str, baseline: str | None) -> bool:
    period_key = _period_key(period)
    baseline_key = _period_key(baseline)
    return period_key is not None and baseline_key is not None and period_key > baseline_key


def _period_before(period: str, baseline: str | None) -> bool:
    period_key = _period_key(period)
    baseline_key = _period_key(baseline)
    return period_key is not None and baseline_key is not None and period_key < baseline_key


def _coverage_note(note: object, *, pending_publication: bool, pending_backfill: bool) -> str | None:
    base = str(note) if note else None
    notes = [base] if base else []
    if pending_publication:
        notes.append(PUBLICATION_PENDING_NOTE)
    if pending_backfill:
        notes.append(BACKFILL_PENDING_NOTE)
    if not notes:
        return None
    return "；".join(notes)


def _business_status(has_coverage: bool, blocked: bool, pending_verify: bool) -> str:
    if has_coverage:
        return "covered"
    if blocked or pending_verify:
        return "pending_verify"
    return "missing"


def _source_status(*, province_count: int, city_count: int, requires_city_source: bool, blocked: bool) -> str:
    if province_count and city_count:
        return "dual_source"
    if city_count:
        return "city_source_present"
    if blocked:
        return "source_blocked"
    if province_count and requires_city_source:
        return "city_source_missing"
    if province_count:
        return "province_source_only"
    return "pending_source_audit"
