import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "FILE_ASSET_DATABASE_URL",
        "postgresql+psycopg://file_asset:file_asset@127.0.0.1:5432/file_asset",
    )
    s3_endpoint_url: str = os.getenv("FILE_ASSET_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
    s3_access_key_id: str = os.getenv("FILE_ASSET_S3_ACCESS_KEY_ID", "minioadmin")
    s3_secret_access_key: str = os.getenv("FILE_ASSET_S3_SECRET_ACCESS_KEY", "minioadmin")
    s3_region_name: str = os.getenv("FILE_ASSET_S3_REGION_NAME", "us-east-1")
    raw_bucket: str = os.getenv("FILE_ASSET_RAW_BUCKET", "cost-raw")
    extract_bucket: str = os.getenv("FILE_ASSET_EXTRACT_BUCKET", "cost-extract")
    report_bucket: str = os.getenv("FILE_ASSET_REPORT_BUCKET", "cost-report")
    nas_mirror_root: str | None = os.getenv("FILE_ASSET_NAS_MIRROR_ROOT") or None
    parse_manifest_path: str | None = os.getenv("FILE_ASSET_PARSE_MANIFEST_PATH") or None


def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv(
            "FILE_ASSET_DATABASE_URL",
            "postgresql+psycopg://file_asset:file_asset@127.0.0.1:5432/file_asset",
        ),
        s3_endpoint_url=os.getenv("FILE_ASSET_S3_ENDPOINT_URL", "http://127.0.0.1:9000"),
        s3_access_key_id=os.getenv("FILE_ASSET_S3_ACCESS_KEY_ID", "minioadmin"),
        s3_secret_access_key=os.getenv("FILE_ASSET_S3_SECRET_ACCESS_KEY", "minioadmin"),
        s3_region_name=os.getenv("FILE_ASSET_S3_REGION_NAME", "us-east-1"),
        raw_bucket=os.getenv("FILE_ASSET_RAW_BUCKET", "cost-raw"),
        extract_bucket=os.getenv("FILE_ASSET_EXTRACT_BUCKET", "cost-extract"),
        report_bucket=os.getenv("FILE_ASSET_REPORT_BUCKET", "cost-report"),
        nas_mirror_root=os.getenv("FILE_ASSET_NAS_MIRROR_ROOT") or None,
        parse_manifest_path=os.getenv("FILE_ASSET_PARSE_MANIFEST_PATH") or None,
    )
