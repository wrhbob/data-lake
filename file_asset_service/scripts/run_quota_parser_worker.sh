#!/usr/bin/env bash
# quota_parser_worker 启动脚本 — nohup 长期运行
#
# 用法：
#   bash scripts/run_quota_parser_worker.sh           # 启动（写 pid 到 logs/worker.pid）
#   bash scripts/run_quota_parser_worker.sh stop      # 停
#   bash scripts/run_quota_parser_worker.sh status    # 看状态
#   bash scripts/run_quota_parser_worker.sh restart   # 重启
#
# 环境变量：
#   QUOTA_PARSE_MOCK=1       走 mock 路径（默认 0 = real）
#   ALLOW_REAL_PARSE=1       real 模式开关（real 模式必须显式设，防误跑真 OCR）
#   LOG_LEVEL                默认 INFO

set -e

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

# 加载 .env（必须，否则 DATABASE_URL/MINIO 配置都没）
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi

mkdir -p "$ROOT/logs"
PIDFILE="$ROOT/logs/worker.pid"

stop_worker() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "[stop] killing pid=$PID"
      kill "$PID" 2>/dev/null || true
      for _ in 1 2 3 4 5; do
        sleep 1
        if ! kill -0 "$PID" 2>/dev/null; then
          echo "[stop] worker pid=$PID exited"
          rm -f "$PIDFILE"
          return 0
        fi
      done
      echo "[stop] worker pid=$PID not responding, sending KILL"
      kill -9 "$PID" 2>/dev/null || true
      rm -f "$PIDFILE"
    else
      echo "[stop] pidfile stale, removing"
      rm -f "$PIDFILE"
    fi
  else
    echo "[stop] no pidfile at $PIDFILE"
  fi
}

case "${1:-start}" in
  stop)
    stop_worker
    exit 0
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "[status] running pid=$(cat "$PIDFILE")"
    else
      echo "[status] not running"
      [ -f "$PIDFILE" ] && echo "  (stale pidfile: $(cat "$PIDFILE"))"
    fi
    exit 0
    ;;
  restart)
    stop_worker
    sleep 1
    ;&  # fallthrough to start
  start|"")
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "[start] worker already running pid=$(cat "$PIDFILE")"
      exit 0
    fi
    TS=$(date +%Y%m%d_%H%M%S)
    LOG="$ROOT/logs/worker.log.$TS"
    echo "[start] launching worker → $LOG"

    # Windows / Git Bash nohup 等价：直接 & 让进程脱离父 shell
    # v0.8: 加 PYTHONUTF8=1 — 强制 Python 启用 utf-8 mode, 避免 str ↔ bytes 默认按 mbcs (GBK)
    # 编码导致 parse_warnings 等含中文 JSON 字段写到 PG jsonb 变乱码.
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 \
      /d/miniconda3/envs/file-asset/python.exe -X utf8 -u "$ROOT/quota/parser/quota_parser_worker.py" \
      >> "$LOG" 2>&1 &
    WORKER_PID=$!
    echo "$WORKER_PID" > "$PIDFILE"
    echo "[start] worker pid=$WORKER_PID log=$LOG"

    # 等 3s 看是否存活
    sleep 3
    if ! kill -0 "$WORKER_PID" 2>/dev/null; then
      echo "[start] worker 启动失败,看日志: $LOG"
      tail -20 "$LOG"
      exit 1
    fi
    echo "[start] OK (pid=$WORKER_PID alive)"
    ;;
  *)
    echo "用法: $0 {start|stop|status|restart}" >&2
    exit 1
    ;;
esac