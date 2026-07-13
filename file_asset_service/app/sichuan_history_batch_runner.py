from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import random
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collection import create_data_source
from app.database import get_session_factory, init_db
from app.info_price_coverage import build_coverage_matrix
from app.mianyang_cost_info import mianyang_cost_info_source_config
from app.models import Archive, ArchiveFile, CollectionTask, DataSource, FileProcessing
from app.sichuan_history_backfill import fanout_sichuan_history_tasks, run_due_sichuan_history_tasks
from app.sichuan_pdf_cost_info import (
    dazhou_cost_info_source_config,
    deyang_cost_info_source_config,
    leshan_cost_info_source_config,
    luzhou_cost_info_source_config,
    panzhihua_cost_info_source_config,
    suining_cost_info_source_config,
)
from app.source_adapter import SourceRegistryHttpClient
from app.storage import get_object_store
from app.zigong_cost_info import zigong_cost_info_source_config

SICHUAN_PROD_BATCH_REGION_CODES = ("510300", "510700", "510500", "510600", "510900", "510400", "511100", "511700")


@dataclass(frozen=True)
class BatchSourceDef:
    region_code: str
    name: str
    city: str
    config_factory: object


SOURCE_DEFS = (
    BatchSourceDef("510300", "自贡市人民政府-材料价格", "自贡市", zigong_cost_info_source_config),
    BatchSourceDef("510700", "绵阳市住房和城乡建设委员会-材料价格", "绵阳市", mianyang_cost_info_source_config),
    BatchSourceDef("510500", "泸州市住房和城乡建设局", "泸州市", luzhou_cost_info_source_config),
    BatchSourceDef("510600", "德阳市人民政府-公示公告", "德阳市", deyang_cost_info_source_config),
    BatchSourceDef("510900", "遂宁市住房和城乡建设局", "遂宁市", suining_cost_info_source_config),
    BatchSourceDef("510400", "攀枝花市住房和城乡建设局", "攀枝花市", panzhihua_cost_info_source_config),
    BatchSourceDef("511100", "乐山市住房和城乡建设局", "乐山市", leshan_cost_info_source_config),
    BatchSourceDef("511700", "达州市住房和城乡建设局", "达州市", dazhou_cost_info_source_config),
)


def upsert_batch_sources(session: Session) -> list[DataSource]:
    sources: list[DataSource] = []
    for source_def in SOURCE_DEFS:
        config = source_def.config_factory()
        source = _find_source(session, region_code=source_def.region_code, site_id=config["stable"]["site_id"])
        if source is None:
            source = create_data_source(
                session,
                source_scope="platform_public",
                tenant_code=None,
                managed_by="platform",
                source_type="info_price",
                connector_type="source_registry",
                name=source_def.name,
                data_domain="cost_info",
                base_url=config["stable"]["entry_url"],
                url=config["stable"]["entry_url"],
                province="四川",
                city=source_def.city,
                region_code=source_def.region_code,
                format=_source_format(config),
                downloadable=True,
                bucket="可自动采",
                frequency="monthly",
                config=config,
                created_by="sichuan-history-batch",
            )
        else:
            source.base_url = config["stable"]["entry_url"]
            source.url = config["stable"]["entry_url"]
            source.province = "四川"
            source.city = source_def.city
            source.region_code = source_def.region_code
            source.format = _source_format(config)
            source.downloadable = True
            source.bucket = "可自动采"
            source.frequency = "monthly"
            source.config = config
            session.add(source)
            session.commit()
            session.refresh(source)
        sources.append(source)
    return sources


