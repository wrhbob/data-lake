"""quota_parser workspace 清理（v0.3 新增）

策略（与 quota/README.md §12.G、web-frontend SPEC §3.1.4 一致）：
- 任务成功：立即删除本地 work_root（产物已上传 MinIO，不再需要本地副本）
- 任务失败：保留 N 天供 debug，写 .failed_at 时间戳标记
- cron：cleanup_expired_jobs() 按 retention_days 删除过期失败 job
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import (
    DEFAULT_FAILURE_RETENTION_DAYS,
    get_failure_retention_days,
)

FAILED_MARKER = ".failed_at"


def _read_failed_at(marker: Path) -> float | None:
    """读 .failed_at 时间戳；缺失或格式错误返回 None。"""
    if not marker.exists():
        return None
    try:
        return float(marker.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def cleanup_workspace(work_root: Path, *, success: bool) -> None:
    """任务结束后清理 workspace。

    Args:
        work_root: 任务专属工作目录（<get_work_root()>/<task_id>/）。
                   一般由 run_quota_pipeline / finalize_reviewed_xlsx 创建。
        success: True=任务成功，删除整个 work_root；
                 False=任务失败，写 .failed_at 标记后保留 N 天。

    Side effects:
        - success=True：调用 shutil.rmtree(work_root, ignore_errors=True)
        - success=False：写 work_root/.failed_at = time.time()
                         （重复调用会刷新时间戳 = 再续 N 天）

    异常：该函数不抛错；任何清理失败仅影响本地磁盘，不应阻塞任务状态写 DB。
    """
    if not work_root.exists():
        return
    try:
        if success:
            shutil.rmtree(work_root, ignore_errors=True)
            return
        marker = work_root / FAILED_MARKER
        marker.write_text(f"{time.time():.6f}\n", encoding="utf-8")
    except OSError:
        # 清理失败不应上抛——任务结果已在 DB / MinIO 落地；
        # 本地磁盘残留由下次 cron 兜底。
        pass


def cleanup_expired_jobs(work_root: Path, *, retention_days: int | None = None) -> dict:
    """清理过期的失败 job workspace（cron / 运维手动调用）。

    Args:
        work_root: worker 任务根目录（一般 = get_work_root()）。
        retention_days: 失败 job 保留天数；None 用 config.get_failure_retention_days()
                        （env QUOTA_PARSER_FAILURE_RETENTION_DAYS，default 7）。

    Returns:
        dict: {
            "scanned": int,         # 扫描到的子目录数
            "expired_removed": int, # 实际清理数
            "active_skipped": int,  # 无 .failed_at 标记的目录数（可能是正在跑）
            "retention_days": int,
        }

    行为：
        - 只清理有 .failed_at 标记的目录
        - 无标记的目录一律不动（可能是正在跑 / 刚创建 / 已被手动挪走）
        - 时间戳格式错误也跳过（运维手动删 .failed_at 后保留目录）
    """
    if retention_days is None:
        retention_days = get_failure_retention_days()
    if retention_days < 0:
        retention_days = DEFAULT_FAILURE_RETENTION_DAYS

    result = {"scanned": 0, "expired_removed": 0, "active_skipped": 0, "retention_days": retention_days}
    if not work_root.exists():
        return result

    cutoff = time.time() - retention_days * 86400.0
    for entry in work_root.iterdir():
        if not entry.is_dir():
            continue
        result["scanned"] += 1
        failed_at = _read_failed_at(entry / FAILED_MARKER)
        if failed_at is None:
            result["active_skipped"] += 1
            continue
        if failed_at < cutoff:
            try:
                shutil.rmtree(entry, ignore_errors=True)
                result["expired_removed"] += 1
            except OSError:
                # 单个删除失败不阻塞整体
                pass
    return result


__all__ = ["cleanup_workspace", "cleanup_expired_jobs"]