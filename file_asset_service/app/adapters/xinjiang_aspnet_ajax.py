from __future__ import annotations

from app.adapters.base_cost_info_adapter import DiscoveredIssue
from app.adapters.history_scope import history_page_limit, is_history_backfill
from app.xinjiang_cost_info import (
    _detail_url,
    _business_key_for_row,
    _row_publish_date,
    _row_source_item_key,
    _row_title,
    ingest_xinjiang_area_issue,
    list_xinjiang_area_issues,
)
from sqlalchemy.orm import object_session


class XinjiangAspnetAjaxAreaAdapter:
    adapter_kind = "aspnet_ajax_area"

    def __init__(self) -> None:
        self._rows_by_key: dict[str, dict] = {}
        self._parser: dict | None = None
        self._client = None

    def discover(self, source, task, client) -> list[DiscoveredIssue]:
        parser = _active_parser(source)
        max_items = int((source.schedule_policy or {}).get("max_items_per_run") or 20)
        if is_history_backfill(task):
            rows = _list_all_pages(
                client,
                parser=parser,
                page_size=max(1, int(parser.get("page_size") or 100)),
                page_limit=history_page_limit(source, task, parser),
            )
        else:
            issue_list = list_xinjiang_area_issues(client, parser=parser, page=1, page_size=max_items)
            rows = issue_list.rows[:max_items]
        self._rows_by_key = {_row_source_item_key(row): row for row in rows}
        self._parser = parser
        self._client = client
        return [_issue_from_row(row, parser) for row in rows]

    def ingest(self, source, task, issue: DiscoveredIssue, storage) -> None:
        parser = self._parser or _active_parser(source)
        row = self._rows_by_key.get(issue.source_item_key)
        if row is None:
            raise ValueError(f"XINJIANG_ASPNET_ROW_NOT_DISCOVERED: {issue.source_item_key}")
        ingest_xinjiang_area_issue(
            _session_for(source, task),
            storage,
            source_id=source.source_id,
            client=self._client,
            row=row,
            parser=parser,
            actor_id="cost-info-worker:aspnet_ajax_area",
            task_id=task.task_id,
        )

    def business_key(self, source, issue: DiscoveredIssue) -> str:
        row = self._rows_by_key.get(issue.source_item_key)
        if row is None:
            raise ValueError(f"XINJIANG_ASPNET_ROW_NOT_DISCOVERED: {issue.source_item_key}")
        return _business_key_for_row(source_id=source.source_id, region_code=source.region_code, row=row)


def _active_parser(source) -> dict:
    config = source.config or {}
    parser_config = config.get("parser") or {}
    active_version = parser_config.get("active_parser_version")
    parser = (parser_config.get("parsers") or {}).get(active_version)
    if not isinstance(parser, dict):
        raise ValueError("XINJIANG_ASPNET_PARSER_CONFIG_MISSING")
    return parser


def _session_for(source, task):
    session = object_session(task) or object_session(source)
    if session is None:
        raise ValueError("SQLALCHEMY_SESSION_REQUIRED")
    return session


def _list_all_pages(client, *, parser: dict, page_size: int, page_limit: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    total_pages: int | None = None
    for page in range(1, page_limit + 1):
        issue_list = list_xinjiang_area_issues(client, parser=parser, page=page, page_size=page_size)
        if total_pages is None:
            total_pages = max(1, (max(0, issue_list.total) + page_size - 1) // page_size)
        added = 0
        for row in issue_list.rows:
            key = _row_source_item_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
        if page >= total_pages or (not issue_list.rows and added == 0):
            break
    return rows


def _issue_from_row(row: dict, parser: dict) -> DiscoveredIssue:
    return DiscoveredIssue(
        source_item_key=_row_source_item_key(row),
        title=_row_title(row),
        publish_date=_row_publish_date(row),
        period_raw=_row_title(row),
        detail_url=_detail_url(row, parser),
        attachment_urls=[],
    )
