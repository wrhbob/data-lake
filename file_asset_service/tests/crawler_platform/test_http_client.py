from app.collection import create_data_source
from app.crawler_platform.cost_info_http_client import CostInfoHttpClient, make_http_client


class FakeInnerClient:
    def __init__(self):
        self.calls = []

    def get_text(self, url: str) -> str:
        self.calls.append(("get_text", url))
        return "页面"

    def post_form(self, url: str, data: dict[str, object]) -> str:
        self.calls.append(("post_form", url, data))
        return "表单"

    def post_form_json(self, url: str, data: dict[str, object]) -> dict:
        self.calls.append(("post_form_json", url, data))
        return {"ok": True}

    def post_form_bytes(self, url: str, data: dict[str, object]) -> tuple[bytes, str | None]:
        self.calls.append(("post_form_bytes", url, data))
        return b"xls", "application/vnd.ms-excel"

    def post_json(self, url: str, payload: dict[str, object]) -> dict:
        self.calls.append(("post_json", url, payload))
        return {"posted": True}

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        self.calls.append(("get_bytes", url))
        return b"pdf", "application/pdf"


class FakeLimiter:
    def __init__(self):
        self.calls = []

    def wait(self, host: str, min_delay_seconds: float, jitter_seconds: float = 0) -> None:
        self.calls.append((host, min_delay_seconds, jitter_seconds))


def test_http_client_calls_limiter_before_each_public_method():
    inner = FakeInnerClient()
    limiter = FakeLimiter()
    client = CostInfoHttpClient(
        inner=inner,
        limiter=limiter,
        host="zjj.deyang.gov.cn",
        min_delay_seconds=8,
        jitter_seconds=4,
    )

    assert client.get_text("https://zjj.deyang.gov.cn/a") == "页面"
    assert client.post_form("https://zjj.deyang.gov.cn/form", {"page": 1}) == "表单"
    assert client.post_form_json("https://zjj.deyang.gov.cn/form-json", {"page": 2}) == {"ok": True}
    assert client.post_form_bytes("https://zjj.deyang.gov.cn/download", {"id": "file-1"}) == (
        b"xls",
        "application/vnd.ms-excel",
    )
    assert client.post_json("https://zjj.deyang.gov.cn/api", {"year": "2026"}) == {"posted": True}
    assert client.get_bytes("https://zjj.deyang.gov.cn/file.pdf") == (b"pdf", "application/pdf")

    assert limiter.calls == [("zjj.deyang.gov.cn", 8, 4)] * 6
    assert [call[0] for call in inner.calls] == [
        "get_text",
        "post_form",
        "post_form_json",
        "post_form_bytes",
        "post_json",
        "get_bytes",
    ]


def test_make_http_client_uses_source_registry_http_client_actual_kwargs(db_session, monkeypatch):
    from app.crawler_platform import cost_info_http_client

    captured = {}

    class CapturingSourceRegistryHttpClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(cost_info_http_client, "SourceRegistryHttpClient", CapturingSourceRegistryHttpClient)
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="德阳市住房和城乡建设局",
        data_domain="cost_info",
        base_url="https://zjj.deyang.gov.cn/",
        url="https://zjj.deyang.gov.cn/",
        config={
            "stable": {
                "site_id": "cost_info.sc.deyang",
                "entry_url": "https://zjj.deyang.gov.cn/xxj/",
            },
            "ops": {
                "http": {
                    "user_agent": "Mozilla/5.0 test browser",
                }
            },
        },
        schedule_policy={
            "rate_limit": {
                "host": "zjj.deyang.gov.cn",
                "request_timeout_seconds": 45,
                "per_read_timeout_seconds": 9,
                "attachment_total_timeout_seconds": 180,
                "read_chunk_size": 2048,
                "min_delay_seconds": 7,
                "jitter_seconds": 3,
            }
        },
    )

    client = make_http_client(source)

    assert isinstance(client, CostInfoHttpClient)
    assert client.host == "zjj.deyang.gov.cn"
    assert client.min_delay_seconds == 7
    assert client.jitter_seconds == 3
    assert captured == {
        "referer": "https://zjj.deyang.gov.cn/xxj/",
        "timeout": 45,
        "cookie": None,
        "tls_ciphers": None,
        "tls_minimum_version": None,
        "min_interval_seconds": 0,
        "jitter_seconds": 0,
        "user_agent": "Mozilla/5.0 test browser",
        "read_timeout_seconds": 9,
        "read_total_timeout_seconds": 180,
        "read_chunk_size": 2048,
    }


def test_make_http_client_falls_back_to_policy_delay_and_entry_host(db_session):
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="重庆市建设工程造价总站",
        data_domain="cost_info",
        base_url="https://zfcxjw.cq.gov.cn/",
        url="https://zfcxjw.cq.gov.cn/",
        config={
            "stable": {
                "site_id": "cost_info.cq.municipal",
                "entry_url": "https://zfcxjw.cq.gov.cn/xxgk/",
            }
        },
        schedule_policy={
            "min_delay_seconds": 11,
            "jitter_seconds": 5,
        },
    )

    client = make_http_client(source)

    assert client.host == "zfcxjw.cq.gov.cn"
    assert client.min_delay_seconds == 11
    assert client.jitter_seconds == 5
