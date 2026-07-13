from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_session_factory, init_db
from app.info_price_coverage import build_coverage_matrix
from app.models import Archive, ArchiveFile, CollectionTask, DataSource, FileAsset, FileProcessing


PACKAGE_SCHEMA_VERSION = "info_price_acceptance.v1"

FIRST_WAVE_PROVINCES: tuple[tuple[str, str], ...] = (
    ("510000", "四川省"),
    ("500000", "重庆市"),
    ("420000", "湖北省"),
    ("110000", "北京市"),
    ("310000", "上海市"),
    ("440000", "广东省"),
    ("410000", "河南省"),
    ("620000", "甘肃省"),
    ("650000", "新疆维吾尔自治区"),
    ("150000", "内蒙古自治区"),
    ("520000", "贵州省"),
    ("640000", "宁夏回族自治区"),
)

PROVINCE_NAME_TO_CODE = {
    name: code for code, name in FIRST_WAVE_PROVINCES
} | {
    "四川": "510000",
    "重庆": "500000",
    "湖北": "420000",
    "北京": "110000",
    "上海": "310000",
    "广东": "440000",
    "河南": "410000",
    "甘肃": "620000",
    "新疆": "650000",
    "新疆维吾尔自治区": "650000",
    "内蒙古": "150000",
    "内蒙古自治区": "150000",
    "贵州": "520000",
    "宁夏": "640000",
    "宁夏回族自治区": "640000",
}

SOURCE_AUDIT_FIELDS = [
    "province_code",
    "province_name",
    "source_id",
    "source_name",
    "source_scope",
    "source_status",
    "audit_status",
    "region_code",
    "city",
    "publisher_name",
    "publisher_scope",
    "acquisition",
    "channel_type",
    "source_attachment_mode",
    "parsability",
    "downloadable",
    "parser_version",
    "source_url",
    "blocked_reason",
    "manual_path",
    "coverage_target_count",
    "remark",
]

COVERAGE_MATRIX_FIELDS = [
    "province_code",
    "province_name",
    "coverage_region_code",
    "coverage_region_name",
    "target_level",
    "period",
    "business_coverage_status",
    "source_completeness_status",
    "source_audit_status",
    "source_audit_note",
    "city_source_url",
    "source_visit_url",
    "province_source_count",
    "city_source_count",
    "source_ids",
    "coverage_note",
]

ARCHIVE_LIST_FIELDS = [
    "province_code",
    "province_name",
    "archive_id",
    "business_key",
    "title",
    "status",
    "channel_type",
    "collection_method",
    "region_code",
    "coverage_region_code",
    "period",
    "tax_type",
    "price_source_type",
    "publish_date",
    "source_id",
    "task_id",
    "batch_id",
    "source_item_key",
    "source_url",
    "file_count",
    "primary_file_name",
    "primary_file_role",
    "created_at",
    "updated_at",
]


@dataclass(frozen=True)
class AcceptancePackageResult:
    output_dir: Path
    files: dict[str, Path]
    summary: dict[str, Any]


