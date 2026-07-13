from __future__ import annotations

from sqlalchemy.orm import object_session

from app.adapters.base_cost_info_adapter import DiscoveredIssue
from app.archive_rules import build_cost_info_business_key
from app.zhangye_cost_info import ingest_zhangye_cost_info_issue, list_zhangye_cost_info_issues


class ZhangyeZipAdapter:
    adapter_kind = "zhangye_zip"

    def __init__(self) -> None:
        self._rows_by_key: dict[str, dict] = {}
        self._client = None

    def discover(self, source, task, client) -> list[DiscoveredIssue]:
        parser = _active_parser(source)
        max_items = int((source.schedule_policy or {}).get("max_items_per_run") or 5)
        rows = list_zhangye_cost_info_issues(client, parser=parser, max_items=max_items)
        rows = _limit_rows(rows, source)
        self._rows_by_key = {_row_source_item_key(row): row for row in rows}
        self._client = client
        return [_issue_from_row(row) for row in rows]

    def ingest(self, source, task, issue: DiscoveredIssue, storage) -> None:
        row = self._rows_by_key.get(issue.source_item_key)
        if row is None:
            raise ValueError(f"ZHANGYE_ZIP_ROW_NOT_DISCOVERED: {issue.source_item_key}")
        ingest_zhangye_cost_info_issue(
            _session_for(source, task),
            storage,
            source_id=source.source_id,
            client=self._client,
            row=row,
            actor_id="cost-info-worker:zhangye_zip",
            task_id=task.task_id,
        )

    def business_key(self, source, issue: DiscoveredIssue) -> str:
        row = self._rows_by_key.get(issue.source_item_key)
        if row is None:
            raise ValueError(f"ZHANGYE_ZIP_ROW_NOT_DISCOVERED: {issue.source_item_key}")
        return build_cost_info_business_key(
            source_id=source.source_id,
            region_code=source.region_code,
            period=str(row.get("period_start") or row.get("period") or ""),
            title=_row_title(row),
        )


def _active_parser(source) -> dict:
    config = source.config or {}
    parser_config = config.get("parser") or {}
    active_version = parser_config.get("active_parser_version")
    parser = (parser_config.get("parsers") or {}).get(active_version)
    if not isinstance(parser, dict):
        raise ValueError("ZHANGYE_ZIP_PARSER_CONFIG_MISSING")
    return parser


def _session_for(source, task):
    session = object_session(task) or object_session(source)
    if session is None:
        raise ValueError("SQLALCHEMY_SESSION_REQUIRED")
    return session


def _limit_rows(rows: list[dict], source) -> list[dict]:
    max_items = int((source.schedule_policy or {}).get("max_items_per_run") or len(rows) or 0)
    return rows[:max_items] if max_items else rows


def _issue_from_row(row: dict) -> DiscoveredIssue:
    return DiscoveredIssue(
        source_item_key=_row_source_item_key(row),
        title=_row_title(row),
        publish_date=str(row["publish_date"]) if row.get("publish_date") else None,
        period_raw=str(row.get("period") or _row_title(row)),
        detail_url=str(row["detail_url"]),
        attachment_urls=_attachment_urls(row),
    )


def _row_source_item_key(row: dict) -> str:
    return str(row["source_item_key"])


def _row_title(row: dict) -> str:
    return str(row["title"])


def _attachment_urls(row: dict) -> list[str]:
    attachments = row.get("attachments") if isinstance(row.get("attachments"), list) else []
    return [str(item["url"]) for item in attachments if isinstance(item, dict) and item.get("url")]
