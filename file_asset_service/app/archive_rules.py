from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.normalization import normalize_key_text, normalize_source_url
# v0.7 fix: file_extension 改成惰性 import, 打破循环依赖 (archive_rules -> assets -> models).
# 之前顶层 from app.assets import file_extension 会导致 models.py 想 import ARCHIVE_FILE_ROLES
# 时撞上循环链: models → archive_rules → assets → models. 现在只在 _infer_archive_file_role
# 函数体里 import, 打破循环.

ARCHIVE_STATUSES = {
    "discovered",
    "collecting",
    "collect_failed",
    "quarantined",
    "collected",
    "pending_tag",
    "archived",
    "ready_for_governance",
}

ARCHIVE_EVENT_TYPES = {
    "ARCHIVE_CREATED",
    "ARCHIVE_FILE_ATTACHED",
    "ARCHIVE_TAGGED",
    "ARCHIVE_ARCHIVED",
    "ARCHIVE_WITHDRAWN",
    "ARCHIVE_QUARANTINED",
    "ARCHIVE_READY_FOR_GOVERNANCE",
    "ADD_REPRESENTATION",
}

ARCHIVE_FILE_ROLES = {
    "main_document",
    "cover",
    "attachment",
    "priced_source",
    "qingdan_package",
    "drawing",
    "geological",
    "tender_doc",
    "web_snapshot",
    "zip_package",
    "preview",
    "quota_db",
    "bill_standard",
    "quota_supplement",
    "quota_interpretation",
    "atlas_document",
    "standard_document",
    "scan_image",
    "policy_document",
    "policy_attachment",
    "table_of_contents",
    "appendix",
    "release_announcement",
    "other",
    # v0.5 新增（INTEGRATION_PLAN §2.4 / DB_SCHEMA.md §3.10）：
    # quota_parser worker 阶段 A/B 产出的 md/html/xlsx 对应 file_role
    # 必须与 app.models.ArchiveFile CheckConstraint + DB schema 同步
    "parse_markdown",
    "parse_html",
    "parse_candidate_xlsx",
    "parse_final_xlsx",
}

