import boto3
import pytest
from botocore.exceptions import ClientError

from app.storage import S3ObjectStore, build_blob_storage_key


def test_build_blob_storage_key_is_content_addressed():
    key = build_blob_storage_key(
        blob_hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        file_ext=".pdf",
    )

    assert key == "objects/ab/cd/abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890.pdf"


def test_build_blob_storage_key_normalizes_extension():
    key = build_blob_storage_key(
        blob_hash="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        file_ext="cjz",
    )

    assert key == "objects/12/34/1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef.cjz"


def test_build_blob_storage_key_normalizes_hash_and_extension_case():
    key = build_blob_storage_key(
        blob_hash="ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890",
        file_ext=".PDF",
    )

    assert key == "objects/ab/cd/abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890.pdf"


def test_s3_object_store_maps_missing_object_to_key_error(monkeypatch):
    class MissingObjectClient:
        def get_object(self, *, Bucket, Key):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: MissingObjectClient())
    store = S3ObjectStore(
        endpoint_url="http://minio.example",
        access_key_id="access",
        secret_access_key="secret",
        region_name="us-east-1",
    )

    with pytest.raises(KeyError):
        store.get_object("cost-raw", "missing.pdf")
