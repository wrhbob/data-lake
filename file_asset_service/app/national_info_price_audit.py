from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.national_cost_info_regions import NationalRegion


SOURCE_STATUSES = {
    "auto_crawl_ready",
    "coverage_declaration",
    "source_blocked",
    "pending_verify",
    "missing_official_source",
}
REVIEW_STATUSES = {"draft", "reviewed", "accepted"}


@dataclass(frozen=True)
class SourceAuditValidationResult:
    target_count: int
    audited_target_count: int
    missing_target_region_codes: list[str]
    invalid_rows: list[dict]

    @property
    def is_complete(self) -> bool:
        return not self.missing_target_region_codes and not self.invalid_rows


def clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def load_source_audit_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = []
        for index, row in enumerate(csv.DictReader(handle), start=2):
            normalized = {key: (value or "").strip() for key, value in row.items()}
            normalized["_line_number"] = str(index)
            rows.append(normalized)
        return rows


def _errors_for_row(row: dict[str, str], target_region_codes: set[str]) -> list[str]:
    errors: list[str] = []
    status = clean(row.get("source_status"))
    review_status = clean(row.get("review_status")) or "draft"

    if status not in SOURCE_STATUSES:
        errors.append("INVALID_SOURCE_STATUS")
    if review_status not in REVIEW_STATUSES:
        errors.append("INVALID_REVIEW_STATUS")
    if clean(row.get("target_region_code")) not in target_region_codes:
        errors.append("UNKNOWN_TARGET_REGION_CODE")

    if status == "auto_crawl_ready":
        if not clean(row.get("site_id")):
            errors.append("AUTO_CRAWL_READY_SITE_ID_REQUIRED")
        if not clean(row.get("entry_url")):
            errors.append("AUTO_CRAWL_READY_ENTRY_URL_REQUIRED")
        if not (clean(row.get("adapter_kind")) or clean(row.get("crawl_pattern"))):
            errors.append("AUTO_CRAWL_READY_ADAPTER_OR_PATTERN_REQUIRED")

    if status == "source_blocked":
        if not (clean(row.get("blocked_reason")) or clean(row.get("audit_note"))):
            errors.append("SOURCE_BLOCKED_REASON_REQUIRED")
        if clean(row.get("manual_path")) != "017/manual_upload":
            errors.append("SOURCE_BLOCKED_MANUAL_PATH_REQUIRED")

    if status == "coverage_declaration":
        if not (clean(row.get("entry_url")) or clean(row.get("evidence_url"))):
            errors.append("COVERAGE_DECLARATION_URL_REQUIRED")

    if status == "missing_official_source" and not clean(row.get("audit_note")):
        errors.append("MISSING_OFFICIAL_SOURCE_AUDIT_NOTE_REQUIRED")

    return errors


def validate_source_audit(
    rows: list[dict[str, str]],
    regions: list[NationalRegion],
) -> SourceAuditValidationResult:
    required_regions = [region for region in regions if region.is_required]
    target_region_codes = {region.target_region_code for region in required_regions}
    audited_codes = {
        str(row.get("target_region_code"))
        for row in rows
        if clean(row.get("review_status")) == "accepted"
    }
    invalid_rows = []

    for row in rows:
        errors = _errors_for_row(row, target_region_codes)
        if errors:
            invalid_rows.append(
                {
                    "line_number": int(row.get("_line_number") or 0),
                    "target_region_code": clean(row.get("target_region_code")),
                    "errors": errors,
                }
            )

    return SourceAuditValidationResult(
        target_count=len(required_regions),
        audited_target_count=len(audited_codes & target_region_codes),
        missing_target_region_codes=sorted(target_region_codes - audited_codes),
        invalid_rows=invalid_rows,
    )


def build_audit_summary(rows: list[dict[str, str]], regions: list[NationalRegion]) -> dict:
    validation = validate_source_audit(rows, regions)
    by_status = Counter(clean(row.get("source_status")) for row in rows if clean(row.get("source_status")))
    by_review = Counter(clean(row.get("review_status")) or "draft" for row in rows)
    return {
        "target_count": validation.target_count,
        "audited_target_count": validation.audited_target_count,
        "missing_target_count": len(validation.missing_target_region_codes),
        "invalid_row_count": len(validation.invalid_rows),
        "is_complete": validation.is_complete,
        "by_source_status": dict(sorted(by_status.items())),
        "by_review_status": dict(sorted(by_review.items())),
    }


def build_crawl_queue_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    queue = []
    for row in rows:
        if clean(row.get("source_status")) != "auto_crawl_ready":
            continue
        if clean(row.get("review_status")) != "accepted":
            continue
        queue.append(
            {
                "province_code": clean(row.get("province_code")) or "",
                "province_name": clean(row.get("province_name")) or "",
                "target_region_code": clean(row.get("target_region_code")) or "",
                "target_region_name": clean(row.get("target_region_name")) or "",
                "site_id": clean(row.get("site_id")) or "",
                "source_name": clean(row.get("source_name")) or "",
                "adapter_kind": clean(row.get("adapter_kind")) or "",
                "crawl_pattern": clean(row.get("crawl_pattern")) or "",
                "entry_url": clean(row.get("entry_url")) or "",
                "review_status": clean(row.get("review_status")) or "",
            }
        )
    return sorted(queue, key=lambda item: (item["province_code"], item["target_region_code"], item["site_id"]))


def build_blocked_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    blocked = []
    for row in rows:
        if clean(row.get("source_status")) != "source_blocked":
            continue
        blocked.append(
            {
                "province_code": clean(row.get("province_code")) or "",
                "province_name": clean(row.get("province_name")) or "",
                "target_region_code": clean(row.get("target_region_code")) or "",
                "target_region_name": clean(row.get("target_region_name")) or "",
                "site_id": clean(row.get("site_id")) or "",
                "source_name": clean(row.get("source_name")) or "",
                "entry_url": clean(row.get("entry_url")) or "",
                "blocked_reason": clean(row.get("blocked_reason")) or clean(row.get("audit_note")) or "",
                "manual_path": clean(row.get("manual_path")) or "",
            }
        )
    return sorted(blocked, key=lambda item: (item["province_code"], item["target_region_code"], item["site_id"]))


def build_pattern_summary_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    counter: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if clean(row.get("source_status")) != "auto_crawl_ready":
            continue
        key = (clean(row.get("adapter_kind")) or "", clean(row.get("crawl_pattern")) or "")
        counter[key] += 1
    return [
        {"adapter_kind": adapter_kind, "crawl_pattern": crawl_pattern, "source_count": count}
        for (adapter_kind, crawl_pattern), count in sorted(counter.items())
    ]