def export_info_price_acceptance_package(
    session: Session,
    output_dir: str | Path,
    *,
    start_period: str = "2026-01",
    end_period: str | None = None,
    province_codes: list[str] | None = None,
    generated_at: datetime | None = None,
) -> AcceptancePackageResult:
    codes = _normalize_province_codes(province_codes)
    generated = generated_at or datetime.now(UTC)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    source_rows = build_source_audit_rows(session, province_codes=codes)
    coverage_rows = build_acceptance_coverage_rows(
        session,
        province_codes=codes,
        start_period=start_period,
        end_period=end_period,
    )
    archive_rows = build_archive_list_rows(session, province_codes=codes)
    summary = build_collection_run_summary(
        session,
        source_rows=source_rows,
        coverage_rows=coverage_rows,
        archive_rows=archive_rows,
        province_codes=codes,
        start_period=start_period,
        end_period=end_period,
        generated_at=generated,
    )

    files = {
        "source_audit": target_dir / "first_wave_source_audit.csv",
        "collection_run": target_dir / "first_wave_collection_run.json",
        "coverage_matrix": target_dir / "first_wave_coverage_matrix.csv",
        "archive_list": target_dir / "first_wave_archive_list.csv",
        "manifest": target_dir / "_manifest.json",
    }
    _write_csv(files["source_audit"], SOURCE_AUDIT_FIELDS, source_rows)
    _write_json(files["collection_run"], summary)
    _write_csv(files["coverage_matrix"], COVERAGE_MATRIX_FIELDS, coverage_rows)
    _write_csv(files["archive_list"], ARCHIVE_LIST_FIELDS, archive_rows)
    _write_json(
        files["manifest"],
        {
            "package_schema_version": PACKAGE_SCHEMA_VERSION,
            "generated_at": generated.isoformat(),
            "files": {key: path.name for key, path in files.items() if key != "manifest"},
            "scope": summary["scope"],
            "red_lines": summary["red_lines"],
        },
    )
    return AcceptancePackageResult(output_dir=target_dir, files=files, summary=summary)


def build_source_audit_rows(session: Session, *, province_codes: list[str]) -> list[dict[str, Any]]:
    sources = [
        source
        for source in session.scalars(select(DataSource).order_by(DataSource.region_code, DataSource.name)).all()
        if _is_cost_info_source(source)
    ]
    rows: list[dict[str, Any]] = []
    province_set = set(province_codes)
    grouped: dict[str, list[DataSource]] = {code: [] for code in province_codes}
    for source in sources:
        province_code = _source_province_code(source)
        if province_code in province_set:
            grouped.setdefault(province_code, []).append(source)

    for province_code in province_codes:
        province_sources = grouped.get(province_code, [])
        if not province_sources:
            rows.append(_missing_source_audit_row(province_code))
            continue
        for source in province_sources:
            rows.append(_source_audit_row(source, province_code))
    return rows


