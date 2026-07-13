from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.national_cost_info_regions import load_national_regions


DOMAIN_DIRS = {
    "cost_info": "信息价",
    "trading": "招投标",
    "quota": "清单定额",
    "policy_regulation": "政策法规",
    "standard_atlas": "图集规范",
}

DIRECT_FILE_ROLES = {
    "main_document",
    "priced_source",
    "tender_doc",
    "web_snapshot",
    "policy_document",
    "standard_document",
    "atlas_document",
    "quota_db",
}

UNSAFE_PATH_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")


class FileMirrorRootUnconfigured(RuntimeError):
    pass


def get_file_mirror() -> "FileMirror":
    return FileMirror.from_settings()


def sanitize_path_component(value: object, *, max_length: int = 180) -> str:
    text = str(value or "").strip()
    text = UNSAFE_PATH_CHARS.sub("_", text)
    text = text.replace("..", "_")
    text = re.sub(r"_+", "_", text)
    text = text.strip(" .")
    if not text:
        return "_"
    return text[:max_length]


def build_archive_mirror_relative_path(archive: Any, mounted: Any, asset: Any) -> str:
    domain = sanitize_path_component(DOMAIN_DIRS.get(str(getattr(archive, "domain_type", "") or ""), getattr(archive, "domain_type", "") or "档案"))
    province, city = _archive_region_names(archive)
    year = _archive_year(archive)
    file_name = sanitize_path_component(getattr(mounted, "display_name", None) or getattr(asset, "file_name", None) or getattr(asset, "file_id", "原件"))
    role = str(getattr(mounted, "file_role", "") or "attachment")
    parts = [
        domain,
        sanitize_path_component(province),
        sanitize_path_component(city),
        sanitize_path_component(year),
    ]
    if role not in DIRECT_FILE_ROLES:
        parts.append(sanitize_path_component(getattr(archive, "title", None) or getattr(asset, "file_id", "档案")))
        file_name = f"{sanitize_path_component(role, max_length=48)}-{file_name}"
    parts.append(file_name)
    return "/".join(parts)


@dataclass(frozen=True)
class FileMirror:
    root: Path | None = None

    @classmethod
    def from_settings(cls) -> "FileMirror":
        root = get_settings().nas_mirror_root
        return cls(root=Path(root).expanduser() if root else None)

    @property
    def configured(self) -> bool:
        return self.root is not None

    def status_for(self, archive: Any, mounted: Any, asset: Any | None) -> dict[str, object]:
        if asset is None:
            return {
                "mirror_status": "missing_asset",
                "mirror_relative_path": None,
                "mirror_path": None,
                "mirror_size": None,
                "mirror_checked_at": _now_iso(),
            }
        relative_path = build_archive_mirror_relative_path(archive, mounted, asset)
        if self.root is None:
            return {
                "mirror_status": "unconfigured",
                "mirror_relative_path": relative_path,
                "mirror_path": None,
                "mirror_size": None,
                "mirror_checked_at": _now_iso(),
            }
        target = self.root / Path(relative_path)
        try:
            stat = target.stat()
        except FileNotFoundError:
            return {
                "mirror_status": "missing",
                "mirror_relative_path": relative_path,
                "mirror_path": str(target),
                "mirror_size": None,
                "mirror_checked_at": _now_iso(),
            }
        except OSError as exc:
            return {
                "mirror_status": "error",
                "mirror_relative_path": relative_path,
                "mirror_path": str(target),
                "mirror_size": None,
                "mirror_error": str(exc),
                "mirror_checked_at": _now_iso(),
            }
        expected_size = int(getattr(asset, "file_size", 0) or 0)
        status = "mirrored" if stat.st_size == expected_size else "stale"
        return {
            "mirror_status": status,
            "mirror_relative_path": relative_path,
            "mirror_path": str(target),
            "mirror_size": stat.st_size,
            "mirror_checked_at": _now_iso(),
        }

    def export_file(self, archive: Any, mounted: Any, asset: Any, content: bytes) -> dict[str, object]:
        if self.root is None:
            raise FileMirrorRootUnconfigured("FILE_MIRROR_ROOT_UNCONFIGURED")
        relative_path = build_archive_mirror_relative_path(archive, mounted, asset)
        target = self.root / Path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        try:
            tmp_path.write_bytes(content)
            try:
                os.replace(tmp_path, target)
            except FileExistsError:
                target.unlink()
                os.replace(tmp_path, target)
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
        return self.status_for(archive, mounted, asset)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata_value(metadata: dict[str, Any], key: str) -> object | None:
    value = metadata.get(key)
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _archive_metadata(archive: Any) -> dict[str, Any]:
    metadata = getattr(archive, "metadata_payload", None)
    return metadata if isinstance(metadata, dict) else {}


def _archive_region_names(archive: Any) -> tuple[str, str]:
    metadata = _archive_metadata(archive)
    province = _metadata_value(metadata, "province_raw")
    city = _metadata_value(metadata, "city_raw")
    if province and city:
        return str(province), str(city)
    region_code = str(getattr(archive, "region_code", "") or "")
    lookup = _region_lookup()
    mapped = lookup.get(region_code) or lookup.get(f"{region_code[:4]}00") or lookup.get(f"{region_code[:2]}0000")
    if mapped:
        return str(province or mapped[0]), str(city or mapped[1])
    fallback = region_code or "未标地区"
    return str(province or fallback), str(city or fallback)


def _archive_year(archive: Any) -> str:
    metadata = _archive_metadata(archive)
    for key in ("period", "period_start", "period_raw", "publish_date_raw"):
        value = _metadata_value(metadata, key)
        if match := YEAR_RE.search(str(value or "")):
            return match.group(1)
    publish_date = getattr(archive, "publish_date", None)
    if isinstance(publish_date, date):
        return str(publish_date.year)
    if match := YEAR_RE.search(str(publish_date or "")):
        return match.group(1)
    return "未标年份"


@lru_cache(maxsize=1)
def _region_lookup() -> dict[str, tuple[str, str]]:
    path = Path(__file__).resolve().parent.parent / "data" / "national_cost_info_regions.csv"
    if not path.exists():
        return {}
    lookup: dict[str, tuple[str, str]] = {}
    for row in load_national_regions(path):
        lookup[row.target_region_code] = (row.province_name, row.target_region_name)
        lookup[row.province_code] = (row.province_name, row.province_name)
    return lookup
