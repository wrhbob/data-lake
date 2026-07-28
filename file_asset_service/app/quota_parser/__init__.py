"""quota_parser web-side adapter (DB ↔ MinIO ↔ quota_parser 包)

薄桥接层 —— 不复制 quota_parser 包的业务逻辑，只做：
  1. 解析阶段 A/B 状态机推进（写 Archive.parse_* 13 列）
  2. PDF 从 MinIO 临时下载到本地 work_root，调 run_quota_pipeline
  3. 产物（candidate.xlsx / final.xlsx / manifest.json / qa_report.*）上传 MinIO
  4. 异常 → parse_error_code 映射（parser/SPEC.md §11）

见 quota/INTEGRATION_PLAN.md §3（adapter 层）。
"""
from .service import (
    PROFILES,
    PARSE_BUCKET_CANDIDATE,
    PARSE_BUCKET_REPORT,
    PARSE_MOCK_ENV_VAR,
    build_parse_section,
    is_parse_mock,
    trigger_parse,
    upload_reviewed,
    validate_reviewed_xlsx,
)

__all__ = [
    "PROFILES",
    "PARSE_BUCKET_CANDIDATE",
    "PARSE_BUCKET_REPORT",
    "PARSE_MOCK_ENV_VAR",
    "is_parse_mock",
    "trigger_parse",
    "upload_reviewed",
    "build_parse_section",
    "validate_reviewed_xlsx",
]