def build_acceptance_coverage_rows(
    session: Session,
    *,
    province_codes: list[str],
    start_period: str,
    end_period: str | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province_code in province_codes:
        matrix = build_coverage_matrix(
            session,
            start_period=start_period,
            end_period=end_period,
            province_code=province_code,
        )
        for row in matrix:
            payload = row.to_dict()
            payload["province_name"] = _province_name(str(payload.get("province_code") or province_code))
            rows.append(payload)
    return rows


def build_archive_list_rows(session: Session, *, province_codes: list[str]) -> list[dict[str, Any]]:
    archives = session.scalars(
        select(Archive)
        .where(Archive.domain_type == "cost_info", Archive.is_withdrawn.is_(False))
        .order_by(Archive.created_at.desc(), Archive.archive_id.desc())
    ).all()
    province_set = set(province_codes)
    rows: list[dict[str, Any]] = []
    for archive in archives:
        province_code = _archive_province_code(session, archive)
        if province_code not in province_set:
            continue
        period = _metadata_cell_value((archive.metadata_payload or {}).get("period"))
        file_summary = _archive_file_summary(session, archive.archive_id)
        rows.append(
            {
                "province_code": province_code,
                "province_name": _province_name(province_code),
                "archive_id": archive.archive_id,
                "business_key": archive.business_key,
                "title": archive.title,
                "status": archive.status,
                "channel_type": archive.channel_type,
                "collection_method": archive.collection_method,
                "region_code": archive.region_code,
                "coverage_region_code": _metadata_cell_value(
                    (archive.metadata_payload or {}).get("coverage_region_code")
                )
                or archive.region_code,
                "period": period
                or _metadata_cell_value((archive.metadata_payload or {}).get("period_start"))
                or _metadata_cell_value((archive.metadata_payload or {}).get("period_raw")),
                "tax_type": _metadata_cell_value((archive.metadata_payload or {}).get("tax_type")),
                "price_source_type": _metadata_cell_value(
                    (archive.metadata_payload or {}).get("price_source_type")
                ),
                "publish_date": archive.publish_date.isoformat() if archive.publish_date else None,
                "source_id": archive.source_id,
                "task_id": archive.task_id,
                "batch_id": archive.batch_id,
                "source_item_key": archive.source_item_key,
                "source_url": archive.source_url,
                "file_count": file_summary["file_count"],
                "primary_file_name": file_summary["primary_file_name"],
                "primary_file_role": file_summary["primary_file_role"],
                "created_at": archive.created_at.isoformat(),
                "updated_at": archive.updated_at.isoformat(),
            }
        )
    return rows


def build_collection_run_summary(
    session: Session,
    *,
    source_rows: list[dict[str, Any]],
    coverage_rows: list[dict[str, Any]],
    archive_rows: list[dict[str, Any]],
    province_codes: list[str],
    start_period: str,
    end_period: str | None,
    generated_at: datetime,
) -> dict[str, Any]:
    archive_channel_counts = Counter(row["channel_type"] for row in archive_rows if row.get("channel_type"))
    archive_status_counts = Counter(row["status"] for row in archive_rows if row.get("status"))
    source_status_counts = Counter(row["audit_status"] for row in source_rows if row.get("audit_status"))
    coverage_business_counts = Counter(
        row["business_coverage_status"] for row in coverage_rows if row.get("business_coverage_status")
    )
    coverage_source_counts = Counter(
        row["source_completeness_status"] for row in coverage_rows if row.get("source_completeness_status")
    )
    task_status_counts = _task_status_counts(session, province_codes=province_codes)
    file_processing_count = _scoped_file_processing_count(session, province_codes=province_codes)
    return {
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "scope": {
            "domain_type": "cost_info",
            "province_codes": province_codes,
            "province_names": [_province_name(code) for code in province_codes],
            "start_period": start_period,
            "end_period": end_period,
        },
        "source_counts": {
            "total_rows": len(source_rows),
            "by_audit_status": dict(sorted(source_status_counts.items())),
        },
        "archive_counts": {
            "total": len(archive_rows),
            "by_channel": dict(sorted(archive_channel_counts.items())),
            "by_status": dict(sorted(archive_status_counts.items())),
        },
        "coverage_counts": {
            "total_cells": len(coverage_rows),
            "by_business_coverage_status": dict(sorted(coverage_business_counts.items())),
            "by_source_completeness_status": dict(sorted(coverage_source_counts.items())),
        },
        "task_counts": {
            "total": sum(task_status_counts.values()),
            "by_status": dict(sorted(task_status_counts.items())),
        },
        "red_lines": {
            "file_processing_expected": 0,
            "file_processing_actual": file_processing_count,
            "file_processing_passes": file_processing_count == 0,
            "layer0_rule": "FileProcessing must remain 0 for accepted information-price Layer 0 original archives.",
        },
    }


def _source_audit_row(source: DataSource, province_code: str) -> dict[str, Any]:
    config = source.config if isinstance(source.config, dict) else {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    coverage = config.get("coverage_expectation") if isinstance(config.get("coverage_expectation"), dict) else {}
    target_regions = coverage.get("target_regions") if isinstance(coverage.get("target_regions"), list) else []
    acquisition = _first_present(
        stable.get("acquisition"),
        config.get("acquisition"),
        coverage.get("mode"),
    )
    parser_version = _first_present(
        config.get("active_parser_version"),
        stable.get("active_parser_version"),
    )
    return {
        "province_code": province_code,
        "province_name": _province_name(province_code),
        "source_id": source.source_id,
        "source_name": source.name,
        "source_scope": source.source_scope,
        "source_status": source.status,
        "audit_status": _source_audit_status(source, acquisition),
        "region_code": source.region_code or stable.get("coverage_region_code") or stable.get("region_code"),
        "city": source.city,
        "publisher_name": stable.get("publisher_name") or source.name,
        "publisher_scope": stable.get("publisher_scope"),
        "acquisition": acquisition,
        "channel_type": _channel_type_for_source(source, acquisition),
        "source_attachment_mode": stable.get("source_attachment_mode") or source.format,
        "parsability": stable.get("parsability"),
        "downloadable": source.downloadable,
        "parser_version": parser_version,
        "source_url": source.url or source.base_url or stable.get("entry_url"),
        "blocked_reason": _first_present(stable.get("blocked_reason"), config.get("blocked_reason")),
        "manual_path": _manual_path(source, acquisition),
        "coverage_target_count": len(target_regions),
        "remark": source.remark,
    }


def _missing_source_audit_row(province_code: str) -> dict[str, Any]:
    return {
        "province_code": province_code,
        "province_name": _province_name(province_code),
        "source_id": None,
        "source_name": None,
        "source_scope": None,
        "source_status": "missing",
        "audit_status": "missing_source_audit",
        "region_code": province_code,
        "city": None,
        "publisher_name": None,
        "publisher_scope": None,
        "acquisition": None,
        "channel_type": None,
        "source_attachment_mode": None,
        "parsability": None,
        "downloadable": None,
        "parser_version": None,
        "source_url": None,
        "blocked_reason": None,
        "manual_path": None,
        "coverage_target_count": 0,
        "remark": "首批省份尚无 source registry 记录，验收不可判定为完成。",
    }


def _source_audit_status(source: DataSource, acquisition: Any) -> str:
    text = " ".join(
        str(value)
        for value in [
            source.status,
            acquisition,
            source.remark,
            source.config if isinstance(source.config, dict) else None,
        ]
        if value is not None
    ).lower()
    if "source_blocked" in text or "manual_upload_required" in text or "blocked" in text:
        return "source_blocked"
    if "coverage_declaration" in text or "coverage_directory" in text:
        return "coverage_declaration"
    if source.downloadable is True or "auto_crawl" in text or "public_file" in text:
        return "auto_crawl_ready"
    if source.status in {"active", "verified"}:
        return "pending_verify"
    return source.status or "pending_verify"


def _manual_path(source: DataSource, acquisition: Any) -> str | None:
    status = _source_audit_status(source, acquisition)
    if status != "source_blocked":
        return None
    return "017/manual_upload"


def _channel_type_for_source(source: DataSource, acquisition: Any) -> str | None:
    status = _source_audit_status(source, acquisition)
    if status == "source_blocked":
        return "manual_upload"
    if status in {"auto_crawl_ready", "coverage_declaration", "pending_verify"}:
        return "crawler"
    return None


def _is_cost_info_source(source: DataSource) -> bool:
    config = source.config if isinstance(source.config, dict) else {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    return (
        source.data_domain == "cost_info"
        or source.source_type in {"info_price", "cost_info"}
        or stable.get("domain_type") == "cost_info"
    )


def _source_province_code(source: DataSource) -> str | None:
    config = source.config if isinstance(source.config, dict) else {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    candidates = [
        source.region_code,
        stable.get("coverage_region_code"),
        stable.get("publisher_region_code"),
        stable.get("region_code"),
    ]
    coverage = config.get("coverage_expectation") if isinstance(config.get("coverage_expectation"), dict) else {}
    target_regions = coverage.get("target_regions") if isinstance(coverage.get("target_regions"), list) else []
    for target in target_regions:
        if isinstance(target, dict):
            candidates.append(target.get("coverage_region_code") or target.get("region_code"))
    for candidate in candidates:
        province_code = _province_code_from_region(candidate)
        if province_code:
            return province_code
    if source.province:
        return PROVINCE_NAME_TO_CODE.get(source.province)
    return None


def _archive_province_code(session: Session, archive: Archive) -> str | None:
    metadata = archive.metadata_payload or {}
    candidates = [
        _metadata_cell_value(metadata.get("coverage_region_code")),
        archive.region_code,
    ]
    for candidate in candidates:
        province_code = _province_code_from_region(candidate)
        if province_code:
            return province_code
    source = session.get(DataSource, archive.source_id)
    return _source_province_code(source) if source is not None else None


def _province_code_from_region(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 2 or not text[:2].isdigit():
        return None
    if len(text) >= 6 and text[:6].isdigit():
        return f"{text[:2]}0000"
    return None


def _province_name(province_code: str) -> str:
    return dict(FIRST_WAVE_PROVINCES).get(province_code, province_code)


def _normalize_province_codes(province_codes: list[str] | None) -> list[str]:
    if not province_codes:
        return [code for code, _ in FIRST_WAVE_PROVINCES]
    normalized: list[str] = []
    for code in province_codes:
        province_code = _province_code_from_region(code) or code
        if province_code not in normalized:
            normalized.append(province_code)
    return normalized


def _metadata_cell_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _archive_file_summary(session: Session, archive_id: str) -> dict[str, Any]:
    rows = session.execute(
        select(ArchiveFile, FileAsset)
        .outerjoin(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .where(ArchiveFile.archive_id == archive_id)
        .order_by(ArchiveFile.is_primary.desc(), ArchiveFile.sort_order, ArchiveFile.added_at)
    ).all()
    primary = next((row for row in rows if row[0].is_primary), rows[0] if rows else None)
    return {
        "file_count": len(rows),
        "primary_file_name": primary[1].file_name if primary and primary[1] else primary[0].display_name if primary else None,
        "primary_file_role": primary[0].file_role if primary else None,
    }


def _task_status_counts(session: Session, *, province_codes: list[str]) -> Counter:
    source_ids = _scoped_source_ids(session, province_codes=province_codes)
    if not source_ids:
        return Counter()
    rows = session.execute(
        select(CollectionTask.status, func.count())
        .where(CollectionTask.source_id.in_(source_ids), CollectionTask.data_domain == "cost_info")
        .group_by(CollectionTask.status)
    ).all()
    return Counter({status: count for status, count in rows})


def _scoped_file_processing_count(session: Session, *, province_codes: list[str]) -> int:
    archive_ids = [row["archive_id"] for row in build_archive_list_rows(session, province_codes=province_codes)]
    if not archive_ids:
        return 0
    count = session.scalar(
        select(func.count())
        .select_from(FileProcessing)
        .join(ArchiveFile, ArchiveFile.file_id == FileProcessing.file_id)
        .where(ArchiveFile.archive_id.in_(archive_ids))
    )
    return int(count or 0)


def _scoped_source_ids(session: Session, *, province_codes: list[str]) -> list[str]:
    province_set = set(province_codes)
    return [
        source.source_id
        for source in session.scalars(select(DataSource)).all()
        if _is_cost_info_source(source) and _source_province_code(source) in province_set
    ]


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export first-wave information-price Layer 0 acceptance package.")
    parser.add_argument("--output-dir", required=True, help="Directory that will receive the fixed acceptance files.")
    parser.add_argument("--start-period", default="2026-01", help="Coverage matrix start period, YYYY-MM.")
    parser.add_argument("--end-period", default=None, help="Coverage matrix end period, YYYY-MM.")
    parser.add_argument(
        "--province-code",
        action="append",
        dest="province_codes",
        help="Province code to include. Can be passed multiple times. Defaults to first-wave 12 provinces.",
    )
    parser.add_argument("--skip-init-db", action="store_true", help="Do not run schema initialization/migrations first.")
    args = parser.parse_args(argv)

    if not args.skip_init_db:
        init_db()
    SessionFactory = get_session_factory()
    with SessionFactory() as session:
        result = export_info_price_acceptance_package(
            session,
            args.output_dir,
            start_period=args.start_period,
            end_period=args.end_period,
            province_codes=args.province_codes,
        )
    print(json.dumps({key: str(path) for key, path in result.files.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
