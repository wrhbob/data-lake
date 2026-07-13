from app.adapters.base_cost_info_adapter import AdapterResult, DiscoveredIssue


def test_discovered_issue_keeps_attachment_urls_independent():
    first = DiscoveredIssue(
        source_item_key="2026-05",
        title="德阳市2026年5月工程造价信息",
        publish_date="2026-05-28",
        period_raw="2026年5月",
        detail_url="https://example.gov.cn/detail/202605",
    )
    second = DiscoveredIssue(
        source_item_key="2026-06",
        title="德阳市2026年6月工程造价信息",
        publish_date="2026-06-28",
        period_raw="2026年6月",
        detail_url="https://example.gov.cn/detail/202606",
    )

    first.attachment_urls.append("https://example.gov.cn/files/202605.pdf")

    assert first.attachment_urls == ["https://example.gov.cn/files/202605.pdf"]
    assert second.attachment_urls == []


def test_adapter_result_defaults_are_layer0_safe():
    result = AdapterResult()

    assert result.discovered_count == 0
    assert result.ingested_count == 0
    assert result.duplicate_count == 0
    assert result.failed_count == 0
    assert result.skipped_count == 0
    assert result.early_stopped_on_duplicate is False
    assert result.error_code is None
    assert result.error_context is None
    assert result.failed_items == []
