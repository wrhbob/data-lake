from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


VALID_TARGET_LEVELS = {"province", "prefecture", "municipality"}


@dataclass(frozen=True)
class NationalRegion:
    admin_division_version: str
    province_code: str
    province_name: str
    target_region_code: str
    target_region_name: str
    target_level: str
    is_required: bool
    source_note: str | None = None

    @property
    def target_key(self) -> str:
        return f"{self.province_code}:{self.target_region_code}"


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _row_to_region(row: dict[str, str], *, line_number: int) -> NationalRegion:
    target_level = _clean(row.get("target_level"))
    if target_level not in VALID_TARGET_LEVELS:
        raise ValueError(f"INVALID_TARGET_LEVEL line={line_number}: {target_level!r}")

    required = {
        "admin_division_version": _clean(row.get("admin_division_version")),
        "province_code": _clean(row.get("province_code")),
        "province_name": _clean(row.get("province_name")),
        "target_region_code": _clean(row.get("target_region_code")),
        "target_region_name": _clean(row.get("target_region_name")),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"NATIONAL_REGION_REQUIRED_FIELD_MISSING line={line_number}: {','.join(missing)}")

    return NationalRegion(
        admin_division_version=str(required["admin_division_version"]),
        province_code=str(required["province_code"]),
        province_name=str(required["province_name"]),
        target_region_code=str(required["target_region_code"]),
        target_region_name=str(required["target_region_name"]),
        target_level=target_level,
        is_required=_parse_bool(row.get("is_required")),
        source_note=_clean(row.get("source_note")),
    )


def load_national_regions(path: str | Path) -> list[NationalRegion]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            _row_to_region(row, line_number=index + 2)
            for index, row in enumerate(csv.DictReader(handle))
        ]


def validate_region_seed(rows: list[NationalRegion]) -> None:
    required_targets = [row.target_region_code for row in rows if row.is_required]
    duplicates = sorted(code for code, count in Counter(required_targets).items() if count > 1)
    if duplicates:
        raise ValueError(f"DUPLICATE_TARGET_REGION_CODE: {','.join(duplicates)}")

    province_codes = {row.province_code for row in rows}
    if len(province_codes) > 31:
        raise ValueError(f"TOO_MANY_PROVINCE_CODES_FOR_MAINLAND_SCOPE: {len(province_codes)}")


def summarize_region_targets(rows: list[NationalRegion]) -> dict[str, dict]:
    validate_region_seed(rows)
    summary: dict[str, dict] = {}
    counters: dict[str, Counter] = defaultdict(Counter)

    for row in rows:
        if not row.is_required:
            continue
        counters[row.province_code][row.target_level] += 1
        summary.setdefault(
            row.province_code,
            {
                "province_name": row.province_name,
                "required_target_count": 0,
                "target_levels": {},
            },
        )
        summary[row.province_code]["required_target_count"] += 1

    for province_code, counter in counters.items():
        summary[province_code]["target_levels"] = dict(counter)
    return dict(sorted(summary.items()))
