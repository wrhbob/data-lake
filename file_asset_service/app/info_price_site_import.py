from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collection import PLATFORM_PUBLIC_TENANT
from app.database import get_session_factory, init_db
from app.models import CollectionTask, DataSource, utcnow


IMPORT_VERSION = "info_price_site_ledger_v1"
COST_INFO_DOMAIN = "cost_info"
INFO_PRICE_SOURCE_TYPE = "info_price"
SOURCE_REGISTRY_CONNECTOR = "source_registry"


@dataclass(frozen=True)
class ImportResult:
    total_rows: int
    created_sources: int
    updated_sources: int
    created_tasks: int
    updated_tasks: int
    bucket_counts: dict[str, int]
    status_counts: dict[str, int]
    period_start_count: int


def clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def clean_url(value: str | None) -> str | None:
    text = clean(value)
    if text in {None, "无", "暂无", "-"}:
        return None
    return text


def parse_downloadable(value: str | None) -> bool | None:
    text = clean(value)
    if text == "是":
        return True
    if text == "否":
        return False
    return None


def parse_yes_no(value: str | None) -> bool | None:
    text = clean(value)
    if text in {"是", "true", "True", "1", "yes", "Y"}:
        return True
    if text in {"否", "false", "False", "0", "no", "N"}:
        return False
    return None


def row_source_status(row: dict[str, str]) -> str:
    explicit = clean(row.get("source_status"))
    if explicit:
        return explicit
    bucket = clean(row.get("bucket"))
    return {
        "可自动采": "auto_crawl_ready",
        "需人工": "source_blocked",
        "异常待修": "pending_verify",
        "待找源": "missing_official_source",
    }.get(bucket or "", "pending_verify")


def source_registry_site_id(row: dict[str, str]) -> str | None:
    return clean(row.get("site_id"))


def entry_url_for(row: dict[str, str]) -> str | None:
    return clean_url(row.get("entry_url")) or clean_url(row.get("url"))


def schedule_policy_for(row: dict[str, str]) -> dict | None:
    if row_source_status(row) != "auto_crawl_ready":
        return None
    entry_url = entry_url_for(row)
    host = urlsplit(entry_url or "").netloc
    return {
        "enabled": False,
        "frequency": "daily",
        "timezone": "Asia/Shanghai",
        "max_attempts": 3,
        "early_stop_duplicate": True,
        "rate_limit": {
            "host": host,
            "max_concurrent": 1,
            "min_delay_seconds": 8,
            "jitter_seconds": 4,
        },
    }


def parser_config_for(row: dict[str, str]) -> dict:
    adapter_kind = clean(row.get("adapter_kind"))
    site_id = source_registry_site_id(row)
    if not adapter_kind or not site_id:
        return {}
    parser_version = f"{site_id}.v1"
    return {
        "active_parser_version": parser_version,
        "parsers": {
            parser_version: {
                "adapter_kind": adapter_kind,
                "list_url": entry_url_for(row),
                "crawl_pattern": clean(row.get("crawl_pattern")),
            }
        },
    }


def _factory_parser_for(site_id: str | None) -> dict | None:
    """Full parser block from the site's config factory, or None.

    Re-importing the CSV ledger must not clobber a factory-restored parser with
    the CSV summary (which only carries ``adapter_kind`` + the site-root URL as
    ``list_url``). For sites with a registered ``_source_config()`` factory we
    take the factory's parser (with ``file_list_endpoint``/selectors); sites
    without a factory fall through to the CSV summary at the call site.
    """
    if not site_id:
        return None
    try:
        from app.cost_info_config_factories import build_source_config

        return (build_source_config(site_id) or {}).get("parser") or None
    except ValueError:
        return None


