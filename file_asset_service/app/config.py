import os
from dataclasses import dataclass


class RuntimeConfigurationError(RuntimeError):
    """Raised when a process is not configured for the shared NAS services."""


def _required_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    joined = " or ".join(names)
    raise RuntimeConfigurationError(f"Missing required environment variable: {joined}")


def _nas_postgres_url() -> str:
    database_url = _required_env("FILE_ASSET_DATABASE_URL", "DATABASE_URL")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeConfigurationError(
            "FILE_ASSET_DATABASE_URL must point to the shared NAS PostgreSQL instance; "
            "SQLite and local fallback databases are not supported."
        )
    return database_url


@dataclass(frozen=True)
class Settings:
    database_url: str
    s3_endpoint_url: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_region_name: str
    raw_bucket: str
    extract_bucket: str
    report_bucket: str
    nas_mirror_root: str | None
    parse_manifest_path: str | None
    mineru_api_url: str  # 2026-08-07 新增:web UI 调 MinerU OCR 的端点(广州 A1 接入 + 子进程覆盖 DEFAULT_API)


def get_settings() -> Settings:
    return Settings(
        database_url=_nas_postgres_url(),
        s3_endpoint_url=_required_env("FILE_ASSET_S3_ENDPOINT_URL"),
        s3_access_key_id=_required_env("FILE_ASSET_S3_ACCESS_KEY_ID"),
        s3_secret_access_key=_required_env("FILE_ASSET_S3_SECRET_ACCESS_KEY"),
        s3_region_name=os.getenv("FILE_ASSET_S3_REGION_NAME", "us-east-1"),
        raw_bucket=os.getenv("FILE_ASSET_RAW_BUCKET", "cost-raw"),
        extract_bucket=os.getenv("FILE_ASSET_EXTRACT_BUCKET", "cost-extract"),
        report_bucket=os.getenv("FILE_ASSET_REPORT_BUCKET", "cost-report"),
        nas_mirror_root=os.getenv("FILE_ASSET_NAS_MIRROR_ROOT") or None,
        parse_manifest_path=os.getenv("FILE_ASSET_PARSE_MANIFEST_PATH") or None,
        # 2026-08-07 新增:迁移时改 INFO_PRICE_MINERU_API_URL,默认指向内网 MinerU
        mineru_api_url=os.getenv("INFO_PRICE_MINERU_API_URL", "http://171.212.159.15:8000"),
    )
