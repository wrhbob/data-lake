from __future__ import annotations

from app.adapters.base_cost_info_adapter import DiscoveredIssue


class MockCostInfoAdapter:
    adapter_kind = "mock"

    def __init__(self, issues: list[DiscoveredIssue] | None = None) -> None:
        self._issues = issues or [
            DiscoveredIssue(
                source_item_key=f"mock_issue_{index}",
                title=f"Mock Issue {index}",
                publish_date="2026-06-01",
                period_raw="2026年6月",
                detail_url=f"https://mock.gov.cn/detail/{index}",
                attachment_urls=[f"https://mock.gov.cn/file/{index}.pdf"],
            )
            for index in range(3)
        ]

    def discover(self, source, task, client) -> list[DiscoveredIssue]:
        return self._issues

    def ingest(self, source, task, issue: DiscoveredIssue, storage) -> None:
        return None