def fanout_batch(
    session: Session,
    sources: list[DataSource],
    *,
    min_delay_seconds: int,
    jitter_seconds: int,
    max_tasks_per_source: int | None = None,
    client_min_interval_seconds: int | None = None,
    client_jitter_seconds: int | None = None,
) -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    base_time = datetime.now(UTC) + timedelta(seconds=2)
    for source in sources:
        client = _client_for(
            source,
            min_interval_seconds=client_min_interval_seconds,
            jitter_seconds=client_jitter_seconds,
        )
        report = fanout_sichuan_history_tasks(
            session,
            source_id=source.source_id,
            client=client,
            mode="history_backfill",
            base_time=base_time,
            min_delay_seconds=min_delay_seconds,
            jitter_seconds=jitter_seconds,
            max_tasks=max_tasks_per_source,
            as_of_date=datetime.now(UTC).date().isoformat(),
        )
        reports.append(report)
        print("fanout", source.region_code, source.name, report, flush=True)
    return reports


def run_worker_until_idle(
    session: Session,
    sources: list[DataSource],
    *,
    retry_delay_seconds: int,
    client_min_interval_seconds: int | None = None,
    client_jitter_seconds: int | None = None,
    max_idle_seconds: int = 60,
    sleep_seconds: int = 1,
    stop_on_block_signal: bool = True,
) -> dict[str, object]:
    storage = get_object_store()
    clients = {
        source.source_id: _client_for(
            source,
            min_interval_seconds=client_min_interval_seconds,
            jitter_seconds=client_jitter_seconds,
        )
        for source in sources
    }
    source_by_id = {source.source_id: source for source in sources}
    totals = {"leased_count": 0, "done_count": 0, "retry_count": 0, "dead_letter_count": 0}
    idle_started_at: datetime | None = None
    start = datetime.now(UTC)
    while True:
        now = datetime.now(UTC)
        if stop_on_block_signal:
            blocked = _blocked_sources(session, sources)
            if blocked:
                print("block_signal", blocked, flush=True)
                break
        made_progress = False
        for source in _shuffled_sources(sources):
            report = run_due_sichuan_history_tasks(
                session,
                storage,
                source_id=source.source_id,
                client=clients[source.source_id],
                now=now,
                limit=1,
                retry_delay_seconds=retry_delay_seconds,
            )
            if report["leased_count"]:
                made_progress = True
                for key in totals:
                    totals[key] += report[key]
                print("worker", source.region_code, source.name, report, flush=True)
        if not _remaining_tasks(session, sources):
            break
        if made_progress:
            idle_started_at = None
        elif idle_started_at is None:
            idle_started_at = now
        elif (now - idle_started_at).total_seconds() >= max_idle_seconds:
            print("idle_wait", _next_due_summary(session, source_by_id), flush=True)
            idle_started_at = now
        time.sleep(sleep_seconds)

    elapsed_seconds = int((datetime.now(UTC) - start).total_seconds())
    return {**totals, "elapsed_seconds": elapsed_seconds}


def verify_batch(session: Session, sources: list[DataSource]) -> dict[str, object]:
    source_ids = [source.source_id for source in sources]
    archive_rows = list(
        session.scalars(
            select(Archive).where(
                Archive.source_id.in_(source_ids),
                Archive.domain_type == "cost_info",
                Archive.channel_type == "crawler",
            )
        ).all()
    )
    periods_by_region: dict[str, set[str]] = {source.region_code: set() for source in sources if source.region_code}
    for archive in archive_rows:
        metadata = archive.metadata_payload or {}
        period = _cell_value(metadata.get("period") or metadata.get("period_start"))
        region_code = _cell_value(metadata.get("coverage_region_code")) or archive.region_code
        if period:
            for compatible_region_code in _compatible_verify_region_codes(str(region_code)):
                if compatible_region_code in periods_by_region:
                    periods_by_region[compatible_region_code].add(str(period))
    task_counts = _task_counts(session, sources)
    file_processing_count = session.scalar(select(FileProcessing).where(FileProcessing.file_id.is_not(None)).count()) if False else _file_processing_count(session, source_ids)
    matrix = build_coverage_matrix(session, start_period="2023-01", end_period=datetime.now(UTC).strftime("%Y-%m"), province_code="510000")
    matrix_present = {
        source.region_code: len(
            [
                row
                for row in matrix
                if row.coverage_region_code == source.region_code and row.source_completeness_status in {"city_source_present", "dual_source"}
            ]
        )
        for source in sources
    }
    return {
        "archive_count": len(archive_rows),
        "periods_by_region": {region: sorted(periods) for region, periods in periods_by_region.items()},
        "task_counts": task_counts,
        "file_processing_count": file_processing_count,
        "coverage_present_cells": matrix_present,
    }