PRICED_SOURCE_EXTENSIONS = {".zbx", ".cjz", ".cos", ".qtfx"}
ZIP_EXTENSIONS = {".zip", ".rar", ".7z", ".cdz"}
TRADING_QINGDAN_SOURCE_ROLES = {"qingdan_package", "control_price_qingdan", "tender_qingdan"}
TRADING_QINGDAN_CONTAINER_KEYWORDS = ("工程量清单", "清单包", "招标控制价", "控制价")
TRADING_DRAWING_KEYWORDS = ("工程图", "施工图", "设计图", "图纸", "图审")
TRADING_GEOLOGICAL_KEYWORDS = ("地勘", "勘察", "岩土")
TRADING_TENDER_DOC_KEYWORDS = ("招标文件", "招标资料", "招标正文")
WEB_EXTENSIONS = {".html", ".htm"}
SOURCE_LEVELS = {"crawler", "filename", "manual", "extracted"}
LAYER0_ALLOWED_SOURCE_LEVELS = {"crawler", "filename", "manual"}
C_CLASS_ARCHIVE_FIELDS = {"title", "business_key", "domain_type", "channel_type", "region_code", "publish_date"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def metadata_cell(
    value: object,
    *,
    source_level: str,
    tagged_by: str,
    tagged_at: str | None = None,
) -> dict[str, object]:
    if source_level not in SOURCE_LEVELS:
        raise ValueError(f"UNSUPPORTED_SOURCE_LEVEL: {source_level}")
    if source_level == "extracted":
        raise ValueError("METADATA_EXTRACTION_FORBIDDEN")
    return {
        "value": value,
        "source_level": source_level,
        "tagged_by": tagged_by,
        "tagged_at": tagged_at or now_iso(),
    }


def metadata_value(value: object) -> object:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _validate_source_level(source_level: object, *, extraction_error_code: str) -> None:
    if source_level not in SOURCE_LEVELS:
        raise ValueError(f"UNSUPPORTED_SOURCE_LEVEL: {source_level}")
    if source_level == "extracted":
        raise ValueError(extraction_error_code)


def _validate_source_payload(
    source: object,
    *,
    field_name: str,
    invalid_error_code: str,
    extraction_error_code: str,
    tagged_by_error_code: str,
    tagged_at_error_code: str,
) -> None:
    if not isinstance(source, dict):
        raise ValueError(f"{invalid_error_code}: {field_name}")
    source_level = source.get("source_level")
    _validate_source_level(source_level, extraction_error_code=extraction_error_code)
    if not source.get("tagged_by"):
        raise ValueError(f"{tagged_by_error_code}: {field_name}")
    if not source.get("tagged_at"):
        raise ValueError(f"{tagged_at_error_code}: {field_name}")


def validate_metadata_cells(metadata: dict | None) -> dict:
    payload = metadata or {}
    if not isinstance(payload, dict):
        raise ValueError("METADATA_INVALID")
    if "project_group_key" in payload:
        raise ValueError("PROJECT_GROUP_KEY_FORBIDDEN")
    for key, cell in payload.items():
        if not isinstance(cell, dict) or "source_level" not in cell:
            raise ValueError(f"METADATA_CELL_REQUIRED: {key}")
        _validate_source_payload(
            cell,
            field_name=key,
            invalid_error_code="METADATA_CELL_REQUIRED",
            extraction_error_code="METADATA_EXTRACTION_FORBIDDEN",
            tagged_by_error_code="METADATA_TAGGED_BY_REQUIRED",
            tagged_at_error_code="METADATA_TAGGED_AT_REQUIRED",
        )
        if "value" not in cell:
            raise ValueError(f"METADATA_VALUE_REQUIRED: {key}")
    return dict(payload)


def validate_field_sources(field_sources: dict | None) -> dict:
    payload = field_sources or {}
    if not isinstance(payload, dict):
        raise ValueError("FIELD_SOURCES_INVALID")
    for key, source in payload.items():
        _validate_source_payload(
            source,
            field_name=key,
            invalid_error_code="FIELD_SOURCE_INVALID",
            extraction_error_code="FIELD_SOURCE_EXTRACTION_FORBIDDEN",
            tagged_by_error_code="FIELD_SOURCE_TAGGED_BY_REQUIRED",
            tagged_at_error_code="FIELD_SOURCE_TAGGED_AT_REQUIRED",
        )
    return dict(payload)


def validate_archive_provenance(
    *,
    archive_fields: dict,
    metadata: dict | None,
    field_sources: dict | None,
) -> tuple[dict, dict]:
    validated_metadata = validate_metadata_cells(metadata)
    validated_sources = validate_field_sources(field_sources)
    for field_name in sorted(C_CLASS_ARCHIVE_FIELDS):
        value = archive_fields.get(field_name)
        if value is not None and value != "" and field_name not in validated_sources:
            raise ValueError(f"FIELD_SOURCE_MISSING: {field_name}")
    return validated_metadata, validated_sources


def build_cost_info_business_key(
    *,
    source_id: str,
    region_code: str | None,
    period: str | None,
    title: str,
) -> str:
    return f"cost_info:{source_id}:{region_code or ''}:{period or ''}:{normalize_key_text(title)}"


def build_trading_business_key(
    *,
    source_id: str,
    source_item_key: str | None,
    title: str,
    publish_date: str | None,
    notice_type_raw: str | None,
) -> str:
    if source_item_key:
        return f"trading:{source_id}:{source_item_key}"
    normalized_title = normalize_key_text(title)
    normalized_notice_type = normalize_key_text(notice_type_raw)
    date_value = publish_date or ""
    return f"trading:{source_id}:{normalized_title}:{date_value}:{normalized_notice_type}"


def build_quota_business_key(
    *,
    source_id: str,
    title: str,
    region: str | None,
    quota_code: str | None,
    version: str | None,
    effective_date: str | None,
    standard_no: str | None,
) -> str:
    if region and quota_code and version and effective_date:
        return f"quota:{region}:{quota_code}:{version}:{effective_date}"
    if standard_no and version:
        if region:
            return f"quota:{region}:{standard_no}:{version}"
        return f"quota:{standard_no}:{version}"
    return f"quota:{source_id}:{normalize_key_text(title)}"


def build_policy_regulation_business_key(
    *,
    source_id: str,
    title: str,
    issuing_authority: str | None,
    document_no: str | None,
    publish_date: str | None,
) -> str:
    if document_no:
        return f"policy:{document_no}"
    hash_input = "|".join(
        [
            normalize_key_text(title),
            normalize_key_text(issuing_authority),
            publish_date or "",
        ]
    )
    digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:16]
    return f"policy:hash:{digest}"