def build_registry_config(source: DataSource, row: dict[str, str]) -> dict:
    site_id = source_registry_site_id(row)
    entry_url = entry_url_for(row)
    target_region_code = clean(row.get("target_region_code")) or clean(row.get("region_code"))
    target_region_name = clean(row.get("target_region_name")) or clean(row.get("city")) or clean(row.get("province"))
    publisher_scope = clean(row.get("publisher_scope")) or (
        "city" if clean(row.get("city")) not in {None, "省站"} else "province"
    )
    source_status = row_source_status(row)
    note = clean(row.get("audit_note")) or clean(row.get("remark"))
    config = dict(source.config or {})
    config.update(
        {
            "import_version": IMPORT_VERSION,
            "registry_schema_version": "source_registry.v1",
            "ledger_seq": int(clean(row.get("seq")) or 0),
            "stable": {
                "site_id": site_id,
                "domain_type": COST_INFO_DOMAIN,
                "province": clean(row.get("province_name")) or clean(row.get("province")),
                "city": clean(row.get("target_region_name")) or clean(row.get("city")),
                "region_code": target_region_code,
                "coverage_region_code": target_region_code,
                "publisher_name": clean(row.get("publisher_name")) or clean(row.get("name")),
                "publisher_scope": publisher_scope,
                "entry_url": entry_url,
            },
            "parser": _factory_parser_for(site_id) or parser_config_for(row),
            "price_coordinates": {
                "price_source_type": INFO_PRICE_SOURCE_TYPE,
                "price_kind": clean(row.get("price_kind")) or "unspecified",
                "tax_type": clean(row.get("tax_type")),
            },
            "source_shape": {
                "source_attachment_mode": clean(row.get("source_attachment_mode")) or clean(row.get("format")),
                "publication_mode": clean(row.get("publication_mode")) or "UNKNOWN",
                "parsability": clean(row.get("parsability")) or "unknown",
                "period_kind": clean(row.get("period_kind")) or "monthly",
            },
            "reachability": {
                "source_status": source_status,
                "downloadable": parse_yes_no(row.get("downloadable")),
                "blocked_reason": clean(row.get("blocked_reason")),
                "manual_path": clean(row.get("manual_path")),
            },
            "coverage_expectation": {
                "periodicity": clean(row.get("frequency")) or "monthly",
                "latest_public_period": clean(row.get("period_start")) or clean(row.get("latest_period_raw")),
                "publisher_scope": publisher_scope,
                "target_regions": [
                    {
                        "region_code": target_region_code,
                        "region_name": target_region_name,
                        "target_level": clean(row.get("target_level")),
                        "business_coverage_status": "pending_verify",
                        "source_completeness_status": (
                            "city_source_present" if publisher_scope == "city" else "province_source_only"
                        ),
                        "coverage_note": note,
                    }
                ],
            },
            "audit": {
                "admin_division_version": clean(row.get("admin_division_version")),
                "evidence_url": clean_url(row.get("evidence_url")),
                "review_status": clean(row.get("review_status")) or "draft",
                "source_status": source_status,
                "legacy_status": clean(row.get("status")),
                "bucket": clean(row.get("bucket")),
                "audit_note": note,
            },
        }
    )
    return config


def connector_type_for(row: dict[str, str]) -> str:
    bucket = clean(row.get("bucket"))
    if bucket == "待找源":
        return "not_configured"
    if bucket == "需人工":
        return "manual_upload"
    if clean_url(row.get("url")):
        return "http_site"
    return "manual_upload"


def task_status_for(bucket: str | None) -> str:
    return {
        "可自动采": "ready",
        "需人工": "manual_required",
        "异常待修": "exception",
        "待找源": "needs_source",
    }.get(bucket or "", "pending")


def source_identity(row: dict[str, str]) -> tuple[str, str | None, str | None, str]:
    return (
        clean(row.get("source_type")) or INFO_PRICE_SOURCE_TYPE,
        clean(row.get("province")) or clean(row.get("province_name")),
        clean(row.get("city")) or clean(row.get("target_region_name")),
        clean(row.get("name")) or clean(row.get("source_name")) or "",
    )


def find_source(session: Session, row: dict[str, str]) -> DataSource | None:
    site_id = source_registry_site_id(row)
    if site_id:
        statement = select(DataSource).where(
            DataSource.source_scope == "platform_public",
            DataSource.asset_tenant_code == PLATFORM_PUBLIC_TENANT,
            DataSource.data_domain == COST_INFO_DOMAIN,
        )
        for source in session.scalars(statement).all():
            if ((source.config or {}).get("stable") or {}).get("site_id") == site_id:
                return source

    source_type, province, city, name = source_identity(row)
    statement = select(DataSource).where(
        DataSource.source_scope == "platform_public",
        DataSource.asset_tenant_code == PLATFORM_PUBLIC_TENANT,
        DataSource.source_type == source_type,
        DataSource.province == province,
        DataSource.city == city,
        DataSource.name == name,
    )
    return session.scalars(statement).first()


