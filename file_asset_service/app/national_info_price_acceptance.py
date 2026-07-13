from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.national_cost_info_regions import load_national_regions
from app.national_info_price_audit import (
    build_audit_summary,
    build_blocked_rows,
    build_crawl_queue_rows,
    build_pattern_summary_rows,
    load_source_audit_rows,
    validate_source_audit,
)


@dataclass(frozen=True)
class NationalPackageResult:
    output_dir: Path
    files: dict[str, Path]
    summary: dict[str, Any]


def _write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_national_info_price_package(
    *,
    regions_path: str | Path,
    audit_path: str | Path,
    output_dir: str | Path,
) -> NationalPackageResult:
    regions = load_national_regions(regions_path)
    rows = load_source_audit_rows(audit_path)
    validation = validate_source_audit(rows, regions)
    summary = build_audit_summary(rows, regions)
    summary["missing_target_region_codes"] = validation.missing_target_region_codes
    summary["invalid_rows"] = validation.invalid_rows

    target_dir = Path(output_dir)
    files = {
        "source_audit": target_dir / "national_source_audit.csv",
        "completeness": target_dir / "national_source_completeness.json",
        "crawl_queue": target_dir / "national_crawl_queue.csv",
        "blocked_sources": target_dir / "national_blocked_sources.csv",
        "pattern_summary": target_dir / "national_pattern_summary.csv",
    }

    audit_fieldnames = [key for key in rows[0].keys() if key != "_line_number"] if rows else []
    _write_csv(files["source_audit"], rows, audit_fieldnames)
    _write_json(files["completeness"], summary)
    _write_csv(
        files["crawl_queue"],
        build_crawl_queue_rows(rows),
        [
            "province_code",
            "province_name",
            "target_region_code",
            "target_region_name",
            "site_id",
            "source_name",
            "adapter_kind",
            "crawl_pattern",
            "entry_url",
            "review_status",
        ],
    )
    _write_csv(
        files["blocked_sources"],
        build_blocked_rows(rows),
        [
            "province_code",
            "province_name",
            "target_region_code",
            "target_region_name",
            "site_id",
            "source_name",
            "entry_url",
            "blocked_reason",
            "manual_path",
        ],
    )
    _write_csv(
        files["pattern_summary"],
        build_pattern_summary_rows(rows),
        ["adapter_kind", "crawl_pattern", "source_count"],
    )
    return NationalPackageResult(output_dir=target_dir, files=files, summary=summary)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export national cost_info source audit package.")
    parser.add_argument("--regions", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = export_national_info_price_package(
        regions_path=args.regions,
        audit_path=args.audit,
        output_dir=args.output_dir,
    )
    print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    return 0 if result.summary["is_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
