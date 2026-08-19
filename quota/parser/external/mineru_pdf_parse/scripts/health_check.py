"""
MinerU API 健康检查脚本（v0.2 函数化版本）。

函数级入口（Worker 使用）:
    from external.mineru_pdf_parse.scripts.health_check import health_check
    info = health_check(api_url="http://171.212.159.15:8000")
    if not info["ok"]:
        raise OcrUnavailableError(...)

CLI 入口（开发调试，本地人类使用）:
    python health_check.py --url http://171.212.159.15:8000

退出码（CLI）:
    0  → 健康，可以继续调用
    1  → API 不通
    2  → API 通但状态不是 healthy
    3  → API 通但 version 字段缺失（异常情况）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import requests


def check(url: str, timeout: float = 3.0) -> dict[str, Any]:
    """调 /health 端点，返回响应 dict。"""
    r = requests.get(f"{url.rstrip('/')}/health", timeout=timeout)
    r.raise_for_status()
    return r.json()


def health_check(
    api_url: str = "http://171.212.159.15:8000",
    timeout: float = 3.0,
) -> dict[str, Any]:
    """v0.2 函数级入口。

    返回 dict:
        {
            "ok": bool,                      # True=健康；False=异常
            "http_status": int,              # -1 表示请求未发出
            "version": str | None,
            "queued_tasks": int | None,
            "processing_tasks": int | None,
            "completed_tasks": int | None,
            "failed_tasks": int | None,
            "max_concurrent_requests": int | None,
            "error": str | None,             # 失败原因（成功为 None）
            "elapsed_ms": float,
        }

    不抛异常；所有失败信息放在 dict["ok"] / dict["error"]。
    Worker 层根据 info["ok"] 决定是否 raise OcrUnavailableError。
    """
    t0 = time.time()
    info: dict[str, Any] = {
        "ok": False,
        "http_status": -1,
        "version": None,
        "queued_tasks": None,
        "processing_tasks": None,
        "completed_tasks": None,
        "failed_tasks": None,
        "max_concurrent_requests": None,
        "error": None,
        "elapsed_ms": 0.0,
    }
    try:
        data = check(api_url, timeout=timeout)
    except requests.exceptions.ConnectionError as e:
        info["error"] = f"无法连接 MinerU API ({api_url}): {e}"
        info["elapsed_ms"] = (time.time() - t0) * 1000
        return info
    except requests.exceptions.Timeout:
        info["error"] = f"MinerU API 超时 ({timeout}s): {api_url}"
        info["elapsed_ms"] = (time.time() - t0) * 1000
        return info
    except requests.exceptions.HTTPError as e:
        info["http_status"] = e.response.status_code
        info["error"] = f"MinerU 返回 HTTP {e.response.status_code}: {e.response.text[:200]}"
        info["elapsed_ms"] = (time.time() - t0) * 1000
        return info
    except Exception as e:
        info["error"] = f"未知错误: {type(e).__name__}: {e}"
        info["elapsed_ms"] = (time.time() - t0) * 1000
        return info

    info["elapsed_ms"] = (time.time() - t0) * 1000
    info["http_status"] = 200
    info["version"] = data.get("version")
    info["queued_tasks"] = data.get("queued_tasks")
    info["processing_tasks"] = data.get("processing_tasks")
    info["completed_tasks"] = data.get("completed_tasks")
    info["failed_tasks"] = data.get("failed_tasks")
    info["max_concurrent_requests"] = data.get("max_concurrent_requests")

    status = data.get("status")
    if status != "healthy":
        info["error"] = f"API 返回非 healthy 状态: {status!r}; body={json.dumps(data, ensure_ascii=False)}"
        return info
    if not info["version"]:
        info["error"] = f"响应缺 version 字段: {data}"
        return info

    info["ok"] = True
    return info


def main():
    ap = argparse.ArgumentParser(description="检查本地 MinerU API 是否健康")
    ap.add_argument("--url", default="http://171.212.159.15:8000",
                    help="MinerU API base URL (default: http://171.212.159.15:8000)")
    ap.add_argument("--timeout", type=float, default=3.0,
                    help="HTTP 超时秒数 (default: 3)")
    args = ap.parse_args()

    info = health_check(api_url=args.url, timeout=args.timeout)

    if info["ok"]:
        print(f"✅ MinerU API 健康  ({info['elapsed_ms']:.0f}ms)")
        print(f"   url:     {args.url}")
        print(f"   version: {info['version']}")
        print(f"   tasks:   "
              f"queued={info['queued_tasks']!r} "
              f"processing={info['processing_tasks']!r} "
              f"completed={info['completed_tasks']!r} "
              f"failed={info['failed_tasks']!r}")
        print(f"   max_concurrent_requests: {info['max_concurrent_requests']!r}")
        sys.exit(0)

    print(f"❌ {info['error']}", file=sys.stderr)
    if "无法连接" in (info["error"] or ""):
        print("\n排查步骤：", file=sys.stderr)
        print("  1) 容器在跑吗？  docker ps | grep mineru", file=sys.stderr)
        print("  2) 端口映射对吗？  docker port PDF2Markdown", file=sys.stderr)
        print("  3) 容器内 API 进程在吗？", file=sys.stderr)
        print("     docker exec PDF2Markdown ps -ef | grep mineru-api", file=sys.stderr)
        print("  4) 如果 API 没跑，重启：", file=sys.stderr)
        print("     docker exec -d PDF2Markdown bash -c \\", file=sys.stderr)
        print("       'nohup mineru-api --host 0.0.0.0 --port 8000 \\", file=sys.stderr)
        print("        > /tmp/mineru-api.log 2>&1 &'", file=sys.stderr)
        sys.exit(1)
    if "version" in (info["error"] or ""):
        sys.exit(3)
    sys.exit(2)


if __name__ == "__main__":
    main()