def _find_source(session: Session, *, region_code: str, site_id: str) -> DataSource | None:
    candidates = session.scalars(
        select(DataSource).where(
            DataSource.source_scope == "platform_public",
            DataSource.source_type == "info_price",
            DataSource.data_domain == "cost_info",
            DataSource.region_code == region_code,
        )
    ).all()
    for source in candidates:
        stable = source.config.get("stable") if isinstance(source.config, dict) else {}
        if stable.get("site_id") == site_id:
            return source
    return candidates[0] if candidates else None


def _source_format(config: dict) -> str:
    mode = config.get("source_shape", {}).get("source_attachment_mode")
    if mode == "zip_package":
        return "zip"
    if mode == "xls_attachment":
        return "xls"
    return "pdf"


def _client_for(
    source: DataSource,
    *,
    min_interval_seconds: int | None = None,
    jitter_seconds: int | None = None,
) -> SourceRegistryHttpClient:
    config = source.config or {}
    entry_url = config.get("stable", {}).get("entry_url") or source.url or source.base_url
    queue = config.get("ops", {}).get("queue", {}) if isinstance(config.get("ops"), dict) else {}
    http = config.get("ops", {}).get("http", {}) if isinstance(config.get("ops"), dict) else {}
    configured_interval = int(queue.get("min_delay_seconds") or 0)
    configured_jitter = int(queue.get("jitter_seconds") or 0)
    return SourceRegistryHttpClient(
        referer=str(entry_url),
        timeout=45,
        user_agent=str(http.get("user_agent") or "Mozilla/5.0 source-registry-crawler/1.0"),
        min_interval_seconds=max(5, min_interval_seconds if min_interval_seconds is not None else configured_interval),
        jitter_seconds=max(0, jitter_seconds if jitter_seconds is not None else configured_jitter),
    )


def _select_sources(sources: list[DataSource], region_codes: str | None) -> list[DataSource]:
    if not region_codes:
        return sources
    requested = [code.strip() for code in region_codes.split(",") if code.strip()]
    by_region = {source.region_code: source for source in sources}
    missing = [code for code in requested if code not in by_region]
    if missing:
        raise ValueError(f"SICHUAN_BATCH_SOURCE_MISSING: {','.join(missing)}")
    return [by_region[code] for code in requested]


def _remaining_tasks(session: Session, sources: list[DataSource]) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(CollectionTask)
            .where(CollectionTask.source_id.in_([source.source_id for source in sources]))
            .where(CollectionTask.task_type == "crawl_issue")
            .where(CollectionTask.status.in_(["pending", "running"]))
        )
        or 0
    )


def _task_counts(session: Session, sources: list[DataSource]) -> dict[str, dict[str, int]]:
    rows = session.scalars(
        select(CollectionTask)
        .where(CollectionTask.source_id.in_([source.source_id for source in sources]))
        .where(CollectionTask.task_type == "crawl_issue")
    ).all()
    source_by_id = {source.source_id: source for source in sources}
    result: dict[str, dict[str, int]] = {}
    for task in rows:
        region_code = source_by_id[task.source_id].region_code or task.source_id
        result.setdefault(region_code, {})
        result[region_code][task.status] = result[region_code].get(task.status, 0) + 1
    return result


def _file_processing_count(session: Session, source_ids: list[str]) -> int:
    return int(
        session.scalar(
            select(func.count(func.distinct(FileProcessing.processing_id)))
            .select_from(FileProcessing)
            .join(ArchiveFile, ArchiveFile.file_id == FileProcessing.file_id)
            .join(Archive, Archive.archive_id == ArchiveFile.archive_id)
            .where(Archive.source_id.in_(source_ids))
        )
        or 0
    )


