"""状态机测试."""

import pytest

from quota_lake.lake.state_machine import (
    BookStatus,
    TRANSITIONS,
    transition,
    allowed_transitions,
)


class TestTransitions:
    """合法转移测试."""

    @pytest.mark.parametrize("current,target", [
        ("draft", "ingested"),
        ("ingested", "parsing"),
        ("parsing", "parsed"),
        ("parsed", "qa"),
        ("qa", "qa_passed"),
        ("qa", "qa_failed"),
        ("qa_passed", "review"),
        ("qa_passed", "usable"),
        ("qa_passed", "archived"),
        ("review", "qa"),
        ("review", "usable"),
        ("review", "parsed"),
        ("usable", "archived"),
        # P0 修补: usable → qa 回归路径
        ("usable", "qa"),
    ])
    def test_valid_transition(self, current, target):
        result = transition(current, target)
        assert result.ok, result.message

    @pytest.mark.parametrize("current,target", [
        ("draft", "qa"),          # 跳过太多步骤
        ("ingested", "usable"),   # 必须经过解析
        ("parsed", "usable"),     # 必须经过 QA
        ("qa", "draft"),          # 不能回退到草稿
        ("archived", "ingested"), # 归档后不能回退
    ])
    def test_invalid_transition(self, current, target):
        result = transition(current, target)
        assert not result.ok, f"应该拒绝 {current} → {target}"

    def test_unknown_status(self):
        result = transition("nonexistent", "draft")
        assert not result.ok
        assert "未知状态" in result.message

    def test_unknown_target(self):
        result = transition("draft", "nonexistent")
        assert not result.ok
        assert "未知目标状态" in result.message


class TestUsableRegression:
    """P0 修补: usable→qa_review 回归路径 (勘误/调价到达时)."""

    def test_usable_to_qa_allowed(self):
        result = transition("usable", "qa")
        assert result.ok, "勘误/调价到达时, usable 应可回归 QA"

    def test_usable_to_qa_in_transitions(self):
        allowed = allowed_transitions("usable")
        assert "qa" in allowed, "P0 修补: usable 状态应包含 qa 回归路径"


class TestAllowedTransitions:
    def test_draft_allowed(self):
        targets = allowed_transitions("draft")
        assert "ingested" in targets
        assert "error" in targets

    def test_error_allowed(self):
        targets = allowed_transitions("error")
        assert "draft" in targets
        assert "ingested" in targets

    def test_unknown_returns_empty(self):
        targets = allowed_transitions("bogus")
        assert targets == []


class TestTransitionsTableCompleteness:
    """转移表完整性: 每个状态至少有一个出口."""

    def test_every_status_has_exit(self):
        for status in BookStatus:
            targets = allowed_transitions(status.value)
            assert len(targets) > 0, f"状态 {status} 没有出口转移"
