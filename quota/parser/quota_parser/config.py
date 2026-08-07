"""quota_parser 配置（路径 / 默认值 / Profile 注册表）"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# 默认 OCR 服务（局域网 MinerU）
DEFAULT_OCR_API = "http://172.16.20.23:8000"

# env 覆盖
ENV_OCR_URL = "QUOTA_PARSER_OCR_URL"
ENV_WORK_ROOT = "QUOTA_PARSER_WORK_ROOT"
ENV_POLL_INTERVAL = "QUOTA_PARSER_POLL_INTERVAL"
ENV_DATABASE_URL = "QUOTA_PARSER_DATABASE_URL"
ENV_FAILURE_RETENTION_DAYS = "QUOTA_PARSER_FAILURE_RETENTION_DAYS"

# 版本
PARSER_VERSION = "0.2.0"

# Worker 任务工作目录默认名（在系统临时目录下）
DEFAULT_WORK_ROOT_NAME = "quota-parser-jobs"

# 失败任务保留天数（成功后立即清理；失败保留 N 天供 debug）
DEFAULT_FAILURE_RETENTION_DAYS = 7


def get_ocr_api_url() -> str:
    return os.environ.get(ENV_OCR_URL, DEFAULT_OCR_API)


def get_work_root() -> Path:
    """Worker 任务工作目录根。

    优先级：env QUOTA_PARSER_WORK_ROOT > 系统临时目录/<DEFAULT_WORK_ROOT_NAME>。
    v0.3 起 default 改为平台无关（Linux 用 /tmp，Windows 用 %TEMP%），不再硬编码 D:/。
    """
    root = os.environ.get(ENV_WORK_ROOT)
    if root:
        return Path(root).resolve()
    return Path(tempfile.gettempdir()).resolve() / DEFAULT_WORK_ROOT_NAME


def get_failure_retention_days() -> int:
    """失败任务保留天数（成功任务立即清理）。"""
    raw = os.environ.get(ENV_FAILURE_RETENTION_DAYS)
    if raw is None:
        return DEFAULT_FAILURE_RETENTION_DAYS
    try:
        days = int(raw)
        return days if days >= 0 else DEFAULT_FAILURE_RETENTION_DAYS
    except ValueError:
        return DEFAULT_FAILURE_RETENTION_DAYS


def get_poll_interval() -> float:
    try:
        return float(os.environ.get(ENV_POLL_INTERVAL, "3.0"))
    except ValueError:
        return 3.0


def get_database_url() -> str:
    return os.environ.get(ENV_DATABASE_URL, "")


# 省份 → 关键词（与 quota-md-to-csv-v2 PROVINCE_KEYWORDS 对齐）
PROVINCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sc": ("四川", "川建"),
    "cq": ("重庆",),
    "gd": ("广东", "粤"),  # v0.13 已落地 extractors/gd/
    "hu": ("湖北", "鄂"),  # hu 提取器已落地 extractors/hu/
    # v0.4 §9 #15: default 作为 sentinel,不绑定省份关键词;用于 web 侧传"无偏好"。
    # extract_quota._load_province_module 不接受 — pipeline 必须在此之前收敛。
    "default": (),
}

PROVINCE_NAMES: dict[str, str] = {
    "sc": "四川",
    "cq": "重庆",
    "gd": "广东",
    "hu": "湖北",
}

# v0.4 §9 #15: 显式 sentinel 名;pipeline.py 在调省份子脚本前收敛到该值,
# 防止 extract_quota._load_province_module 抛 ValueError。
PROVINCE_DEFAULT_KEY: str = "default"


# 段临界页数（>100 页走分段 OCR）
CHUNK_THRESHOLD_PAGES = 100

# autofinalize 5 步顺序
FINALIZE_STEPS = [
    "clean_empty_qty.py",
    "drop_toc_sections.py",
    "fill_work_content.py",
    "space_split_materials.py",
    "finalize_last_step.py",  # 原 to_xlsx.py
]


# external/ 目录定位（用于 import 复用层）
PARSER_ROOT = Path(__file__).resolve().parent.parent
EXTERNAL_ROOT = PARSER_ROOT / "external"
MINERU_SCRIPT_DIR = EXTERNAL_ROOT / "mineru_pdf_parse" / "scripts"
QUOTA_MD_TO_CSV_DIR = EXTERNAL_ROOT / "quota_md_to_csv_v2"
QUOTA_CSV_FINALIZE_DIR = EXTERNAL_ROOT / "quota_csv_finalize"