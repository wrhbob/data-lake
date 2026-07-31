"""quota_parser_sweeper — 独立进程扫孤儿 running job (v0.6 §#5)

设计：
  - 独立 Python 常驻进程（与 worker 同 .env, 同 DB）
  - fcntl flock 防运维双启（Windows 跳过）
  - 主循环每 60s 调一次 _run_sweeper() 复用 worker 的 SQL+UPDATE 逻辑
  - 不抢单, 不写 heartbeat, 只读 last_heartbeat_at 判定超时

启动：
  python -u quota/parser/quota_parser_sweeper.py
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path

# Windows GBK 兜底：与 worker 保持一致
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass

# ── 路径设置（与 worker 完全相同） ──
# this file = quota/parser/quota_parser_sweeper.py
#   ROOT       = data_lake0714/
#   file_asset_service/ 在 ROOT 下
#   quota/parser/ 在 ROOT 下
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "file_asset_service"))
sys.path.insert(0, str(ROOT / "quota" / "parser"))

# 复用 worker 的常量 + sweeper 函数体（worker 不再自己跑 sweeper）
from quota_parser_worker import (
    FIRST_CHUNK_TIMEOUT,
    SUBSEQUENT_CHUNK_TIMEOUT,
    SWEEPER_INTERVAL_SECONDS,
    _run_sweeper,
)

LOCKFILE = Path(os.environ.get(
    "QUOTA_PARSER_SWEEPER_LOCKFILE", "/tmp/quota_parser_sweeper.lock"
))

logger = logging.getLogger("quota_parser_sweeper")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.warning(
        "quota_parser_sweeper 启动 pid=%d host=%s thresholds=%s / %s interval=%ds",
        os.getpid(), socket.gethostname(),
        FIRST_CHUNK_TIMEOUT, SUBSEQUENT_CHUNK_TIMEOUT, SWEEPER_INTERVAL_SECONDS,
    )

    # fcntl flock — Windows 跳过（与 worker 一致）
    flock_fd = None
    if sys.platform != "win32":
        import fcntl as _fcntl
        try:
            flock_fd = open(LOCKFILE, "w")
            _fcntl.flock(flock_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            flock_fd.write(f"{os.getpid()}\n")
            flock_fd.flush()
            logger.info("acquired flock on %s", LOCKFILE)
        except (BlockingIOError, OSError) as e:
            logger.error("另一个 sweeper 已占 %s — 退出: %s", LOCKFILE, e)
            return 1
    else:
        logger.warning("Windows: 跳过 fcntl.flock（依赖运维不双启）")

    shutdown_requested = False

    def _on_sigterm(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.warning("收到 SIGTERM，准备退出 sweeper")

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    # 主循环：每 60s 调一次 _run_sweeper()
    while not shutdown_requested:
        try:
            n = _run_sweeper()
            if n:
                logger.warning("sweeper 标记 %d 个超时 job", n)
        except Exception as e:
            logger.exception("sweeper 主循环异常: %s", e)
        # 睡 SWEEPER_INTERVAL_SECONDS，但支持 SIGTERM 立即退出
        for _ in range(SWEEPER_INTERVAL_SECONDS):
            if shutdown_requested:
                break
            time.sleep(1)

    logger.warning("sweeper 退出 pid=%d", os.getpid())
    if flock_fd:
        flock_fd.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())