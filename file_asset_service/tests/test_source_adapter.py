from dataclasses import dataclass

import pytest

from app.source_adapter import (
    SourceRegistryHttpClient,
    SourceRegistryReadTimeout,
    count_opaque_attachments,
    crawl_lag_days,
    read_response_bytes_with_deadline,
    run_incremental_channel_crawl,
)


class FakeResponse:
    def __init__(self, content: bytes, content_type: str = "text/plain"):
        self._content = content
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._content


def test_source_registry_http_client_applies_headers_and_tls(monkeypatch):
    from app import source_adapter

    calls = []

    def fake_urlopen(req, *, timeout, context=None):
        calls.append(
            {
                "url": req.full_url,
                "method": req.get_method(),
                "headers": dict(req.header_items()),
                "data": req.data,
                "timeout": timeout,
                "has_context": context is not None,
            }
        )
        if req.get_method() == "POST" and req.headers.get("Content-type", "").startswith("application/json"):
            return FakeResponse(b'{"ok": true}', "application/json")
        if req.get_method() == "POST":
            return FakeResponse("表单".encode("utf-8"), "text/html")
        if req.full_url.endswith(".bin"):
            return FakeResponse(b"bytes", "application/octet-stream")
        return FakeResponse("页面".encode("utf-8"), "text/html")

    monkeypatch.setattr(source_adapter.request, "urlopen", fake_urlopen)
    client = SourceRegistryHttpClient(
        referer="https://example.com/list.aspx",
        timeout=12,
        cookie="sid=abc",
        tls_ciphers="DEFAULT:@SECLEVEL=1",
    )

    assert client.get_text("https://example.com/详情.aspx?q=一") == "页面"
    assert client.post_form("https://example.com/list.aspx", {"displaytypeval": "1"}) == "表单"
    assert client.post_json("https://example.com/api", {"year": "2026"}) == {"ok": True}
    assert client.get_bytes("https://example.com/file.bin") == (b"bytes", "application/octet-stream")

    assert calls[0]["url"] == "https://example.com/%E8%AF%A6%E6%83%85.aspx?q=%E4%B8%80"
    assert calls[0]["timeout"] == 12
    assert calls[0]["has_context"] is True
    assert calls[0]["headers"]["Referer"] == "https://example.com/list.aspx"
    assert calls[0]["headers"]["Cookie"] == "sid=abc"
    assert calls[1]["data"] == b"displaytypeval=1"
    assert calls[2]["data"] == b'{"year": "2026"}'


