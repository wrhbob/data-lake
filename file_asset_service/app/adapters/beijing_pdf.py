from __future__ import annotations

from app.adapters.base_cost_info_adapter import DiscoveredIssue
from app.adapters.history_scope import history_page_limit, is_history_backfill
from app.beijing_cost_info import (
    _business_key_for_row,
    _row_attachments,
    _row_detail_url,
    _row_publish_date,
    _row_source_item_key,
    _row_title,
    _period_from_title,
    ingest_beijing_cost_info_issue,
    list_beijing_cost_info_issues,
)
from sqlalchemy.orm import object_session


class BeijingPdfAdapter:
    adapter_kind = "beijing_pdf"

    def __init__(self) -> None:
        self._rows_by_key: dict[str, dict] = {}
        self._parser: dict | None = None
        self._client = None

    def discover(self, source, task, client) -> list[DiscoveredIssue]:
        parser = _parser_for_task(_active_parser(source), task)
        if is_history_backfill(task):
            parser = _with_history_pagination(
                parser,
                max_pages=history_page_limit(source, task, parser, default=8),
            )
        rows = list_beijing_cost_info_issues(client, parser=parser)
        rows = _filter_rows_for_task(rows, task, parser)
        if not _requested_periods(task):
            rows = _limit_rows(rows, source)
        self._rows_by_key = {_row_source_item_key(row): row for row in rows}
        self._parser = parser
        self._client = client
        return [_issue_from_row(row) for row in rows]

    def ingest(self, source, task, issue: DiscoveredIssue, storage) -> None:
        parser = self._parser or _active_parser(source)
        row = self._rows_by_key.get(issue.source_item_key)
        if row is None:
            raise ValueError(f"BEIJING_PDF_ROW_NOT_DISCOVERED: {issue.source_item_key}")
        ingest_beijing_cost_info_issue(
            _session_for(source, task),
            storage,
            source_id=source.source_id,
            client=self._client,
            row=row,
            parser=parser,
            actor_id="cost-info-worker:beijing_pdf",
            task_id=task.task_id,
        )

    def business_key(self, source, issue: DiscoveredIssue) -> str:
        row = self._rows_by_key.get(issue.source_item_key)
        if row is None:
            raise ValueError(f"BEIJING_PDF_ROW_NOT_DISCOVERED: {issue.source_item_key}")
        return _business_key_for_row(source_id=source.source_id, region_code=source.region_code, row=row)


def _active_parser(source) -> dict:
    config = source.config or {}
    parser_config = config.get("parser") or {}
    active_version = parser_config.get("active_parser_version")
    parser = (parser_config.get("parsers") or {}).get(active_version)
    if not isinstance(parser, dict):
        raise ValueError("BEIJING_PDF_PARSER_CONFIG_MISSING")
    return parser


def _parser_for_task(parser: dict, task) -> dict:
    requested = _requested_periods(task)
    if not requested:
        return parser
    earliest_requested = min(requested)
    min_period = str(parser.get("min_period") or "")
    adjusted = dict(parser)
    if not min_period or min_period > earliest_requested:
        adjusted["min_period"] = earliest_requested
    adjusted["_requested_periods"] = sorted(requested)
    return _with_history_pagination(adjusted)


def _with_history_pagination(parser: dict, *, max_pages: int | None = None) -> dict:
    adjusted = dict(parser)
    pagination = adjusted.get("pagination") if isinstance(adjusted.get("pagination"), dict) else {}
    configured_limit = max_pages or pagination.get("max_pages") or 8
    adjusted["_history_pagination"] = True
    adjusted["_history_page_limit"] = max(1, min(int(configured_limit), 100))
    return adjusted


def _session_for(source, task):
    session = object_session(task) or object_session(source)
    if session is None:
        raise ValueError("SQLALCHEMY_SESSION_REQUIRED")
    return session


def _limit_rows(rows: list[dict], source) -> list[dict]:
    max_items = int((source.schedule_policy or {}).get("max_items_per_run") or len(rows) or 0)
    return rows[:max_items] if max_items else rows


def _requested_periods(task) -> set[str]:
    override = task.config_override if isinstance(task.config_override, dict) else {}
    coverage_backfill = override.get("coverage_backfill") if isinstance(override, dict) else {}
    if not isinstance(coverage_backfill, dict):
        return set()
    values = coverage_backfill.get("requested_periods") or [coverage_backfill.get("period")]
    if not isinstance(values, list):
        values = [values]
    return {str(value) for value in values if value}


def _filter_rows_for_task(rows: list[dict], task, parser: dict) -> list[dict]:
    requested = _requested_periods(task)
    if not requested:
        return rows
    return [row for row in rows if _row_period(row, parser) in requested]


def _row_period(row: dict, parser: dict) -> str | None:
    if row.get("period"):
        return str(row["period"])
    return _period_from_title(_row_title(row), parser=parser)


def _issue_from_row(row: dict) -> DiscoveredIssue:
    return DiscoveredIssue(
        source_item_key=_row_source_item_key(row),
        title=_row_title(row),
        publish_date=_row_publish_date(row),
        period_raw=_row_title(row),
        detail_url=_row_detail_url(row),
        attachment_urls=[str(item["url"]) for item in _row_attachments(row)],
    )
