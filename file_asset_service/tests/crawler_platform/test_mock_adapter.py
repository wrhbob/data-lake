import pytest

from app.adapters import get_adapter
from app.adapters.base_cost_info_adapter import DiscoveredIssue
from app.adapters.mock_adapter import MockCostInfoAdapter


def test_mock_adapter_returns_three_default_issues():
    adapter = MockCostInfoAdapter()

    issues = adapter.discover(source=None, task=None, client=None)

    assert adapter.adapter_kind == "mock"
    assert [issue.source_item_key for issue in issues] == [
        "mock_issue_0",
        "mock_issue_1",
        "mock_issue_2",
    ]
    assert all(issue.attachment_urls for issue in issues)


def test_mock_adapter_accepts_injected_issues():
    expected = [
        DiscoveredIssue(
            source_item_key="custom_issue",
            title="自定义测试信息价",
            publish_date="2026-06-01",
            period_raw="2026年6月",
            detail_url="https://mock.gov.cn/detail/custom",
        )
    ]

    adapter = MockCostInfoAdapter(expected)

    assert adapter.discover(source=None, task=None, client=None) == expected


def test_adapter_dispatch_returns_mock_instance():
    adapter = get_adapter("mock")

    assert isinstance(adapter, MockCostInfoAdapter)


def test_adapter_dispatch_rejects_unknown_kind():
    with pytest.raises(ValueError, match="Unknown adapter_kind='missing'"):
        get_adapter("missing")
