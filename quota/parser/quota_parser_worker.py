"""quota_parser_worker — 独立进程消费 quota_parse_job 表（INTEGRATION_PLAN §1.2.3）

设计：
  - fcntl.flock 防运维双启（Windows 跳过，靠 DB FOR UPDATE SKIP LOCKED 兜底）
  - 主循环每 2s 抢一个 queued job
  - mock / real 分流（按 job.metadata_payload["mock"]）
  - chunk 完成时通过 callback 写 last_heartbeat_at + chunks_done
  - 完成后写 archive_file 4 行 + manifest.json + 更新 archive.parse_*

heartbeat 机制（v0.6 §#6）：
  - parse_chunked 加 on_chunk_done callback，每个 chunk 完成（成功/失败）调一次
  - worker._on_chunk_progress 内部调 _update_job_fields 刷心跳 + 推进 chunks_done
  - 失败 chunk 不递增 chunks_done，但 last_heartbeat_at 必刷（sweeper 兜底需要）

sweeper（v0.6 §#5）：
  - v0.6 起独立进程 `quota_parser_sweeper.py`，不再嵌入 worker
  - 主进程只管抢单 + 处理 job，心跳全靠 callback
  - 每 60s 扫一次 status='running' 的 job，首 chunk 30min / 后续 15min 未推进 → 标 failed
  - worker 与 sweeper 必须同机部署（共享 .env / 共享 DB 网络）

入口：
  python -u quota/parser/quota_parser_worker.py
"""
from __future__ import annotations

# fcntl 仅 Linux/Mac 有；Windows 上 _acquire_lock 走 None 路径
import hashlib
import json
import logging
import os
import signal
import socket
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Windows GBK 兜底：parse_chunked.py 里有 emoji print → 必须 UTF-8 stdout
# 必须在任何 print / 触发 stdio 的代码之前设上
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass

# ── 路径设置 ──
# this file = quota/parser/quota_parser_worker.py
#   ROOT       = data_lake0714/
#   app/       = file_asset_service/app/  (parent of file_asset_service/)
#   quota_parser package = quota/parser/quota_parser/
#   所以: 把 file_asset_service/ 和 quota/parser/ 都塞 sys.path,
#        让 `from app.X import ...` 和 `from quota_parser.X import ...` 都 work
ROOT = Path(__file__).resolve().parent.parent.parent  # .../data_lake0714
sys.path.insert(0, str(ROOT / "file_asset_service"))    # → from app.database import ...
sys.path.insert(0, str(ROOT / "quota" / "parser"))      # → from quota_parser.pipeline import ...

# ── 超时阈值 ──
FIRST_CHUNK_TIMEOUT = timedelta(minutes=30)
SUBSEQUENT_CHUNK_TIMEOUT = timedelta(minutes=15)
SWEEPER_INTERVAL_SECONDS = 60
POLL_INTERVAL_SECONDS = 2
LOCKFILE = Path(os.environ.get(
    "QUOTA_PARSER_WORKER_LOCKFILE", "/tmp/quota_parser_worker.lock"
))

