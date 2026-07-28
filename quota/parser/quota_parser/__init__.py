"""quota_parser — 定额 PDF → 多 sheet xlsx 的 Python 包入口（v0.2）

子模块（Worker 调用入口）:
    pipeline.run_quota_pipeline(...)         阶段 A: PDF → candidate xlsx
    pipeline.finalize_reviewed_xlsx(...)     阶段 B: reviewed → final xlsx
    pipeline.serve_worker(...)               常驻 Worker（轮询数据库）

复用层（external/）:
    external.mineru_pdf_parse.scripts.parse_pdf / parse_chunked / health_check / render
    external.quota_md_to_csv_v2.extract_quota.process_md_file
    external.quota_csv_finalize.{clean_empty_qty,drop_toc_sections,
                                fill_work_content,space_split_materials,
                                finalize_last_step}.process_xlsx

约束:
    - 只 import 上述函数,不复制业务逻辑（行为零变更）。
    - 一切失败以异常抛出;Worker 层负责状态机映射。
"""
__version__ = "0.3.0"

from .pipeline import run_quota_pipeline, finalize_reviewed_xlsx, serve_worker
from .cleanup import cleanup_workspace, cleanup_expired_jobs
from .result import StageAResult, StageBResult
from .exceptions import (
    QuotaParserError,
    OcrUnavailableError,
    OcrTransientError,
    UnsupportedProvinceError,
    InvalidPageRangeError,
    InvalidXlsxStructureError,
    ProfileExecutionError,
    WorkdirNotWritableError,
)

__all__ = [
    "run_quota_pipeline",
    "finalize_reviewed_xlsx",
    "serve_worker",
    "cleanup_workspace",
    "cleanup_expired_jobs",
    "StageAResult",
    "StageBResult",
    "QuotaParserError",
    "OcrUnavailableError",
    "OcrTransientError",
    "UnsupportedProvinceError",
    "InvalidPageRangeError",
    "InvalidXlsxStructureError",
    "ProfileExecutionError",
    "WorkdirNotWritableError",
]