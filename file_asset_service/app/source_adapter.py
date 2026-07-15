from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import random
import re
import ssl
import time
from typing import Any, Protocol
from urllib import request
from urllib.parse import quote, urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Archive, DataSource


class CrawlSummary(Protocol):
    source_item_key: str
    publish_date: str | None


class SourceRegistryReadTimeout(TimeoutError):
    pass


class SourceRegistryHttpClient:
    def __init__(
        self,
        *,
        referer: str,
        timeout: int = 60,
        cookie: str | None = None,
        tls_ciphers: str | None = None,
        tls_minimum_version: ssl.TLSVersion | None = None,
        user_agent: str = "Mozilla/5.0 source-registry-crawler/1.0",
        min_interval_seconds: float = 0,
        jitter_seconds: float = 0,
        read_timeout_seconds: float | None = None,
        read_total_timeout_seconds: float | None = None,
        read_chunk_size: int = 1024 * 1024,
        read_retry_attempts: int = 1,
        read_retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.referer = referer
        self.timeout = timeout
        self.cookie = cookie
        self.user_agent = user_agent
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.jitter_seconds = max(0.0, float(jitter_seconds))
        self.read_timeout_seconds = None if read_timeout_seconds is None else max(0.0, float(read_timeout_seconds))
        self.read_total_timeout_seconds = (
            None if read_total_timeout_seconds is None else max(0.0, float(read_total_timeout_seconds))
        )
        self.read_chunk_size = max(1, int(read_chunk_size))
        self.read_retry_attempts = max(0, int(read_retry_attempts))
        self.read_retry_backoff_seconds = max(0.0, float(read_retry_backoff_seconds))
        self._last_request_at: float | None = None
        self._ssl_context = None
        if tls_ciphers or tls_minimum_version:
            self._ssl_context = ssl.create_default_context()
            if tls_minimum_version:
                self._ssl_context.minimum_version = tls_minimum_version
            if tls_ciphers:
                self._ssl_context.set_ciphers(tls_ciphers)

    def get_text(self, url: str) -> str:
        self._throttle()
        req = request.Request(safe_url(url), headers=self._headers(), method="GET")
        with request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as response:
            return response.read().decode("utf-8", errors="replace")

    def post_form(self, url: str, data: dict[str, object]) -> str:
        self._throttle()
        body = urlencode(data, doseq=True).encode("utf-8")
        req = request.Request(
            safe_url(url),
            data=body,
            headers=self._headers(
                content_type="application/x-www-form-urlencoded; charset=UTF-8",
                form_ajax=True,
            ),
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as response:
            return response.read().decode("utf-8", errors="replace")

    def post_form_json(self, url: str, data: dict[str, object]) -> dict:
        return json.loads(self.post_form(url, data))

    def post_form_bytes(self, url: str, data: dict[str, object]) -> tuple[bytes, str | None]:
        body = urlencode(data, doseq=True).encode("utf-8")
        for attempt in range(self.read_retry_attempts + 1):
            self._throttle()
            req = request.Request(
                safe_url(url),
                data=body,
                headers=self._headers(
                    content_type="application/x-www-form-urlencoded; charset=UTF-8",
                    form_ajax=True,
                ),
                method="POST",
            )
            try:
                with request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as response:
                    return self._read_bytes_response(response)
            except SourceRegistryReadTimeout:
                if attempt >= self.read_retry_attempts:
                    raise
                self._sleep_before_read_retry(attempt)
        raise AssertionError("unreachable")

    def post_json(self, url: str, payload: dict[str, Any]) -> dict:
        self._throttle()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            safe_url(url),
            data=body,
            headers=self._headers(content_type="application/json; charset=UTF-8"),
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as response:
            return json.loads(response.read().decode("utf-8-sig"))

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        for attempt in range(self.read_retry_attempts + 1):
            self._throttle()
            req = request.Request(safe_url(url), headers=self._headers(), method="GET")
            try:
                with request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as response:
                    return self._read_bytes_response(response)
            except SourceRegistryReadTimeout:
                if attempt >= self.read_retry_attempts:
                    raise
                self._sleep_before_read_retry(attempt)
        raise AssertionError("unreachable")

    def _read_bytes_response(self, response) -> tuple[bytes, str | None]:
        _set_response_read_timeout(response, self.read_timeout_seconds)
        return (
            read_response_bytes_with_deadline(
                response,
                total_timeout_seconds=self.read_total_timeout_seconds,
                chunk_size=self.read_chunk_size,
            ),
            response.headers.get("Content-Type"),
        )

    def _sleep_before_read_retry(self, attempt: int) -> None:
        if self.read_retry_backoff_seconds <= 0:
            return
        time.sleep(self.read_retry_backoff_seconds * (attempt + 1))

    def _throttle(self) -> None:
        if self.min_interval_seconds <= 0 and self.jitter_seconds <= 0:
            return
        now = time.monotonic()
        if self._last_request_at is not None:
            target_gap = self.min_interval_seconds
            if self.jitter_seconds > 0:
                target_gap += random.uniform(0, self.jitter_seconds)
            wait_seconds = target_gap - (now - self._last_request_at)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        self._last_request_at = time.monotonic()

    def _headers(self, *, content_type: str | None = None, form_ajax: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Referer": safe_url(self.referer),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if form_ajax:
            headers["Accept"] = "application/json, text/javascript, */*; q=0.01"
            headers["X-Requested-With"] = "XMLHttpRequest"
        if content_type:
            headers["Content-Type"] = content_type
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers


def read_response_bytes_with_deadline(
    response,
    *,
    total_timeout_seconds: float | None,
    clock: Callable[[], float] = time.monotonic,
    chunk_size: int = 1024 * 1024,
) -> bytes:
    started_at = clock()
    chunks: list[bytes] = []
    expected_content_length = _response_content_length(response)
    reader = getattr(response, "read1", None) or response.read
    while True:
        if _deadline_exceeded(started_at, total_timeout_seconds, clock):
            raise SourceRegistryReadTimeout(f"ATTACHMENT_DOWNLOAD_TIMEOUT: total timeout {total_timeout_seconds}s")
        try:
            chunk = reader(chunk_size)
        except TypeError:
            chunk = response.read()
            if _deadline_exceeded(started_at, total_timeout_seconds, clock):
                raise SourceRegistryReadTimeout(f"ATTACHMENT_DOWNLOAD_TIMEOUT: total timeout {total_timeout_seconds}s")
            _ensure_complete_response(chunk, expected_content_length)
            return chunk
        if not chunk:
            content = b"".join(chunks)
            _ensure_complete_response(content, expected_content_length)
            return content
        chunks.append(chunk)
        if _deadline_exceeded(started_at, total_timeout_seconds, clock):
            raise SourceRegistryReadTimeout(f"ATTACHMENT_DOWNLOAD_TIMEOUT: total timeout {total_timeout_seconds}s")


def _response_content_length(response) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None
    raw_value = headers.get("Content-Length")
    if raw_value is None:
        return None
    try:
        expected = int(raw_value)
    except (TypeError, ValueError):
        return None
    return expected if expected >= 0 else None


def _ensure_complete_response(content: bytes, expected_content_length: int | None) -> None:
    if expected_content_length is None or len(content) >= expected_content_length:
        return
    raise SourceRegistryReadTimeout(
        f"ATTACHMENT_DOWNLOAD_TIMEOUT: incomplete response {len(content)}/{expected_content_length} bytes"
    )


def _deadline_exceeded(started_at: float, total_timeout_seconds: float | None, clock: Callable[[], float]) -> bool:
    if total_timeout_seconds is None:
        return False
    return clock() - started_at > total_timeout_seconds


def _set_response_read_timeout(response, read_timeout_seconds: float | None) -> None:
    if read_timeout_seconds is None:
        return
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if sock is None or not hasattr(sock, "settimeout"):
        return
    sock.settimeout(read_timeout_seconds)


@dataclass(frozen=True)
class IncrementalChannelReport:
    channel_id: str
    notice_type_raw: object
    notice_family: object
    displaytypeval: object
    ingested_count: int
    skipped_existing_count: int
    cursor_source_item_key: str | None
    cursor_publish_date: str | None
    crawl_lag_days: int | None
    stopped_on_existing: bool
    failed_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_id": self.channel_id,
            "notice_type_raw": self.notice_type_raw,
            "notice_family": self.notice_family,
            "displaytypeval": self.displaytypeval,
            "ingested_count": self.ingested_count,
            "skipped_existing_count": self.skipped_existing_count,
            "cursor_source_item_key": self.cursor_source_item_key,
            "cursor_publish_date": self.cursor_publish_date,
            "crawl_lag_days": self.crawl_lag_days,
            "stopped_on_existing": self.stopped_on_existing,
            "failed_count": self.failed_count,
        }


def run_incremental_channel_crawl(
    *,
    source_id: str,
    channels: Iterable[Mapping[str, object]],
    discover_page: Callable[[Mapping[str, object], int, int], list[CrawlSummary]],
    business_key_for: Callable[[CrawlSummary], str],
    archive_exists: Callable[[str], bool],
    ingest: Callable[[CrawlSummary], object],
    on_ingest_error: Callable[[CrawlSummary, Exception], Mapping[str, object] | None] | None = None,
    selected_channel_ids: Iterable[str] | None = None,
    max_pages: int = 1,
    page_size: int = 10,
    max_items_per_channel: int | None = None,
    as_of_date: str | date | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    selected = set(selected_channel_ids or [])
    channel_reports: list[dict[str, object]] = []
    ingested_count = 0
    skipped_existing_count = 0
    failed_count = 0
    failed_items: list[dict[str, object]] = []

    for channel in channels:
        channel_id = str(channel["channel_id"])
        if selected and channel_id not in selected:
            continue
        channel_ingested = 0
        channel_skipped = 0
        channel_failed = 0
        stop_channel = False
        stopped_on_existing = False
        cursor_source_item_key: str | None = None
        cursor_publish_date: str | None = None

        for page in range(1, max_pages + 1):
            summaries = discover_page(channel, page, page_size)
            if not summaries:
                break
            for summary in summaries:
                if cursor_source_item_key is None:
                    cursor_source_item_key = summary.source_item_key
                    cursor_publish_date = summary.publish_date
                if archive_exists(business_key_for(summary)):
                    skipped_existing_count += 1
                    channel_skipped += 1
                    if stop_on_existing:
                        stop_channel = True
                        stopped_on_existing = True
                        break
                    # Historical backfills must continue beyond records already
                    # in the lake, otherwise the first known newest record
                    # would hide all older notices in the requested window.
                    continue
                try:
                    ingest(summary)
                except Exception as exc:
                    if on_ingest_error is None:
                        raise
                    failure = on_ingest_error(summary, exc)
                    failed_count += 1
                    channel_failed += 1
                    if failure is not None:
                        failed_items.append(dict(failure))
                    continue
                ingested_count += 1
                channel_ingested += 1
                if max_items_per_channel is not None and channel_ingested >= max_items_per_channel:
                    stop_channel = True
                    break
            if stop_channel:
                break

        channel_reports.append(
            IncrementalChannelReport(
                channel_id=channel_id,
                notice_type_raw=channel.get("notice_type_raw"),
                notice_family=channel.get("notice_family"),
                displaytypeval=channel.get("displaytypeval"),
                ingested_count=channel_ingested,
                skipped_existing_count=channel_skipped,
                cursor_source_item_key=cursor_source_item_key,
                cursor_publish_date=cursor_publish_date,
                crawl_lag_days=crawl_lag_days(cursor_publish_date, as_of_date=as_of_date),
                stopped_on_existing=stopped_on_existing,
                failed_count=channel_failed,
            ).to_dict()
        )

    return {
        "source_id": source_id,
        "ingested_count": ingested_count,
        "skipped_existing_count": skipped_existing_count,
        "failed_count": failed_count,
        "failed_items": failed_items,
        "channels": channel_reports,
    }


def require_data_source(session: Session, source_id: str, *, domain_type: str | None = None) -> DataSource:
    source = session.get(DataSource, source_id)
    if source is None:
        raise ValueError(f"DATA_SOURCE_NOT_FOUND: {source_id}")
    if domain_type is not None and source.data_domain != domain_type:
        raise ValueError(f"SOURCE_DOMAIN_MISMATCH: {source.data_domain}")
    return source


def config_digest(config: dict) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def active_parser_version(config: dict) -> str:
    parser = config.get("parser")
    if not isinstance(parser, dict):
        raise ValueError("SOURCE_REGISTRY_PARSER_REQUIRED")
    parser_version = parser.get("active_parser_version")
    if not parser_version:
        raise ValueError("SOURCE_REGISTRY_PARSER_VERSION_REQUIRED")
    return str(parser_version)


def archive_business_key_exists(
    session: Session,
    *,
    tenant_code: str,
    domain_type: str,
    business_key: str,
    version: int = 1,
) -> bool:
    return (
        session.scalar(
            select(Archive.archive_id).where(
                Archive.tenant_code == tenant_code,
                Archive.domain_type == domain_type,
                Archive.business_key == business_key,
                Archive.version == version,
            )
        )
        is not None
    )


def count_opaque_attachments(attachments: Iterable[object], opaque_extensions: set[str] | list[str]) -> int:
    normalized = {normalize_extension(ext) for ext in opaque_extensions}
    return sum(1 for attachment in attachments if file_extension(getattr(attachment, "file_name", "")) in normalized)


def crawl_lag_days(publish_date: str | None, *, as_of_date: str | date | None) -> int | None:
    if not publish_date:
        return None
    published = date.fromisoformat(publish_date)
    if as_of_date is None:
        current = date.today()
    elif isinstance(as_of_date, date):
        current = as_of_date
    else:
        current = date.fromisoformat(as_of_date)
    return max((current - published).days, 0)


def file_extension(file_name: str) -> str:
    match = re.search(r"(\.[^.\\/]+)$", file_name.strip())
    return normalize_extension(match.group(1)) if match else ""


def normalize_extension(value: str) -> str:
    ext = value.lower().strip()
    if not ext:
        return ""
    return ext if ext.startswith(".") else f".{ext}"


def safe_url(url: str) -> str:
    return quote(url, safe=":/?&=%#")
