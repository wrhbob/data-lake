from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class WorkerReport:
    run_id: str
    trigger: str
    run_started_at: str
    worker_id: str = ""
    run_finished_at: str = ""
    duration_seconds: int = 0
    run_type: str = "worker"
    data_domain: str = "cost_info"
    leased_count: int = 0
    done_count: int = 0
    retry_count: int = 0
    dead_letter_count: int = 0
    archive_created_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    manual_recovery_count: int = 0
    per_source: list[dict] = field(default_factory=list)
    file_processing_rows_created: int = 0
    file_processing_passes: bool = True
    red_line_violations: list[dict] = field(default_factory=list)
    health_status: str = "healthy"
    health_reason: str = ""

    def to_dict(self) -> dict:
        payload = dataclasses.asdict(self)
        summary_keys = [
            "leased_count",
            "done_count",
            "retry_count",
            "dead_letter_count",
            "archive_created_count",
            "duplicate_count",
            "failed_count",
            "manual_recovery_count",
        ]
        payload["summary"] = {key: payload[key] for key in summary_keys}
        payload["red_lines"] = {
            "file_processing_rows_created": payload["file_processing_rows_created"],
            "file_processing_passes": payload["file_processing_passes"],
            "violations": payload["red_line_violations"],
        }
        return payload


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: str) -> datetime:
    return ensure_aware_utc(datetime.fromisoformat(value))


def finalize_worker_report(report: WorkerReport, *, finished_at: datetime) -> WorkerReport:
    finished = ensure_aware_utc(finished_at)
    started = _parse_timestamp(report.run_started_at)
    report.run_finished_at = finished.isoformat()
    report.duration_seconds = int((finished - started).total_seconds())
    apply_worker_health(report)
    return report


def apply_worker_health(report: WorkerReport) -> None:
    if not report.file_processing_passes:
        report.health_status = "critical"
        report.health_reason = "file_processing rows created during cost_info Layer 0 worker run"
        return
    if report.dead_letter_count:
        report.health_status = "critical"
        report.health_reason = f"{report.dead_letter_count} task(s) exceeded max attempts"
        return
    if report.retry_count or report.failed_count:
        report.health_status = "degraded"
        report.health_reason = f"{report.retry_count} retry task(s), {report.failed_count} failed item(s)"
        return
    report.health_status = "healthy"
    report.health_reason = ""
