from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collection import create_collection_task
from app.cost_info_scheduler import adapter_kind, config_digest, source_site_id
from app.info_price_coverage import build_coverage_matrix, build_monthly_periods
from app.models import CollectionTask, DataSource

DATA_DOMAIN = "cost_info"
BACKFILL_TASK_TYPE = "crawl_issue"


def run_coverage_backfill(
    session: Session,
    *,
    region_code: str,
    start_period: str,
    end_period: str,
    source_id: str | None = None,
    dry_run: bool = True,
    include_covered: bool = False,
) -> dict[str, object]:
    periods = build_monthly_periods(start_period, end_period)
    province_code = _province_code_for_region(region_code)
    matrix_rows = build_coverage_matrix(
        session,
        start_period=start_period,
        end_period=end_period,
        province_code=province_code,
    )
    rows_by_period = {
        row.period: row
        for row in matrix_rows
        if row.coverage_region_code == region_code and row.period in set(periods)
    }

    report = {
        "region_code": region_code,
        "start_period": start_period,
        "end_period": end_period,
        "requested_periods": periods,
        "dry_run": dry_run,
        "include_covered": include_covered,
        "task_created": 0,
        "task_skipped_existing": 0,
        "task_skipped_covered": 0,
        "task_skipped_no_source": 0,
        "batch_ids": [],
        "tasks": [],
        "skipped": [],
    }

    for period in periods:
        row = rows_by_period.get(period)
        if row is None:
            report["task_skipped_no_source"] += 1
            report["skipped"].append({"period": period, "reason": "coverage_cell_not_found"})
            continue
        if row.business_coverage_status == "covered" and not include_covered:
            report["task_skipped_covered"] += 1
            report["skipped"].append({"period": period, "reason": "covered"})
            continue
        source = _source_for_row(session, row.source_ids, requested_source_id=source_id)
        if source is None:
            report["task_skipped_no_source"] += 1
            report["skipped"].append({"period": period, "reason": "source_not_found"})
            continue
        if _matching_pending_task_exists(session, source_id=source.source_id, period=period):
            report["task_skipped_existing"] += 1
            report["skipped"].append({"period": period, "source_id": source.source_id, "reason": "pending_or_running_exists"})
            continue

        task_payload = _task_payload(source, region_code=region_code, period=period)
        if dry_run:
            report["tasks"].append(task_payload)
            continue

        batch_id = f"cost_info:{source_site_id(source)}:backfill:{period}:{uuid4().hex[:6]}"
        task = create_collection_task(
            session,
            source_id=source.source_id,
            operator_type="system",
            task_type=BACKFILL_TASK_TYPE,
            trigger_type="coverage_backfill",
            data_domain=DATA_DOMAIN,
            batch_id=batch_id,
            status="pending",
            period_raw=period,
            period_start=period,
            period_end=period,
            period_note="coverage_backfill",
            config_override=task_payload["config_override"],
            created_by="crawler_coverage_backfill",
        )
        report["task_created"] += 1
        report["batch_ids"].append(task.batch_id)
        created = dict(task_payload)
        created["task_id"] = task.task_id
        created["batch_id"] = task.batch_id
        created["status"] = task.status
        report["tasks"].append(created)
    return report


def _province_code_for_region(region_code: str) -> str:
    text = str(region_code or "").strip()
    if len(text) >= 2 and text[:2].isdigit():
        return f"{text[:2]}0000"
    raise ValueError("region_code must start with a province code")


def _source_for_row(session: Session, source_ids: list[str], *, requested_source_id: str | None) -> DataSource | None:
    candidates = [requested_source_id] if requested_source_id else list(source_ids or [])
    for candidate in candidates:
        if not candidate:
            continue
        source = session.get(DataSource, candidate)
        if source is not None and source.status == "active":
            return source
    return None


def _matching_pending_task_exists(session: Session, *, source_id: str, period: str) -> bool:
    statement = (
        select(CollectionTask)
        .where(
            CollectionTask.source_id == source_id,
            CollectionTask.data_domain == DATA_DOMAIN,
            CollectionTask.task_type == BACKFILL_TASK_TYPE,
            CollectionTask.status.in_(["pending", "running"]),
        )
        .order_by(CollectionTask.created_at.desc())
        .limit(100)
    )
    for task in session.scalars(statement).all():
        if task.period_start == period:
            return True
        override = task.config_override or {}
        coverage_backfill = override.get("coverage_backfill") if isinstance(override, dict) else {}
        if isinstance(coverage_backfill, dict) and coverage_backfill.get("period") == period:
            return True
    return False


def _task_payload(source: DataSource, *, region_code: str, period: str) -> dict[str, object]:
    site_id = source_site_id(source)
    payload = {
        "source_id": source.source_id,
        "site_id": site_id,
        "adapter_kind": adapter_kind(source),
        "period": period,
        "region_code": region_code,
        "task_type": BACKFILL_TASK_TYPE,
    }
    payload["config_override"] = {
        "site_id": site_id,
        "adapter_kind": adapter_kind(source),
        "active_parser_version": ((source.config or {}).get("parser") or {}).get("active_parser_version"),
        "config_digest": config_digest(source.config or {}),
        "coverage_backfill": {
            "region_code": region_code,
            "period": period,
            "requested_periods": [period],
            "source_id": source.source_id,
            "site_id": site_id,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    return payload
