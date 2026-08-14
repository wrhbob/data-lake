from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Iterator, Protocol

from app.config import get_settings

V01_BUCKETS = ("cost-raw", "cost-extract", "cost-report")


@dataclass(frozen=True)
class ObjectStat:
    byte_size: int
    content_type: str | None = None
    etag: str | None = None


class ReadableObjectBody(Protocol):
    def read(self, amount: int = -1) -> bytes:
        ...

    def close(self) -> None:
        ...


@dataclass
class ObjectStream:
    body: ReadableObjectBody
    content_length: int
    content_type: str | None = None
    etag: str | None = None

    def iter_chunks(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        try:
            while chunk := self.body.read(chunk_size):
                yield chunk
        finally:
            self.body.close()


class ObjectStore(Protocol):
    def ensure_buckets(self, buckets: tuple[str, ...] = V01_BUCKETS) -> None:
        ...

    def put_object(self, bucket: str, object_key: str, content: bytes, content_type: str | None = None) -> str:
        ...

    def stat_object(self, bucket: str, object_key: str) -> ObjectStat:
        ...

    def get_object(self, bucket: str, object_key: str) -> bytes:
        ...

    def open_object(
        self,
        bucket: str,
        object_key: str,
        byte_range: tuple[int, int] | None = None,
    ) -> ObjectStream:
        ...

    def delete_object(self, bucket: str, object_key: str) -> None:
        """删除对象；不存在抛 KeyError（404 NoSuchKey），调用方决定是否幂等"""
        ...


@dataclass(frozen=True)
class StoredObject:
    content: bytes
    content_type: str | None
    etag: str


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], StoredObject] = {}
        self.buckets: set[str] = set()
        self.put_count = 0

    def ensure_buckets(self, buckets: tuple[str, ...] = V01_BUCKETS) -> None:
        self.buckets.update(buckets)

    def put_object(self, bucket: str, object_key: str, content: bytes, content_type: str | None = None) -> str:
        self.buckets.add(bucket)
        etag = hashlib.md5(content, usedforsecurity=False).hexdigest()
        self.objects[(bucket, object_key)] = StoredObject(content=content, content_type=content_type, etag=etag)
        self.put_count += 1
        return etag

    def get_object(self, bucket: str, object_key: str) -> bytes:
        return self.objects[(bucket, object_key)].content

    def open_object(
        self,
        bucket: str,
        object_key: str,
        byte_range: tuple[int, int] | None = None,
    ) -> ObjectStream:
        stored = self.objects[(bucket, object_key)]
        content = stored.content
        if byte_range is not None:
            start, end = byte_range
            content = content[start : end + 1]
        return ObjectStream(
            body=BytesIO(content),
            content_length=len(content),
            content_type=stored.content_type,
            etag=stored.etag,
        )

    def stat_object(self, bucket: str, object_key: str) -> ObjectStat:
        stored = self.objects[(bucket, object_key)]
        return ObjectStat(byte_size=len(stored.content), content_type=stored.content_type, etag=stored.etag)

    def delete_object(self, bucket: str, object_key: str) -> None:
        # 2026-08-10 增：delete_xlsx_output 调用，404 抛 KeyError 保持幂等语义
        key = (bucket, object_key)
        if key not in self.objects:
            raise KeyError(key)
        del self.objects[key]


class S3ObjectStore:
    def __init__(
        self,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region_name: str,
    ) -> None:
        import boto3
        from botocore.config import Config

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
            config=Config(signature_version="s3v4"),
        )

    def ensure_buckets(self, buckets: tuple[str, ...] = V01_BUCKETS) -> None:
        for bucket in buckets:
            try:
                self.client.head_bucket(Bucket=bucket)
            except Exception:
                self.client.create_bucket(Bucket=bucket)

    def put_object(self, bucket: str, object_key: str, content: bytes, content_type: str | None = None) -> str:
        kwargs: dict[str, object] = {"Bucket": bucket, "Key": object_key, "Body": content}
        if content_type:
            kwargs["ContentType"] = content_type
        response = self.client.put_object(**kwargs)
        return str(response.get("ETag", "")).strip('"')

    def stat_object(self, bucket: str, object_key: str) -> ObjectStat:
        from botocore.exceptions import ClientError

        try:
            response = self.client.head_object(Bucket=bucket, Key=object_key)
        except ClientError as exc:
            if _is_missing_object_error(exc):
                raise KeyError((bucket, object_key)) from exc
            raise
        return ObjectStat(
            byte_size=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType"),
            etag=str(response.get("ETag", "")).strip('"') or None,
        )

    def get_object(self, bucket: str, object_key: str) -> bytes:
        stream = self.open_object(bucket, object_key)
        return b"".join(stream.iter_chunks())

    def open_object(
        self,
        bucket: str,
        object_key: str,
        byte_range: tuple[int, int] | None = None,
    ) -> ObjectStream:
        from botocore.exceptions import ClientError

        kwargs: dict[str, object] = {"Bucket": bucket, "Key": object_key}
        if byte_range is not None:
            start, end = byte_range
            kwargs["Range"] = f"bytes={start}-{end}"
        try:
            response = self.client.get_object(**kwargs)
        except ClientError as exc:
            if _is_missing_object_error(exc):
                raise KeyError((bucket, object_key)) from exc
            raise
        return ObjectStream(
            body=response["Body"],
            content_length=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType"),
            etag=str(response.get("ETag", "")).strip('"') or None,
        )

    def delete_object(self, bucket: str, object_key: str) -> None:
        # 2026-08-10 增：delete_xlsx_output 调用；404 NoSuchKey 抛 KeyError 走幂等
        from botocore.exceptions import ClientError

        try:
            self.client.delete_object(Bucket=bucket, Key=object_key)
        except ClientError as exc:
            if _is_missing_object_error(exc):
                raise KeyError((bucket, object_key)) from exc
            raise


def _is_missing_object_error(exc: object) -> bool:
    response = getattr(exc, "response", {})
    error = response.get("Error", {})
    status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return error.get("Code") in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"} or status_code == 404


def get_object_store() -> ObjectStore:
    settings = get_settings()
    store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region_name,
    )
    store.ensure_buckets((settings.raw_bucket, settings.extract_bucket, settings.report_bucket))
    return store


def safe_partition(value: str | None) -> str:
    if not value:
        return "none"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "none"


def build_object_key(
    *,
    tenant_code: str,
    source_type: str,
    batch_id: str | None,
    sha256: str,
    file_ext: str,
) -> str:
    suffix = file_ext if not file_ext or file_ext.startswith(".") else f".{file_ext}"
    return (
        f"tenant={safe_partition(tenant_code)}/"
        f"source={safe_partition(source_type)}/"
        f"batch={safe_partition(batch_id)}/"
        f"sha256={sha256[:2]}/{sha256}{suffix}"
    )


def build_blob_storage_key(*, blob_hash: str, file_ext: str) -> str:
    normalized_hash = blob_hash.lower()
    normalized_ext = (file_ext or "").lower()
    suffix = normalized_ext if not normalized_ext or normalized_ext.startswith(".") else f".{normalized_ext}"
    return f"objects/{normalized_hash[:2]}/{normalized_hash[2:4]}/{normalized_hash}{suffix}"
