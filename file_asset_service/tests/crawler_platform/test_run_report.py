from datetime import UTC, datetime, timedelta

from app.crawler_platform.run_report import WorkerReport, finalize_worker_report


def test_finalize_worker_report_marks_healthy_when_no_failures_or_red_lines():
    started_at = datetime(2026, 6, 26, 2, 0, tzinfo=UTC)
    report = WorkerReport(run_id="run_20260626020000_abcd1234", trigger="manual", run_started_at=started_at.isoformat())

    finalize_worker_report(report, finished_at=started_at + timedelta(seconds=7))

    assert report.run_type == "worker"
    assert report.data_domain == "cost_info"
    assert report.duration_seconds == 7
    assert report.health_status == "healthy"
    assert report.health_reason == ""


def test_finalize_worker_report_marks_degraded_for_retries_or_failed_items():
    report = WorkerReport(
        run_id="run_20260626020000_abcd1234",
        trigger="manual",
        run_started_at="2026-06-26T02:00:00+00:00",
        retry_count=1,
        failed_count=2,
    )

    finalize_worker_report(report, finished_at=datetime(2026, 6, 26, 2, 0, 3, tzinfo=UTC))

    assert report.health_status == "degraded"
    assert report.health_reason == "1 retry task(s), 2 failed item(s)"


def test_finalize_worker_report_marks_critical_for_dead_letters():
    report = WorkerReport(
        run_id="run_20260626020000_abcd1234",
        trigger="manual",
        run_started_at="2026-06-26T02:00:00+00:00",
        dead_letter_count=1,
    )

    finalize_worker_report(report, finished_at=datetime(2026, 6, 26, 2, 0, 3, tzinfo=UTC))

    assert report.health_status == "critical"
    assert report.health_reason == "1 task(s) exceeded max attempts"


def test_finalize_worker_report_marks_critical_for_red_line_violations():
    report = WorkerReport(
        run_id="run_20260626020000_abcd1234",
        trigger="manual",
        run_started_at="2026-06-26T02:00:00+00:00",
        file_processing_rows_created=1,
        file_processing_passes=False,
        red_line_violations=[{"task_id": "task-1"}],
    )

    finalize_worker_report(report, finished_at=datetime(2026, 6, 26, 2, 0, 3, tzinfo=UTC))

    assert report.health_status == "critical"
    assert report.health_reason == "file_processing rows created during cost_info Layer 0 worker run"


def test_worker_report_to_dict_keeps_stdout_json_shape():
    report = WorkerReport(
        run_id="run_20260626020000_abcd1234",
        trigger="manual",
        run_started_at="2026-06-26T02:00:00+00:00",
        leased_count=2,
        per_source=[{"site_id": "cost_info.sc.deyang"}],
    )

    payload = report.to_dict()

    assert payload["run_type"] == "worker"
    assert payload["data_domain"] == "cost_info"
    assert payload["summary"]["leased_count"] == 2
    assert payload["per_source"] == [{"site_id": "cost_info.sc.deyang"}]
    assert payload["red_lines"]["file_processing_rows_created"] == 0
    assert payload["red_lines"]["file_processing_passes"] is True
