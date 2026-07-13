from __future__ import annotations

from urllib.error import HTTPError

from app.adapters.base_cost_info_adapter import DiscoveredIssue
from app.chongqing_cost_info import (
    CHONGQING_ENTRY_URL,
    _pdf_attachment_from_file_path,
    _business_key_for_row,
    _row_file_path,
    _row_publish_date,
    _row_source_item_key,
    _row_title,
    bootstrap_chongqing_cookie_with_playwright,
    ingest_chongqing_pdf_row,
    list_chongqing_pdf_issues,
)
from sqlalchemy.orm import object_session


class ChongqingPdfAdapter:
    adapter_kind = "chongqing_pdf"

    def __init__(self) -> None:
        self._rows_by_key: dict[str, dict] = {}
        self._parser: dict | None = None
        self._client = None

    def discover(self, source, task, client) -> list[DiscoveredIssue]:
        parser = _active_parser(source)
        rows = _list_rows_with_optional_cookie_bootstrap(source, client, parser)
        rows = _limit_rows(rows, source)
        self._rows_by_key = {_source_item_key(row): row for row in rows}
        self._parser = parser
        self._client = client
        return [_issue_from_row(row, parser) for row in rows]

    def ingest(self, source, task, issue: DiscoveredIssue, storage) -> None:
        parser = self._parser or _active_parser(source)
        row = self._rows_by_key.get(issue.source_item_key)
        if row is None:
            raise ValueError(f"CHONGQING_PDF_ROW_NOT_DISCOVERED: {issue.source_item_key}")
        ingest_chongqing_pdf_row(
            _session_for(source, task),
            storage,
            source_id=source.source_id,
            client=self._client,
            row=row,
            parser=parser,
            actor_id="cost-info-worker:chongqing_pdf",
            task_id=task.task_id,
        )

    def business_key(self, source, issue: DiscoveredIssue) -> str:
        row = self._rows_by_key.get(issue.source_item_key)
        if row is None:
            raise ValueError(f"CHONGQING_PDF_ROW_NOT_DISCOVERED: {issue.source_item_key}")
        return _business_key_for_row(source_id=source.source_id, region_code=source.region_code, row=row)


def _active_parser(source) -> dict:
    config = source.config or {}
    parser_config = config.get("parser") or {}
    active_version = parser_config.get("active_parser_version")
    parser = (parser_config.get("parsers") or {}).get(active_version)
    if not isinstance(parser, dict):
        raise ValueError("CHONGQING_PDF_PARSER_CONFIG_MISSING")
    return parser


def _list_rows_with_optional_cookie_bootstrap(source, client, parser: dict) -> list[dict]:
    try:
        return list_chongqing_pdf_issues(client, parser=parser)
    except HTTPError as exc:
        if exc.code != 412 or not _requires_cookie_bootstrap(source):
            raise
        cookie = bootstrap_chongqing_cookie_with_playwright()
        _set_client_cookie(client, cookie)
        return list_chongqing_pdf_issues(client, parser=parser)


def _requires_cookie_bootstrap(source) -> bool:
    ops = (source.config or {}).get("ops") or {}
    return ops.get("session_bootstrap") == "playwright_cookie_then_http"


def _set_client_cookie(client, cookie: str) -> None:
    target = getattr(client, "inner", client)
    if not hasattr(target, "cookie"):
        raise ValueError("CHONGQING_COOKIE_BOOTSTRAP_CLIENT_UNSUPPORTED")
    target.cookie = cookie


def _session_for(source, task):
    session = object_session(task) or object_session(source)
    if session is None:
        raise ValueError("SQLALCHEMY_SESSION_REQUIRED")
    return session


def _limit_rows(rows: list[dict], source) -> list[dict]:
    max_items = int((source.schedule_policy or {}).get("max_items_per_run") or len(rows) or 0)
    return rows[:max_items] if max_items else rows


def _source_item_key(row: dict) -> str:
    key = _row_source_item_key(row)
    if not key:
        raise ValueError("CHONGQING_PDF_SOURCE_ITEM_KEY_MISSING")
    return key


def _issue_from_row(row: dict, parser: dict) -> DiscoveredIssue:
    _file_name, download_url = _pdf_attachment_from_file_path(_row_file_path(row), parser["download_base_url"])
    return DiscoveredIssue(
        source_item_key=_source_item_key(row),
        title=_row_title(row),
        publish_date=_row_publish_date(row),
        period_raw=_row_title(row),
        detail_url=CHONGQING_ENTRY_URL,
        attachment_urls=[download_url],
    )