def apply_source_fields(source: DataSource, row: dict[str, str]) -> None:
    source_type, province, city, name = source_identity(row)
    url = entry_url_for(row)
    source.source_scope = "platform_public"
    source.tenant_code = None
    source.asset_tenant_code = PLATFORM_PUBLIC_TENANT
    source.managed_by = "platform"
    source.source_type = source_type
    source.connector_type = SOURCE_REGISTRY_CONNECTOR
    source.name = name
    source.base_url = url
    source.url = url
    source.url_alt = clean_url(row.get("url2"))
    source.province = province
    source.city = city
    source.region_code = clean(row.get("target_region_code")) or clean(row.get("region_code"))
    source.data_domain = COST_INFO_DOMAIN
    source.auth_secret_ref = None
    source.format = clean(row.get("format"))
    source.downloadable = parse_downloadable(row.get("downloadable"))
    source.bucket = clean(row.get("bucket"))
    source.owner = clean(row.get("owner"))
    source.reviewer = clean(row.get("reviewer"))
    source.remark = clean(row.get("remark"))
    source.frequency = clean(row.get("frequency"))
    source.status = "pending_verify"
    source.config = build_registry_config(source, row)
    source.schedule_policy = schedule_policy_for(row)
    source.updated_at = utcnow()


def find_task(session: Session, source_id: str, batch_id: str) -> CollectionTask | None:
    return session.scalars(
        select(CollectionTask).where(
            CollectionTask.source_id == source_id,
            CollectionTask.batch_id == batch_id,
        )
    ).first()


def apply_task_fields(task: CollectionTask, source: DataSource, row: dict[str, str], batch_id: str) -> None:
    task.source_id = source.source_id
    task.asset_tenant_code = source.asset_tenant_code
    task.operator_type = "platform_ops"
    task.operator_id = "ledger_import"
    task.task_type = "ledger_import"
    task.trigger_type = "import"
    task.batch_id = batch_id
    task.data_domain = COST_INFO_DOMAIN
    task.status = task_status_for(source.bucket)
    task.period_raw = clean(row.get("latest_period_raw"))
    task.period_start = clean(row.get("period_start"))
    task.period_end = clean(row.get("period_end"))
    task.period_note = clean(row.get("period_note"))
    task.config_override = {
        **(task.config_override or {}),
        "import_version": IMPORT_VERSION,
        "ledger_seq": int(clean(row.get("seq")) or 0),
        "site_id": source_registry_site_id(row),
        "source_status": row_source_status(row),
        "review_status": clean(row.get("review_status")) or "draft",
    }
    task.created_by = task.created_by or "ledger_import"
    task.updated_at = utcnow()


def import_info_price_site_ledger(session: Session, csv_path: str | Path) -> ImportResult:
    path = Path(csv_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    bucket_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    created_sources = updated_sources = created_tasks = updated_tasks = 0

    for row in rows:
        bucket_counts[clean(row.get("bucket")) or ""] += 1
        status_counts[clean(row.get("status")) or ""] += 1

        source = find_source(session, row)
        if source is None:
            source = DataSource()
            session.add(source)
            created_sources += 1
        else:
            updated_sources += 1
        apply_source_fields(source, row)
        session.flush()

        batch_id = f"{IMPORT_VERSION}:{clean(row.get('seq')) or source.source_id}"
        task = find_task(session, source.source_id, batch_id)
        if task is None:
            task = CollectionTask()
            session.add(task)
            created_tasks += 1
        else:
            updated_tasks += 1
        apply_task_fields(task, source, row, batch_id)

    session.commit()
    return ImportResult(
        total_rows=len(rows),
        created_sources=created_sources,
        updated_sources=updated_sources,
        created_tasks=created_tasks,
        updated_tasks=updated_tasks,
        bucket_counts=dict(bucket_counts),
        status_counts=dict(status_counts),
        period_start_count=sum(1 for row in rows if clean(row.get("period_start"))),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import information-price site ledger CSV.")
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()

    init_db()
    Session = get_session_factory()
    with Session() as session:
        result = import_info_price_site_ledger(session, args.csv_path)

    print(f"total_rows={result.total_rows}")
    print(f"created_sources={result.created_sources} updated_sources={result.updated_sources}")
    print(f"created_tasks={result.created_tasks} updated_tasks={result.updated_tasks}")
    print(f"period_start_count={result.period_start_count}")
    print(f"bucket_counts={result.bucket_counts}")
    print(f"status_counts={result.status_counts}")


if __name__ == "__main__":
    main()
