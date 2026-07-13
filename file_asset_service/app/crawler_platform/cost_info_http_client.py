from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.crawler_platform.host_rate_limiter import HostLimiter, get_host_limiter
from app.source_adapter import SourceRegistryHttpClient


@dataclass
class CostInfoHttpClient:
    inner: SourceRegistryHttpClient
    limiter: HostLimiter
    host: str
    min_delay_seconds: float = 5.0
    jitter_seconds: float = 2.0

    def _wait(self) -> None:
        self.limiter.wait(self.host, self.min_delay_seconds, self.jitter_seconds)

    def get_text(self, url: str) -> str:
        self._wait()
        return self.inner.get_text(url)

    def post_form(self, url: str, data: dict[str, object]) -> str:
        self._wait()
        return self.inner.post_form(url, data)

    def post_form_json(self, url: str, data: dict[str, object]) -> dict:
        self._wait()
        return self.inner.post_form_json(url, data)

    def post_form_bytes(self, url: str, data: dict[str, object]) -> tuple[bytes, str | None]:
        self._wait()
        return self.inner.post_form_bytes(url, data)

    def post_json(self, url: str, payload: dict[str, object]) -> dict:
        self._wait()
        return self.inner.post_json(url, payload)

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        self._wait()
        return self.inner.get_bytes(url)


def source_entry_url(source) -> str:
    config = source.config or {}
    stable = config.get("stable") or {}
    return stable.get("entry_url") or source.url or source.base_url


def make_http_client(source) -> CostInfoHttpClient:
    config = source.config or {}
    ops = config.get("ops") if isinstance(config.get("ops"), dict) else {}
    http_options = ops.get("http") if isinstance(ops.get("http"), dict) else {}
    policy = source.schedule_policy or {}
    rate = policy.get("rate_limit") or {}
    entry_url = source_entry_url(source)
    if not entry_url:
        raise ValueError(f"SOURCE_ENTRY_URL_REQUIRED: {source.source_id}")
    host = rate.get("host") or urlparse(entry_url).netloc

    inner_kwargs = {
        "referer": entry_url,
        "timeout": int(rate.get("request_timeout_seconds", 60)),
        "cookie": rate.get("cookie"),
        "tls_ciphers": rate.get("tls_ciphers"),
        "tls_minimum_version": rate.get("tls_minimum_version"),
        "min_interval_seconds": 0,
        "jitter_seconds": 0,
        "read_timeout_seconds": rate.get("per_read_timeout_seconds"),
        "read_total_timeout_seconds": rate.get("attachment_total_timeout_seconds"),
        "read_chunk_size": int(rate.get("read_chunk_size", 1024 * 1024)),
    }
    if http_options.get("user_agent"):
        inner_kwargs["user_agent"] = str(http_options["user_agent"])
    inner = SourceRegistryHttpClient(**inner_kwargs)

    return CostInfoHttpClient(
        inner=inner,
        limiter=get_host_limiter(),
        host=host,
        min_delay_seconds=float(rate.get("min_delay_seconds", policy.get("min_delay_seconds", 5.0))),
        jitter_seconds=float(rate.get("jitter_seconds", policy.get("jitter_seconds", 2.0))),
    )
