#!/usr/bin/env bash
# quota_parser_sweeper 启动脚本 — nohup 长期运行（v0.6 §#5）
#
# 用法：
#   bash scripts/run_quota_parser_sweeper.sh           # 启动（写 pid 到 logs/sweeper.pid）
#   bash scripts/run_quota_parser_sweeper.sh stop      # 停
#   bash scripts/run_quota_parser_sweeper.sh status    # 看状态
#   bash scripts/run_quota_parser_sweeper.sh restart   # 重启
#
# 环境变量：
#   LOG_LEVEL                默认 INFO
#   QUOTA_PARSER_SWEEPER_LOCKFILE   默认 /tmp/quota_parser_sweeper.lock
#
# 运维 SOP：
#   "先启 sweeper，再启 worker" — sweeper 在 worker 之前活着，
#   才能吸收 worker 启动 / 重启期间的孤儿 running job。

set -e

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

# 加载 .env（必须，否则 DATABASE_URL/MINIO 配置都没）
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi

mkdir -p "$ROOT/logs"
PIDFILE="$ROOT/logs/sweeper.pid"

stop_sweeper() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "[stop] killing pid=$PID"
      kill "$PID" 2>/dev/null || true
      for _ in 1 2 3 4 5; do
        sleep 1
        if ! kill -0 "$PID" 2>/dev/null; then
          echo "[stop] sweeper pid=$PID exited"
          rm -f "$PIDFILE"
          return 0
        fi
      done
      echo "[stop] sweeper pid=$PID not responding, sending KILL"
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
    stop_sweeper
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
    stop_sweeper
    sleep 1
    ;&  # fallthrough to start
  start|"")
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "[start] sweeper already running pid=$(cat "$PIDFILE")"
      exit 0
    fi
    TS=$(date +%Y%m%d_%H%M%S)
    LOG="$ROOT/logs/sweeper.log.$TS"
    echo "[start] launching sweeper → $LOG"

    # Windows / Git Bash nohup 等价：直接 & 让进程脱离父 shell
    /d/miniconda3/envs/file-asset/python.exe -u "$ROOT/quota/parser/quota_parser_sweeper.py" \
      >> "$LOG" 2>&1 &
    SWEEPER_PID=$!
    echo "$SWEEPER_PID" > "$PIDFILE"
    echo "[start] sweeper pid=$SWEEPER_PID log=$LOG"

    # 等 3s 看是否存活
    sleep 3
    if ! kill -0 "$SWEEPER_PID" 2>/dev/null; then
      echo "[start] sweeper 启动失败,看日志: $LOG"
      tail -20 "$LOG"
      exit 1
    fi
    echo "[start] OK (pid=$SWEEPER_PID alive)"
    ;;
  *)
    echo "用法: $0 {start|stop|status|restart}" >&2
    exit 1
    ;;
esac