def build_standard_atlas_business_key(
    *,
    source_id: str,
    title: str,
    standard_no: str | None,
    version: str | None,
    publish_date: str | None,
) -> str:
    if standard_no and version:
        return f"standard_atlas:{standard_no}:{version}"
    return f"standard_atlas:{source_id}:{normalize_key_text(title)}"


def _contains_any(value: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in value for keyword in keywords)


def infer_archive_file_role(
    *,
    file_name: str,
    is_primary: bool = False,
    source_item_role: str | None = None,
    domain_type: str | None = None,
) -> str:
    item_role = source_item_role or ""
    # 惰性 import: 打破 models ↔ archive_rules ↔ assets 循环依赖
    from app.assets import file_extension
    ext = file_extension(file_name)
    normalized_name = normalize_key_text(file_name)
    if ext in PRICED_SOURCE_EXTENSIONS:
        return "priced_source"
    if domain_type == "trading":
        normalized_role = normalize_key_text(item_role)
        if ext in ZIP_EXTENSIONS and (
            normalized_role in TRADING_QINGDAN_SOURCE_ROLES
            or _contains_any(normalized_role, TRADING_QINGDAN_CONTAINER_KEYWORDS)
            or _contains_any(normalized_name, TRADING_QINGDAN_CONTAINER_KEYWORDS)
        ):
            return "qingdan_package"
        if _contains_any(normalized_role, TRADING_DRAWING_KEYWORDS) or _contains_any(
            normalized_name,
            TRADING_DRAWING_KEYWORDS,
        ):
            return "drawing"
        if _contains_any(normalized_role, TRADING_GEOLOGICAL_KEYWORDS) or _contains_any(
            normalized_name,
            TRADING_GEOLOGICAL_KEYWORDS,
        ):
            return "geological"
        if ext not in ZIP_EXTENSIONS and (
            _contains_any(normalized_role, TRADING_TENDER_DOC_KEYWORDS)
            or _contains_any(
                normalized_name,
                TRADING_TENDER_DOC_KEYWORDS,
            )
        ):
            return "tender_doc"
    if ext in ZIP_EXTENSIONS:
        return "zip_package"
    if domain_type == "quota":
        if "补充" in normalized_name and "定额" in normalized_name:
            return "quota_supplement"
        if "解释" in normalized_name and "定额" in normalized_name:
            return "quota_interpretation"
        if "清单" in normalized_name or "gb50500" in normalized_name:
            return "bill_standard"
        if "定额" in normalized_name:
            return "quota_db"
    if domain_type == "policy_regulation":
        if not is_primary and ("附件" in normalized_name or ext in {".xlsx", ".xls"}):
            return "policy_attachment"
        if is_primary or ext in {".pdf", ".doc", ".docx"}:
            return "policy_document"
    if domain_type == "standard_atlas":
        if ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff"} or "扫描" in normalized_name:
            return "scan_image"
        if "gb" in normalized_name or "规范" in normalized_name or "标准" in normalized_name:
            return "standard_document"
        if ext == ".pdf":
            return "atlas_document"
    if item_role in {"web_page_snapshot", "public_notice"}:
        return "web_snapshot"
    if ext in WEB_EXTENSIONS:
        return "web_snapshot"
    if ext in {".jpg", ".jpeg", ".png"} and "封面" in normalized_name:
        return "cover"
    if is_primary:
        return "main_document"
    return "attachment"


def validate_archive_file_role(
    *,
    file_name: str,
    requested_role: str | None,
    is_primary: bool = False,
    source_item_role: str | None = None,
    domain_type: str | None = None,
) -> str:
    inferred_role = infer_archive_file_role(
        file_name=file_name,
        is_primary=is_primary,
        source_item_role=source_item_role,
        domain_type=domain_type,
    )
    if requested_role is None:
        return inferred_role
    if requested_role not in ARCHIVE_FILE_ROLES:
        raise ValueError(f"UNSUPPORTED_ARCHIVE_FILE_ROLE: {requested_role}")
    if requested_role == "priced_source" and inferred_role != "priced_source":
        raise ValueError("PRICED_SOURCE_ROLE_MISMATCH: priced_source is extension based in Layer 0")
    return requested_role
