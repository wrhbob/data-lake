"""Mock 模式：当 QUOTA_PARSE_MOCK=1 时替代真 worker。

实现要点（web-frontend SPEC §8 硬约束 1-4）：
  1. 端点必须 async def，asyncio.create_task 启动后台任务，立即返回
  2. 后台任务独立 SessionLocal() 拿新 session（避免 DetachedInstanceError）
  3. MinIO 写也要在后台（不阻塞端点返回）
  4. asyncio.create_task 引用存到模块级 set，防止被 GC

行为：
  - POST /parse: 5-10s sleep → parse_status='parsed' + 假 candidate.xlsx 上传
  - POST /reviewed: 2-3s sleep → parse_status='qa_passed' + 假 final.xlsx 上传
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.database import get_session_factory
from app.models import Archive
from app.storage import get_object_store

logger = logging.getLogger(__name__)

# task 引用集合（防止被 GC；task done 时回调 discard）
_TASKS: set[asyncio.Task] = set()

# mock 默认时长（秒）
MOCK_A_SECONDS = 5.0  # 阶段 A：OCR + 抽取 + autofinalize 模拟
MOCK_B_SECONDS = 2.0  # 阶段 B：人工 reviewed 直接落 final 模拟


# === 辅助：独立 session ===

def _new_session():
    """独立拿新 session（避免与 FastAPI 请求作用域冲突）。"""
    return get_session_factory()()


def _now() -> datetime:
    return datetime.now(UTC)


# === 辅助：写假 xlsx 到 MinIO ===

def _build_fake_xlsx(*, sheet_name: str, rows: int, cols: int) -> bytes:
    """构造一个最小可用的 xlsx 字节内容。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    for i in range(rows):
        ws.append([f"mock-row-{i}-col-{j}" for j in range(cols)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload_bytes(bucket: str, key: str, data: bytes, *, content_type: str) -> None:
    store = get_object_store()
    store.put_object(bucket, key, data, content_type=content_type)


def _sha256_bytes(data: bytes) -> str:
    """mock runner 独立 SHA-256（与 quota_parser.service._sha256_bytes 等价）。"""
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _build_mock_manifest(*, archive: Archive, phase: str, status: str,
                          artifacts: list[dict[str, str]]) -> dict[str, Any]:
    """构造符合 quota-parser-result/v1 schema 的 Manifest dict（mock 用）。

    字段集与 quota_parser.service._reconstruct_manifest 对齐 — 前端零翻译。
    """
    from app.quota_parser.service import MANIFEST_SCHEMA

    mp = archive.metadata_payload or {}
    prov_cell = mp.get("province")
    province_value: str | None = None
    if isinstance(prov_cell, dict):
        province_value = prov_cell.get("value")
    elif isinstance(prov_cell, str):
        province_value = prov_cell

    metrics = dict(archive.parse_metrics or {})
    metrics.setdefault("mock", True)
    return {
        "$schema": MANIFEST_SCHEMA,
        "task_id": archive.parse_task_id or f"qp_mock_{archive.archive_id[:8]}",
        "phase": phase,
        "status": status,
        "parser_version": archive.parse_parser_version or "0.2.0",
        "profile": archive.parse_profile,
        "province": province_value,
        "ocr_api_url": "http://172.16.20.23:8000",
        "source_pdf_sha256": None,
        "candidate_xlsx_sha256": metrics.get("candidate_xlsx_sha256"),
        "final_xlsx_sha256": metrics.get("final_xlsx_sha256"),
        "artifacts": artifacts,
        "metrics": metrics,
        "warnings": archive.parse_warnings or [],
    }


def _upload_manifest(archive: Archive, manifest: dict[str, Any]) -> None:
    """把 manifest dict 落 MinIO（cost-report 桶，key = quota/<aid>/manifest.json）。

    v0.4 §8 #14：manifest.json 真落 MinIO，便于 audit / replay。
    """
    from app.quota_parser.service import PARSE_BUCKET_REPORT

    key = f"quota/{archive.archive_id}/manifest.json"
    payload = json.dumps(manifest, ensure_ascii=False, default=str).encode("utf-8")
    _upload_bytes(
        PARSE_BUCKET_REPORT, key, payload,
        content_type="application/json; charset=utf-8",
    )


# === 阶段 A mock ===

async def run_mock_pipeline_a(archive_id: str, *, candidate_seconds: float = MOCK_A_SECONDS) -> None:
    """阶段 A mock：sleep → 写 parse_status='parsed' + 上传假 candidate.xlsx。"""
    # 1. 初始校验：档案存在且 parse_status='parsing'（避免 mock 与端点错位）
    with _new_session() as session:
        archive = session.get(Archive, archive_id)
        if archive is None:
            logger.warning("mock_a: archive %s 不存在，跳过", archive_id)
            return
        if archive.parse_status != "parsing":
            logger.warning(
                "mock_a: archive %s parse_status=%r != 'parsing'，跳过",
                archive_id,
                archive.parse_status,
            )
            return
        profile = archive.parse_profile or "sichuan"

    # 2. sleep 模拟 OCR + 抽取耗时
    try:
        await asyncio.sleep(candidate_seconds)
    except asyncio.CancelledError:
        logger.warning("mock_a cancelled archive_id=%s", archive_id)
        return

    # 3. 构造假 candidate.xlsx 并上传（复用 cost-extract 桶，key 前缀 quota/）
    fake_xlsx = _build_fake_xlsx(sheet_name="定额条目", rows=10, cols=10)
    candidate_key = f"quota/{archive_id}/candidate.xlsx"
    candidate_sha = _sha256_bytes(fake_xlsx)
    _upload_bytes(
        "cost-extract",
        candidate_key,
        fake_xlsx,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # 4. 写 Archive.parse_* 字段
    finished_at = _now()
    with _new_session() as session:
        archive = session.get(Archive, archive_id)
        if archive is None:
            logger.warning("mock_a: archive %s 写结果时已不存在", archive_id)
            return
        archive.parse_status = "parsed"
        archive.parse_phase = "stage_a"
        archive.parse_parser_version = "0.2.0"
        archive.parse_finished_at = finished_at
        archive.parse_metrics = {
            "pages": 100,
            "ocr_seconds": int(candidate_seconds),
            "candidate_rows": 10,
            "candidate_xlsx_sha256": candidate_sha,
            "warnings": 1,
            "mock": True,
        }
        archive.parse_warnings = ["mock mode — metrics are fake"]
        archive.parse_error_code = None
        archive.parse_error_message = None
        archive.candidate_xlsx_key = candidate_key
        session.commit()
        # v0.4 §8 #14: refresh 后再读 metadata_payload,避免 SQLAlchemy 缓存过期值
        session.refresh(archive)

    # 5. v0.4 §8 #14: 写 manifest.json 到 cost-report 桶
    mock_manifest = _build_mock_manifest(
        archive=archive,
        phase="stage_a", status="candidate_ready",
        artifacts=[{"kind": "candidate_xlsx", "key": candidate_key}],
    )
    _upload_manifest(archive, mock_manifest)

    logger.info("mock_a done archive_id=%s status=parsed manifest_uploaded=True", archive_id)


def schedule_mock_a(archive_id: str) -> asyncio.Task:
    """封装 asyncio.create_task，引用放进 _TASKS 集合。"""
    task = asyncio.create_task(run_mock_pipeline_a(archive_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


# === 阶段 B mock ===

async def run_mock_pipeline_b(archive_id: str, *, reviewed_bytes: bytes | None = None,
                              candidate_seconds: float = MOCK_B_SECONDS) -> None:
    """阶段 B mock：sleep → 写 parse_status='qa_passed' + 上传假 final.xlsx。"""
    # 1. 校验：档案存在且 parse_status='parsed'（mock 模式下跳过 openpyxl 结构校验）
    with _new_session() as session:
        archive = session.get(Archive, archive_id)
        if archive is None:
            logger.warning("mock_b: archive %s 不存在，跳过", archive_id)
            return
        if archive.parse_status != "parsed":
            logger.warning(
                "mock_b: archive %s parse_status=%r != 'parsed'，跳过",
                archive_id,
                archive.parse_status,
            )
            return

    # 2. sleep
    try:
        await asyncio.sleep(candidate_seconds)
    except asyncio.CancelledError:
        logger.warning("mock_b cancelled archive_id=%s", archive_id)
        return

    # 3. 落 final.xlsx。
    # 关键:用户上传的 reviewed_bytes 是真产物,要原样落到 MinIO;只在没传 reviewed_bytes 时
    # 才回退到占位假 xlsx(此前一直回退,导致 UI 下载 final.xlsx 看到的是 mock 占位而不是
    # 用户实际审核后的内容,bug 修复前 user 体感是"上传没生效")。
    if reviewed_bytes:
        final_xlsx_bytes = reviewed_bytes
        logger.info("mock_b: 使用用户上传的 reviewed_bytes (%d B)", len(reviewed_bytes))
    else:
        final_xlsx_bytes = _build_fake_xlsx(sheet_name="定额条目", rows=10, cols=10)
        logger.info("mock_b: 未传 reviewed_bytes, 落占位假 xlsx (%d B)", len(final_xlsx_bytes))

    final_key = f"quota/{archive_id}/final.xlsx"
    final_sha = _sha256_bytes(final_xlsx_bytes)
    _upload_bytes(
        "cost-extract",
        final_key,
        final_xlsx_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # 4. 写 Archive.parse_* 字段
    finished_at = _now()
    with _new_session() as session:
        archive = session.get(Archive, archive_id)
        if archive is None:
            logger.warning("mock_b: archive %s 写结果时已不存在", archive_id)
            return
        archive.parse_status = "qa_passed"
        archive.parse_phase = "stage_b"
        archive.parse_finished_at = finished_at
        archive.parse_error_code = None
        archive.parse_error_message = None
        archive.final_xlsx_key = final_key
        archive.parse_metrics = {
            **(archive.parse_metrics or {}),
            "final_xlsx_sha256": final_sha,
            "mock": True,
            "from_reviewed_upload": bool(reviewed_bytes),
        }
        session.commit()
        # v0.4 §8 #14: refresh 后再读 metadata_payload,避免 SQLAlchemy 缓存过期值
        session.refresh(archive)

    # 5. v0.4 §8 #14: 覆盖 manifest.json（phase=stage_b, status=qa_passed）
    candidate_key = archive.candidate_xlsx_key
    artifacts = [
        a for a in [
            {"kind": "candidate_xlsx", "key": candidate_key} if candidate_key else None,
            {"kind": "final_xlsx", "key": final_key},
        ] if a
    ]
    mock_manifest = _build_mock_manifest(
        archive=archive,
        phase="stage_b", status="qa_passed",
        artifacts=artifacts,
    )
    _upload_manifest(archive, mock_manifest)

    logger.info("mock_b done archive_id=%s status=qa_passed manifest_uploaded=True", archive_id)


def schedule_mock_b(archive_id: str, *, reviewed_bytes: bytes | None = None) -> asyncio.Task:
    """封装 asyncio.create_task，引用放进 _TASKS 集合。"""
    task = asyncio.create_task(run_mock_pipeline_b(archive_id, reviewed_bytes=reviewed_bytes))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


# === Manifest / QA 报告 fixture（mock 模式下用） ===

def fake_manifest(archive_id: str, *, parse_status: str | None = None) -> dict[str, Any]:
    """返回符合 parser/SPEC.md §6.2 schema 的假 Manifest。

    parse_status 决定 phase/status：
      - None / 'parsing'          → phase=stage_a, status=candidate_running
      - 'parsed'                  → phase=stage_a, status=candidate_ready
      - 'qa_passed' / 'rejected'  → phase=stage_b, status=qa_passed / qa_rejected
    """
    if parse_status == "qa_passed":
        phase, status = "stage_b", "qa_passed"
        artifacts = [
            {"kind": "candidate_xlsx", "key": f"quota/{archive_id}/candidate.xlsx"},
            {"kind": "final_xlsx", "key": f"quota/{archive_id}/final.xlsx"},
        ]
    elif parse_status == "parsed":
        phase, status = "stage_a", "candidate_ready"
        artifacts = [{"kind": "candidate_xlsx", "key": f"quota/{archive_id}/candidate.xlsx"}]
    else:
        phase, status = "stage_a", "candidate_running"
        artifacts = []

    return {
        "$schema": "quota-parser-result/v1",
        "task_id": f"qp_mock_{archive_id[:8]}",
        "phase": phase,
        "status": status,
        "parser_version": "0.2.0",
        "profile": "sichuan",
        "province": "sichuan",
        "ocr_api_url": "http://172.16.20.23:8000",
        "source_pdf_sha256": None,
        "candidate_xlsx_sha256": None,
        "artifacts": artifacts,
        "metrics": {"pages": 100, "ocr_seconds": 5, "candidate_rows": 10, "warnings": 1, "mock": True},
        "warnings": ["mock mode — values are fake"],
    }


def fake_qa_report_json(archive_id: str) -> dict[str, Any]:
    """返回符合 parser/SPEC.md §12.2 schema 的假 qa_report.json。"""
    return {
        "$schema": "quota-parser-qa/v1",
        "task_id": f"qp_mock_{archive_id[:8]}",
        "summary": "ok",
        "checks": [
            {"name": "sheet_names", "status": "ok"},
            {"name": "row_type_enum", "status": "ok"},
            {"name": "row_drop_ratio", "status": "ok", "detail": "row_drop_ratio=0.00"},
        ],
        "mock": True,
    }


def fake_qa_report_md(archive_id: str) -> str:
    """返回人类可读的假 qa_report.md。"""
    return (
        f"# QA Report (mock)\n\n"
        f"- archive_id: `{archive_id}`\n"
        f"- parser_version: 0.2.0\n"
        f"- summary: ok (mock)\n\n"
        f"_This report is a fixture generated by `QUOTA_PARSE_MOCK=1`._\n"
    )