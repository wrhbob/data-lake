from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class DiscoveredIssue:
    """One cost_info publication discovered from a list/detail page."""

    source_item_key: str
    title: str
    publish_date: str | None
    period_raw: str | None
    detail_url: str
    attachment_urls: list[str] = field(default_factory=list)


@dataclass
class AdapterResult:
    discovered_count: int = 0
    ingested_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    cursor_source_item_key: str | None = None
    cursor_publish_date: str | None = None
    early_stopped_on_duplicate: bool = False
    error_code: str | None = None
    error_context: dict | None = None
    failed_items: list[dict] = field(default_factory=list)


class BaseCostInfoAdapter(Protocol):
    adapter_kind: str

    def discover(self, source, task, client) -> list[DiscoveredIssue]:
        """Return issues to ingest without parsing attachment contents."""
        ...

    def ingest(self, source, task, issue: DiscoveredIssue, storage):
        """Download and archive one issue's attachments as Layer 0 assets."""
        ...
