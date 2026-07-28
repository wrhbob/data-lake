"""quota_parser 配置（路径 / 默认值 / Profile 注册表）"""
from __future__ import annotations

import os
from pathlib import Path

# 默认 OCR 服务（局域网 MinerU）
DEFAULT_OCR_API = "http://172.16.20.23:8000"

# env 覆盖
ENV_OCR_URL = "QUOTA_PARSER_OCR_URL"
ENV_WORK_ROOT = "QUOTA_PARSER_WORK_ROOT"
ENV_POLL_INTERVAL = "QUOTA_PARSER_POLL_INTERVAL"
ENV_DATABASE_URL = "QUOTA_PARSER_DATABASE_URL"

# 版本
PARSER_VERSION = "0.2.0"


def get_ocr_api_url() -> str:
    return os.environ.get(ENV_OCR_URL, DEFAULT_OCR_API)


def get_work_root() -> Path:
    """Worker 任务工作目录根。"""
    root = os.environ.get(ENV_WORK_ROOT)
    if root:
        return Path(root).resolve()
    return Path("D:/quota-parser-jobs").resolve()


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
}

PROVINCE_NAMES: dict[str, str] = {
    "sc": "四川",
    "cq": "重庆",
}


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