def _blocked_sources(session: Session, sources: list[DataSource]) -> list[dict[str, object]]:
    blocked = []
    for source in sources:
        recent_failed = session.scalars(
            select(CollectionTask)
            .where(CollectionTask.source_id == source.source_id)
            .where(CollectionTask.task_type == "crawl_issue")
            .where(CollectionTask.status.in_(["pending", "failed"]))
            .where(CollectionTask.error_message.is_not(None))
            .order_by(CollectionTask.updated_at.desc())
            .limit(5)
        ).all()
        messages = " ".join(task.error_message or "" for task in recent_failed)
        if any(token in messages for token in ("429", "503", "验证码", "captcha", "forbidden", "Forbidden")) and len(recent_failed) >= 3:
            blocked.append({"region_code": source.region_code, "name": source.name, "errors": len(recent_failed)})
    return blocked


def _next_due_summary(session: Session, source_by_id: dict[str, DataSource]) -> list[dict[str, object]]:
    rows = session.scalars(
        select(CollectionTask)
        .where(CollectionTask.source_id.in_(list(source_by_id)))
        .where(CollectionTask.task_type == "crawl_issue")
        .where(CollectionTask.status == "pending")
        .order_by(CollectionTask.scheduled_at)
        .limit(5)
    ).all()
    return [
        {
            "region_code": source_by_id[task.source_id].region_code,
            "period": task.period_start,
            "scheduled_at": task.scheduled_at.isoformat() if task.scheduled_at else None,
            "attempt": task.attempt,
        }
        for task in rows
    ]


def _shuffled_sources(sources: list[DataSource]) -> list[DataSource]:
    result = list(sources)
    random.shuffle(result)
    return result


def _cell_value(cell: object) -> object:
    if isinstance(cell, dict) and "value" in cell:
        return cell["value"]
    return cell


def _compatible_verify_region_codes(region_code: str) -> list[str]:
    codes = [region_code]
    if "-" in region_code:
        parent_region_code = region_code.split("-", 1)[0]
        if len(parent_region_code) == 6 and parent_region_code.isdigit():
            codes.append(parent_region_code)
    return list(dict.fromkeys(codes))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fanout", "worker", "verify", "all"], default="all")
    parser.add_argument("--min-delay-seconds", type=int, default=5)
    parser.add_argument("--jitter-seconds", type=int, default=5)
    parser.add_argument("--retry-delay-seconds", type=int, default=120)
    parser.add_argument("--max-tasks-per-source", type=int)
    parser.add_argument("--region-codes")
    parser.add_argument("--client-min-interval-seconds", type=int)
    parser.add_argument("--client-jitter-seconds", type=int)
    args = parser.parse_args()

    init_db()
    Session = get_session_factory()
    with Session() as session:
        sources = _select_sources(upsert_batch_sources(session), args.region_codes)
        print("sources", [(source.region_code, source.source_id, source.name) for source in sources], flush=True)
        if args.mode in {"fanout", "all"}:
            fanout_batch(
                session,
                sources,
                min_delay_seconds=args.min_delay_seconds,
                jitter_seconds=args.jitter_seconds,
                max_tasks_per_source=args.max_tasks_per_source,
                client_min_interval_seconds=args.client_min_interval_seconds,
                client_jitter_seconds=args.client_jitter_seconds,
            )
        if args.mode in {"worker", "all"}:
            report = run_worker_until_idle(
                session,
                sources,
                retry_delay_seconds=args.retry_delay_seconds,
                client_min_interval_seconds=args.client_min_interval_seconds,
                client_jitter_seconds=args.client_jitter_seconds,
            )
            print("worker_total", report, flush=True)
        if args.mode in {"verify", "all"}:
            print("verify", verify_batch(session, sources), flush=True)


if __name__ == "__main__":
    main()