logger = logging.getLogger("quota_parser_worker")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    mock_mode = os.environ.get("QUOTA_PARSE_MOCK", "0") == "1"
    logger.warning(
        "quota_parser_worker 启动 pid=%d host=%s mock=%s",
        os.getpid(), socket.gethostname(), mock_mode,
    )

    # fcntl flock — Windows 跳过
    flock_fd = None
    if sys.platform != "win32":
        import fcntl as _fcntl  # Linux/Mac 才有
        try:
            flock_fd = open(LOCKFILE, "w")
            _fcntl.flock(flock_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            flock_fd.write(f"{os.getpid()}\n")
            flock_fd.flush()
            logger.info("acquired flock on %s", LOCKFILE)
        except (BlockingIOError, OSError) as e:
            logger.error("另一个 worker 已占 %s — 退出: %s", LOCKFILE, e)
            return 1
    else:
        logger.warning("Windows: 跳过 fcntl.flock，依赖 DB FOR UPDATE SKIP LOCKED 防双抢")

    shutdown_requested = False

    def _on_sigterm(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.warning("收到 SIGTERM，处理完当前 job 后退出")

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    while not shutdown_requested:
        try:
            job = _claim_one_job()
            if job is None:
                # v0.6 §#5: sweeper 已独立成进程,worker 只管抢单 + 处理 job
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            logger.info(
                "claimed job_id=%s archive_id=%s profile=%s mock=%s",
                job.job_id, job.archive_id, job.profile,
                bool(job.metadata_payload.get("mock")),
            )
            try:
                _process_job(job)
            except Exception as e:
                logger.exception("处理 job %s 失败: %s", job.job_id, e)
                _mark_job_failed(job, error_code="failed_permanent", message=f"{type(e).__name__}: {e}")

        except Exception as e:
            logger.exception("主循环异常: %s", e)
            time.sleep(POLL_INTERVAL_SECONDS)

    logger.warning("worker 退出 pid=%d", os.getpid())
    if flock_fd:
        flock_fd.close()
    return 0


# ─────────────────────────────────────────────────────────
# 抢单（SELECT FOR UPDATE SKIP LOCKED）
# ─────────────────────────────────────────────────────────
def _claim_one_job():
    """抢一个 queued job，标 running + 写 worker_pid + heartbeat。

    Returns:
        QuotaParseJob 实例，或 None（队列空）。
    """
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.models import QuotaParseJob

    with get_session_factory()() as session:
        # SKIP LOCKED 防止多 worker / 同 worker 同帧抢同一行
        row = session.execute(text("""
            SELECT job_id FROM quota_parse_job
            WHERE status='queued' ORDER BY enqueued_at
            LIMIT 1 FOR UPDATE SKIP LOCKED
        """)).first()
        if row is None:
            session.rollback()
            return None
        job_id = row[0]
        now = datetime.now(UTC)
        session.execute(
            text("""UPDATE quota_parse_job
                    SET status='running', worker_pid=:pid, worker_hostname=:host,
                        started_at=:now, last_heartbeat_at=:now, attempt=attempt+1
                    WHERE job_id=:job_id"""),
            {"pid": os.getpid(), "host": socket.gethostname(),
             "now": now, "job_id": job_id},
        )
        session.commit()
        return session.get(QuotaParseJob, job_id)


# ─────────────────────────────────────────────────────────
# 处理 job（mock/real 分流）
# ─────────────────────────────────────────────────────────
def _process_job(job) -> None:
    is_mock = bool(job.metadata_payload.get("mock"))
    if is_mock:
        _process_mock_job(job)
    else:
        _process_real_job(job)


def _process_mock_job(job) -> None:
    """Mock worker：调 mock_parse_runner.run_mock_pipeline_a（已经写 archive.parse_*
    + candidate.xlsx + manifest.json），worker 只负责标 job done。
    """
    import asyncio
    from app.mock_parse_runner import run_mock_pipeline_a

    try:
        asyncio.run(run_mock_pipeline_a(job.archive_id, candidate_seconds=5.0))
    except Exception as e:
        logger.exception("mock pipeline 失败 job_id=%s: %s", job.job_id, e)
        _mark_job_failed(job, error_code="failed_permanent", message=f"mock: {e}")
        return

    _mark_job_done(job.job_id, chunks_total=1, chunks_done=1)


def _process_real_job(job) -> None:
    """Real worker：下载 PDF → run_quota_pipeline → 上传产物 → 注册 archive_file →
    写 archive.parse_* + manifest.json。
    """
    from sqlalchemy import select
    from sqlalchemy import text as sa_text

    from app.database import get_session_factory
    from app.models import Archive, ArchiveFile, FileAsset
    from app.quota_parser.service import (
        MANIFEST_SCHEMA,
        PARSE_BUCKET_CANDIDATE,
        PARSE_BUCKET_REPORT,
        register_parse_artifact,
    )
    from app.storage import get_object_store
    from quota_parser.config import CHUNK_THRESHOLD_PAGES, get_ocr_api_url
    from quota_parser.pipeline import run_quota_pipeline

    # 1. 找主 PDF：ArchiveFile(main_document, is_primary) → FileAsset
    with get_session_factory()() as session:
        archive = session.get(Archive, job.archive_id)
        if archive is None:
            _mark_job_failed(job, error_code="failed_user",
                             message=f"archive {job.archive_id} 不存在")
            return
        af_main = session.execute(
            select(ArchiveFile).where(
                ArchiveFile.archive_id == job.archive_id,
                ArchiveFile.file_role == "main_document",
                ArchiveFile.is_primary == True,  # noqa: E712
            )
        ).scalar_one_or_none()
        if af_main is None or af_main.file_id is None:
            _mark_job_failed(job, error_code="failed_user",
                             message="archive 无 main_document 主文件")
            return
        fa = session.get(FileAsset, af_main.file_id)
        if fa is None:
            _mark_job_failed(job, error_code="failed_user",
                             message=f"file_asset {af_main.file_id} 不存在")
            return
        pdf_bucket = fa.bucket
        pdf_object_key = fa.object_key
        source_sha = fa.sha256

    # 2. 下载 PDF 到本地 temp（get_object 拿 bytes → 落临时文件）
    work_root = Path(os.environ.get(
        "QUOTA_PARSER_WORK_ROOT", "/tmp/quota_parser_work"
    )) / job.job_id
    work_root.mkdir(parents=True, exist_ok=True)
    local_pdf = work_root / "source.pdf"
    store = get_object_store()
    pdf_bytes = store.get_object(pdf_bucket, pdf_object_key)
    local_pdf.write_bytes(pdf_bytes)
    logger.info("downloaded pdf bucket=%s key=%s size=%d bytes",
                pdf_bucket, pdf_object_key, len(pdf_bytes))

    # 3. 估算 chunks_total（按 PDF 页数 / 100）
    page_count = _read_pdf_page_count(local_pdf)
    chunks_total = max(1, (page_count + CHUNK_THRESHOLD_PAGES - 1) // CHUNK_THRESHOLD_PAGES)
    _update_job_fields(job.job_id, chunks_total=chunks_total)

    # 4. 跑 pipeline（核心 — 阻塞 5-15 分钟）
    ocr_api_url = job.metadata_payload.get("ocr_api_url") or get_ocr_api_url()
    province = job.metadata_payload.get("province")
    try:
        result = run_quota_pipeline(
            pdf_path=str(local_pdf),
            work_dir=str(work_root),
            province=province,
            ocr_api_url=ocr_api_url,
            profile=job.profile,
            task_id=job.job_id,
            enable_chunking=True,
            # v0.6 §#6: 用闭包把 job_id 注入,parse_chunked 看到的签名仍是 (int, int, str)
            on_chunk_done=lambda idx, tot, st: _on_chunk_progress(job.job_id, idx, tot, st),
        )
    except Exception as e:
        logger.exception("run_quota_pipeline 失败 job_id=%s", job.job_id)
        _mark_job_failed(job, error_code="failed_permanent",
                         message=f"pipeline: {type(e).__name__}: {e}")
        return

    # v0.6 §#6: chunks_done 由 callback 实时推进,此处不再一次性写
    chunks_done = chunks_total   # 最终值给下游 archive.parse_metrics.chunks_done 用

    # 5. 上传 4 类产物到 MinIO + register_parse_artifact
    artifacts_meta: list[dict[str, str]] = []
    artifact_specs = [
        ("parse_markdown", result.ocr_markdown_path,
         "text/markdown; charset=utf-8"),
        ("parse_html", result.ocr_result_json_path,
         "application/json; charset=utf-8"),
        ("parse_candidate_xlsx", result.candidate_xlsx_path,
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ]
    for role, local_path, content_type in artifact_specs:
        if not local_path:
            continue
        p = Path(local_path)
        if not p.exists():
            logger.warning("产物不存在，跳过: role=%s path=%s", role, local_path)
            continue
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        ext = p.suffix or ".bin"
        key = f"quota/{job.archive_id}/artifacts/{role}{ext}"
        store.put_object(PARSE_BUCKET_CANDIDATE, key, data, content_type=content_type)
        with get_session_factory()() as session:
            register_parse_artifact(
                session, job.archive_id, role,
                bucket=PARSE_BUCKET_CANDIDATE, key=key, sha256=sha,
                content_type=content_type, size=len(data),
            )
            session.commit()
        artifacts_meta.append({"kind": role, "key": key})
        logger.info("上传 %s key=%s size=%d", role, key, len(data))

    # 6. 更新 archive.parse_* 13 列
    candidate_key = f"quota/{job.archive_id}/artifacts/parse_candidate_xlsx.xlsx"
    # 复算 candidate sha256（pipeline result 已带）
    with get_session_factory()() as session:
        archive = session.get(Archive, job.archive_id)
        archive.parse_status = result.status if result.status != "failed" else "parsed"
        archive.parse_phase = "stage_a"
        archive.parse_parser_version = result.parser_version
        archive.parse_finished_at = datetime.now(UTC)
        archive.parse_metrics = {**(result.metrics or {}), "chunks_total": chunks_total,
                                "chunks_done": chunks_done,
                                "ocr_api_url": ocr_api_url}
        archive.parse_warnings = result.warnings or []
        archive.parse_error_code = None
        archive.parse_error_message = None
        archive.candidate_xlsx_key = candidate_key if any(
            a["kind"] == "parse_candidate_xlsx" for a in artifacts_meta
        ) else None
        session.commit()

    # 7. 写 manifest.json
    manifest = {
        "$schema": MANIFEST_SCHEMA,
        "task_id": job.job_id,
        "phase": "stage_a",
        "status": result.status,
        "parser_version": result.parser_version,
        "profile": job.profile,
        "province": province,
        "ocr_api_url": ocr_api_url,
        "source_pdf_sha256": source_sha,
        "candidate_xlsx_sha256": result.candidate_xlsx_sha256,
        "artifacts": artifacts_meta,
        "metrics": result.metrics or {},
        "warnings": result.warnings or [],
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, default=str).encode("utf-8")
    store.put_object(
        PARSE_BUCKET_REPORT,
        f"quota/{job.archive_id}/manifest.json",
        manifest_bytes,
        content_type="application/json; charset=utf-8",
    )

    # 8. 标 job done
    _mark_job_done(job.job_id, chunks_total=chunks_total, chunks_done=chunks_done)
    logger.info("real job done job_id=%s archive_id=%s status=%s",
                job.job_id, job.archive_id, result.status)


# ─────────────────────────────────────────────────────────
# Sweeper — 30/15 分钟兜底
# ─────────────────────────────────────────────────────────
def _run_sweeper() -> int:
    """扫超时 running job → 标 failed + archive.parse_status='failed_user'。"""
    from sqlalchemy import text

    from app.database import get_session_factory

    now = datetime.now(UTC)
    n_marked = 0
    with get_session_factory()() as session:
        rows = session.execute(text("""
            SELECT job_id, archive_id, started_at, last_heartbeat_at, chunks_done
            FROM quota_parse_job WHERE status='running'
        """)).all()
        for row in rows:
            job_id, archive_id, started_at, heartbeat, chunks_done = row
            timeout = (FIRST_CHUNK_TIMEOUT
                       if (chunks_done or 0) == 0 else SUBSEQUENT_CHUNK_TIMEOUT)
            ref = heartbeat or started_at
            if ref is None or (now - ref) < timeout:
                continue
            msg = f"sweeper: ref={ref.isoformat()} > {timeout}"
            session.execute(
                text("""UPDATE quota_parse_job
                        SET status='failed', error_code='parse_timeout',
                            error_message=:msg, finished_at=:now
                        WHERE job_id=:job_id"""),
                {"msg": msg, "now": now, "job_id": job_id},
            )
            session.execute(
                text("""UPDATE archive
                        SET parse_status='failed_user',
                            parse_error_code='parse_timeout',
                            parse_error_message=:msg,
                            parse_finished_at=:now
                        WHERE archive_id=:archive_id"""),
                {"msg": msg, "now": now, "archive_id": archive_id},
            )
            n_marked += 1
            logger.error("sweeper 标 job %s (archive %s) parse_timeout", job_id, archive_id)
        if n_marked:
            session.commit()
    return n_marked


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────
def _on_chunk_progress(job_id: str, i: int, total: int, status: str) -> None:
    """parse_chunked chunk 完成回调 → 推进 chunks_done + 刷心跳。

    - succeeded: chunks_done = i（前 i 段都完成）
    - failed:    chunks_done 不递增，但 last_heartbeat_at 必须刷（sweeper 兜底需要）

    用 _update_job_fields 一次性写两个字段（它总会写 last_heartbeat_at=now）。
    内部异常被吞掉,不让 heartbeat 写失败污染 pipeline。
    """
    new_chunks_done: int | None = i if status == "succeeded" else None
    try:
        _update_job_fields(job_id, chunks_done=new_chunks_done)
        logger.info("心跳写完成 job_id=%s chunk=%d/%d status=%s",
                    job_id, i, total, status)
    except Exception as e:
        logger.warning("心跳写失败 job_id=%s chunk=%d/%d status=%s: %s",
                       job_id, i, total, status, e)


def _update_job_fields(job_id: str, *, chunks_total: int | None = None,
                       chunks_done: int | None = None) -> None:
    from sqlalchemy import text

    from app.database import get_session_factory

    now = datetime.now(UTC)
    sets = ["last_heartbeat_at=:now"]
    params: dict[str, object] = {"now": now, "job_id": job_id}
    if chunks_total is not None:
        sets.append("chunks_total=:ct")
        params["ct"] = chunks_total
    if chunks_done is not None:
        sets.append("chunks_done=:cd")
        params["cd"] = chunks_done
    sql = f"UPDATE quota_parse_job SET {', '.join(sets)} WHERE job_id=:job_id"
    with get_session_factory()() as session:
        session.execute(text(sql), params)
        session.commit()


def _mark_job_done(job_id: str, *, chunks_total: int | None = None,
                   chunks_done: int | None = None) -> None:
    from sqlalchemy import text

    from app.database import get_session_factory

    sets = ["status='done'", "finished_at=:now"]
    params: dict[str, object] = {"now": datetime.now(UTC), "job_id": job_id}
    if chunks_total is not None:
        sets.append("chunks_total=:ct")
        params["ct"] = chunks_total
    if chunks_done is not None:
        sets.append("chunks_done=:cd")
        params["cd"] = chunks_done
    sql = f"UPDATE quota_parse_job SET {', '.join(sets)} WHERE job_id=:job_id"
    with get_session_factory()() as session:
        session.execute(text(sql), params)
        session.commit()


def _mark_job_failed(job, *, error_code: str, message: str) -> None:
    from sqlalchemy import text

    from app.database import get_session_factory

    now = datetime.now(UTC)
    with get_session_factory()() as session:
        session.execute(
            text("""UPDATE quota_parse_job
                    SET status='failed', error_code=:ec,
                        error_message=:msg, finished_at=:now
                    WHERE job_id=:job_id"""),
            {"ec": error_code, "msg": message, "now": now, "job_id": job.job_id},
        )
        session.execute(
            text("""UPDATE archive
                    SET parse_status='failed_user',
                        parse_error_code=:ec, parse_error_message=:msg,
                        parse_finished_at=:now
                    WHERE archive_id=:archive_id"""),
            {"ec": error_code, "msg": message, "now": now,
             "archive_id": job.archive_id},
        )
        session.commit()


def _read_pdf_page_count(path: Path) -> int:
    try:
        import fitz  # PyMuPDF
        with fitz.open(str(path)) as doc:
            return doc.page_count
    except Exception as e:
        logger.warning("读 PDF 页数失败 %s: %s", path, e)
        return 0


if __name__ == "__main__":
    sys.exit(main())