def test_source_registry_http_client_throttles_between_requests(monkeypatch):
    from app import source_adapter

    current_time = [100.0]
    sleeps = []

    def fake_urlopen(req, *, timeout, context=None):
        return FakeResponse(b"ok", "text/html")

    def fake_sleep(seconds):
        sleeps.append(seconds)
        current_time[0] += seconds

    monkeypatch.setattr(source_adapter.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(source_adapter.time, "monotonic", lambda: current_time[0])
    monkeypatch.setattr(source_adapter.time, "sleep", fake_sleep)
    client = SourceRegistryHttpClient(
        referer="https://example.com/list",
        min_interval_seconds=5,
        jitter_seconds=0,
    )

    client.get_text("https://example.com/a")
    current_time[0] += 1
    client.get_text("https://example.com/b")

    assert sleeps == [4.0]


def test_read_response_bytes_with_deadline_raises_total_timeout():
    current_time = [100.0]

    class SlowResponse:
        headers = {"Content-Type": "application/octet-stream"}

        def read(self, _chunk_size):
            current_time[0] += 6.0
            return b"slow"

    with pytest.raises(SourceRegistryReadTimeout) as exc:
        read_response_bytes_with_deadline(
            SlowResponse(),
            total_timeout_seconds=5.0,
            clock=lambda: current_time[0],
            chunk_size=4,
        )

    assert "ATTACHMENT_DOWNLOAD_TIMEOUT" in str(exc.value)


def test_read_response_bytes_with_deadline_prefers_read1_for_chunked_streams():
    class ChunkedResponse:
        headers = {"Content-Type": "application/octet-stream"}

        def __init__(self):
            self.chunks = [b"aa", b"bb", b""]
            self.read1_calls = 0

        def read(self, _chunk_size):
            raise AssertionError("read() can wait for a large HTTP chunk; use read1() when available")

        def read1(self, _chunk_size):
            self.read1_calls += 1
            return self.chunks.pop(0)

    response = ChunkedResponse()

    assert (
        read_response_bytes_with_deadline(
            response,
            total_timeout_seconds=5.0,
            clock=lambda: 100.0,
            chunk_size=2,
        )
        == b"aabb"
    )
    assert response.read1_calls == 3


def test_read_response_bytes_with_deadline_rejects_short_content_length():
    class ShortResponse:
        headers = {"Content-Type": "application/pdf", "Content-Length": "8"}

        def __init__(self):
            self.chunks = [b"%PDF", b"", b"ignored"]

        def read(self, _chunk_size):
            return self.chunks.pop(0)

    with pytest.raises(SourceRegistryReadTimeout) as exc:
        read_response_bytes_with_deadline(
            ShortResponse(),
            total_timeout_seconds=30.0,
            clock=lambda: 100.0,
            chunk_size=4,
        )

    message = str(exc.value)
    assert "ATTACHMENT_DOWNLOAD_TIMEOUT" in message
    assert "incomplete response 4/8 bytes" in message


def test_read_response_bytes_with_deadline_accepts_complete_content_length():
    class CompleteResponse:
        headers = {"Content-Type": "application/pdf", "Content-Length": "8"}

        def __init__(self):
            self.chunks = [b"%PDF", b"EOF!", b""]

        def read(self, _chunk_size):
            return self.chunks.pop(0)

    assert (
        read_response_bytes_with_deadline(
            CompleteResponse(),
            total_timeout_seconds=30.0,
            clock=lambda: 100.0,
            chunk_size=4,
        )
        == b"%PDFEOF!"
    )


def test_source_registry_http_client_sets_attachment_read_timeout(monkeypatch):
    from app import source_adapter

    class SocketLike:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

    class ResponseWithSocket:
        headers = {"Content-Type": "application/octet-stream"}

        def __init__(self):
            self.fp = type("FP", (), {"raw": type("Raw", (), {"_sock": SocketLike()})()})()
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _chunk_size):
            self.reads += 1
            return b"bytes" if self.reads == 1 else b""

    response = ResponseWithSocket()

    def fake_urlopen(req, *, timeout, context=None):
        return response

    monkeypatch.setattr(source_adapter.request, "urlopen", fake_urlopen)
    client = SourceRegistryHttpClient(
        referer="https://example.com/list",
        read_timeout_seconds=7,
    )

    assert client.get_bytes("https://example.com/file.bin") == (b"bytes", "application/octet-stream")
    assert response.fp.raw._sock.timeouts == [7]


def test_source_registry_http_client_post_form_bytes_posts_form_and_returns_bytes(monkeypatch):
    from app import source_adapter

    calls = []

    def fake_urlopen(req, *, timeout, context=None):
        calls.append(
            {
                "url": req.full_url,
                "method": req.get_method(),
                "headers": dict(req.header_items()),
                "data": req.data,
                "timeout": timeout,
            }
        )
        return FakeResponse(b"xls-bytes", "application/vnd.ms-excel")

    monkeypatch.setattr(source_adapter.request, "urlopen", fake_urlopen)
    client = SourceRegistryHttpClient(
        referer="https://example.com/list",
        timeout=23,
        read_total_timeout_seconds=90,
    )

    assert client.post_form_bytes("https://example.com/download", {"id": "file-202606"}) == (
        b"xls-bytes",
        "application/vnd.ms-excel",
    )
    assert calls == [
        {
            "url": "https://example.com/download",
            "method": "POST",
            "headers": {
                "User-agent": "Mozilla/5.0 source-registry-crawler/1.0",
                "Referer": "https://example.com/list",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-requested-with": "XMLHttpRequest",
            },
            "data": b"id=file-202606",
            "timeout": 23,
        }
    ]


