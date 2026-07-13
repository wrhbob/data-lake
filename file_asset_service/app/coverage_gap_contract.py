"""Coverage-gap contract (contracts-v0.1.0-rc3).

This module is the **code carrier of the rc3 coverage-gap contract**. It defines the
final enums and the single classification rule that three consumers must agree on:

  - the matrix UI three-state (覆盖 / 待核 / 缺失)
  - the ``v_coverage_gap`` Postgres view (the SQL source of truth for contract consumers)
  - this module's :func:`classify_gap` (used by the matrix API on SQLite/tests, and as
    the documented spec the view's CASE expressions must mirror)

Human-readable contract text: ``docs/coverage_gap_contract_rc3.md``.

Cell identity
-------------
A coverage cell is the triple ``(coverage_region_code, period, domain_type)`` where
``period`` is a normalised ``YYYY-MM`` month and ``domain_type`` is ``cost_info``.

The two classification axes
---------------------------
``gap_type`` is the coverage *state* and mirrors the matrix's historical three-state
(``covered`` / ``pending_verify`` / ``missing``). ``gap_reason`` is the actionability
sub-classification and is ``None`` only for ``covered`` cells.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- gap_type: coverage state (aligns with matrix UI 三态) ---------------------

GAP_TYPE_COVERED = "covered"
GAP_TYPE_PENDING_VERIFY = "pending_verify"
GAP_TYPE_MISSING = "missing"
GAP_TYPES = (GAP_TYPE_COVERED, GAP_TYPE_PENDING_VERIFY, GAP_TYPE_MISSING)

GAP_TYPE_LABELS = {
    GAP_TYPE_COVERED: "覆盖",
    GAP_TYPE_PENDING_VERIFY: "待核",
    GAP_TYPE_MISSING: "缺失",
}

# --- gap_reason: actionability (None for covered) -----------------------------

GAP_REASON_NO_SOURCE = "no_source"
GAP_REASON_NOT_PUBLISHED = "not_published"
GAP_REASON_NOT_ATTEMPTED = "not_attempted"
GAP_REASON_FAILED = "failed"
GAP_REASONS = (GAP_REASON_NO_SOURCE, GAP_REASON_NOT_PUBLISHED, GAP_REASON_NOT_ATTEMPTED, GAP_REASON_FAILED)

#: reasons for which an operator may initiate a backfill from the matrix.
ACTIONABLE_REASONS = frozenset({GAP_REASON_NOT_ATTEMPTED, GAP_REASON_FAILED})

GAP_REASON_LABELS = {
    GAP_REASON_NO_SOURCE: "无源",
    GAP_REASON_NOT_PUBLISHED: "未发布",
    GAP_REASON_NOT_ATTEMPTED: "待采集",
    GAP_REASON_FAILED: "失败",
}

# --- failed_stage: where a failed collection broke (rc3 backlogged field) ------
#
# Derived from the worker's task ``error_code`` (see app.cost_info_worker._classify_error).
# Populated only when ``gap_reason == failed``.

FAILED_STAGE_DOWNLOAD_TIMEOUT = "download_timeout"
FAILED_STAGE_HOST_UNREACHABLE = "host_unreachable"
FAILED_STAGE_PARSE_ERROR = "parse_error"
FAILED_STAGE_CRAWL_FAILED = "crawl_failed"
FAILED_STAGE_MAX_ATTEMPTS_EXCEEDED = "max_attempts_exceeded"
FAILED_STAGES = (
    FAILED_STAGE_DOWNLOAD_TIMEOUT,
    FAILED_STAGE_HOST_UNREACHABLE,
    FAILED_STAGE_PARSE_ERROR,
    FAILED_STAGE_CRAWL_FAILED,
    FAILED_STAGE_MAX_ATTEMPTS_EXCEEDED,
)

#: Map worker task ``error_code`` -> contract ``failed_stage``. The view's CASE and
#: :func:`failed_stage_for_error_code` must stay in lock-step.
ERROR_CODE_TO_FAILED_STAGE = {
    "DOWNLOAD_TIMEOUT": FAILED_STAGE_DOWNLOAD_TIMEOUT,
    "HOST_UNREACHABLE": FAILED_STAGE_HOST_UNREACHABLE,
    "PARSE_ERROR": FAILED_STAGE_PARSE_ERROR,
    "COST_INFO_CRAWL_TASK_FAILED": FAILED_STAGE_CRAWL_FAILED,
    "MAX_ATTEMPTS_EXCEEDED": FAILED_STAGE_MAX_ATTEMPTS_EXCEEDED,
}

FAILED_STAGE_LABELS = {
    FAILED_STAGE_DOWNLOAD_TIMEOUT: "下载超时",
    FAILED_STAGE_HOST_UNREACHABLE: "主机不可达",
    FAILED_STAGE_PARSE_ERROR: "解析失败",
    FAILED_STAGE_CRAWL_FAILED: "采集失败",
    FAILED_STAGE_MAX_ATTEMPTS_EXCEEDED: "重试耗尽",
}

# --- cell_status: collection lifecycle (status-writeback, feature 2) -----------
#
# Derived from task/lineage/archive — never stored in a new table (red line).

CELL_STATUS_IN_LAKE = "in_lake"
CELL_STATUS_QUEUED = "queued"
CELL_STATUS_CRAWLING = "crawling"
CELL_STATUS_FAILED = "failed"
CELL_STATUS_MISSING = "missing"

CELL_STATUS_LABELS = {
    CELL_STATUS_IN_LAKE: "已入湖",
    CELL_STATUS_QUEUED: "排队中",
    CELL_STATUS_CRAWLING: "爬取中",
    CELL_STATUS_FAILED: "失败",
    CELL_STATUS_MISSING: "缺失",
}


def failed_stage_for_error_code(error_code: str | None) -> str | None:
    """Map a worker task ``error_code`` to the contract ``failed_stage``."""
    if not error_code:
        return None
    return ERROR_CODE_TO_FAILED_STAGE.get(error_code)


@dataclass(frozen=True)
class CellFacts:
    """Inputs required to classify one cell. All signals come from existing tables."""

    has_coverage: bool
    #: ``True`` when the region's source completeness is ``source_blocked``.
    blocked: bool
    #: ``True`` when the region has at least one enabled (active, non-blocked) source.
    has_active_source: bool
    #: ``True`` when the period is after the latest covered period (publication pending).
    pending_publication: bool
    #: ``True`` when the period is before the earliest covered period (backfill pending).
    pending_backfill: bool
    #: Latest collection-task status for this cell, one of pending/running/failed/done/None.
    latest_task_status: str | None = None
    latest_task_error_code: str | None = None


@dataclass(frozen=True)
class CellClassification:
    gap_type: str
    gap_reason: str | None
    failed_stage: str | None
    cell_status: str
    actionable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "gap_type": self.gap_type,
            "gap_reason": self.gap_reason,
            "failed_stage": self.failed_stage,
            "cell_status": self.cell_status,
            "actionable": self.actionable,
        }


def _gap_type(has_coverage: bool, blocked: bool, pending: bool) -> str:
    if has_coverage:
        return GAP_TYPE_COVERED
    if blocked or pending:
        return GAP_TYPE_PENDING_VERIFY
    return GAP_TYPE_MISSING


def _cell_status(has_coverage: bool, latest_task_status: str | None) -> str:
    if has_coverage:
        return CELL_STATUS_IN_LAKE
    if latest_task_status == "pending":
        return CELL_STATUS_QUEUED
    if latest_task_status == "running":
        return CELL_STATUS_CRAWLING
    if latest_task_status == "failed":
        return CELL_STATUS_FAILED
    return CELL_STATUS_MISSING


def classify_gap(facts: CellFacts) -> CellClassification:
    """Classify a cell. The single rule source for the matrix API and the SQL view.

    ``gap_type`` mirrors the matrix's historical three-state. ``gap_reason`` precedence
    (only evaluated when not covered):

      1. ``no_source``      — region has no usable source (blocked, or none registered).
                              Not actionable.
      2. ``not_published``  — future period, not yet due. Not actionable.
      3. ``failed``         — latest task exhausted retries. Actionable (retry).
      4. ``not_attempted``  — fresh gap or historical backfill gap. Actionable.
    """
    pending = facts.pending_publication or facts.pending_backfill
    gap_type = _gap_type(facts.has_coverage, facts.blocked, pending)
    cell_status = _cell_status(facts.has_coverage, facts.latest_task_status)

    if facts.has_coverage:
        return CellClassification(gap_type, None, None, cell_status, actionable=False)

    if facts.blocked or not facts.has_active_source:
        return CellClassification(gap_type, GAP_REASON_NO_SOURCE, None, cell_status, actionable=False)

    if facts.pending_publication:
        return CellClassification(gap_type, GAP_REASON_NOT_PUBLISHED, None, cell_status, actionable=False)

    if facts.latest_task_status == "failed":
        return CellClassification(
            gap_type,
            GAP_REASON_FAILED,
            failed_stage_for_error_code(facts.latest_task_error_code),
            cell_status,
            actionable=True,
        )

    return CellClassification(gap_type, GAP_REASON_NOT_ATTEMPTED, None, cell_status, actionable=True)