def test_source_registry_http_client_retries_incomplete_get_bytes(monkeypatch):
    from app import source_adapter

    class ChunkedResponse:
        headers = {"Content-Type": "application/pdf", "Content-Length": "8"}

        def __init__(self, chunks):
            self.chunks = list(chunks)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _chunk_size):
            return self.chunks.pop(0)

    responses = [
        ChunkedResponse([b"%PDF", b""]),
        ChunkedResponse([b"%PDF", b"EOF!", b""]),
    ]
    calls = []

    def fake_urlopen(req, *, timeout, context=None):
        calls.append(req.full_url)
        return responses.pop(0)

    monkeypatch.setattr(source_adapter.request, "urlopen", fake_urlopen)
    client = SourceRegistryHttpClient(
        referer="https://example.com/list",
        read_retry_attempts=1,
        read_retry_backoff_seconds=0,
    )

    assert client.get_bytes("https://example.com/file.pdf") == (b"%PDFEOF!", "application/pdf")
    assert calls == ["https://example.com/file.pdf", "https://example.com/file.pdf"]


@dataclass(frozen=True)
class Summary:
    source_item_key: str
    title: str
    publish_date: str | None


def test_run_incremental_channel_crawl_stops_on_existing_and_reports_cursor():
    ingested = []
    discovered_pages = []
    channels = [
        {"channel_id": "existing", "notice_type_raw": "招标公告", "notice_family": "tender", "displaytypeval": "1"},
        {"channel_id": "new", "notice_type_raw": "流标或终止公告", "notice_family": "tombstone", "displaytypeval": "14"},
    ]

    def discover_page(channel, page, page_size):
        discovered_pages.append((channel["channel_id"], page, page_size))
        if channel["channel_id"] == "existing":
            return [Summary("OLD001", "已采公告", "2026-06-18")]
        return [Summary("NEW001", "新公告", "2026-06-20")]

    def business_key_for(summary):
        return f"bk:{summary.source_item_key}"

    report = run_incremental_channel_crawl(
        source_id="source-1",
        channels=channels,
        discover_page=discover_page,
        business_key_for=business_key_for,
        archive_exists=lambda key: key == "bk:OLD001",
        ingest=lambda summary: ingested.append(summary.source_item_key),
        max_pages=2,
        page_size=5,
        max_items_per_channel=1,
        as_of_date="2026-06-21",
    )

    assert discovered_pages == [("existing", 1, 5), ("new", 1, 5)]
    assert ingested == ["NEW001"]
    assert report["source_id"] == "source-1"
    assert report["ingested_count"] == 1
    assert report["skipped_existing_count"] == 1
    assert report["channels"][0]["cursor_source_item_key"] == "OLD001"
    assert report["channels"][0]["cursor_publish_date"] == "2026-06-18"
    assert report["channels"][0]["crawl_lag_days"] == 3
    assert report["channels"][0]["stopped_on_existing"] is True
    assert report["channels"][1]["notice_family"] == "tombstone"
    assert report["channels"][1]["crawl_lag_days"] == 1


def test_history_channel_crawl_skips_known_items_without_stopping_the_date_window():
    ingested = []

    report = run_incremental_channel_crawl(
        source_id="source-1",
        channels=[{"channel_id": "tender_notice", "notice_type_raw": "招标公告", "notice_family": "tender"}],
        discover_page=lambda _channel, _page, _page_size: [
            Summary("KNOWN001", "已入湖公告", "2026-07-15"),
            Summary("OLDER001", "窗口内较早公告", "2026-07-12"),
        ],
        business_key_for=lambda summary: f"bk:{summary.source_item_key}",
        archive_exists=lambda key: key == "bk:KNOWN001",
        ingest=lambda summary: ingested.append(summary.source_item_key),
        max_pages=1,
        page_size=50,
        stop_on_existing=False,
    )

    assert ingested == ["OLDER001"]
    assert report["ingested_count"] == 1
    assert report["skipped_existing_count"] == 1
    assert report["channels"][0]["stopped_on_existing"] is False


def test_count_opaque_attachments_and_lag_are_domain_neutral():
    @dataclass(frozen=True)
    class Attachment:
        file_name: str

    attachments = [Attachment("招标文件.CDZ"), Attachment("图纸.zip"), Attachment("公告.pdf"), Attachment("no-ext")]

    assert count_opaque_attachments(attachments, {".cdz", ".zip"}) == 2
    assert crawl_lag_days("2026-06-19", as_of_date="2026-06-21") == 2
    assert crawl_lag_days(None, as_of_date="2026-06-21") is None
