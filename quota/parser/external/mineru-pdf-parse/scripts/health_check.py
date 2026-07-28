"""
MinerU API 健康检查脚本。

用法:
    python health_check.py
    python health_check.py --url http://172.16.20.23:8000

退出码:
    0  → 健康，可以继续调用
    1  → API 不通
    2  → API 通但状态不是 healthy
    3  → API 通但 version 字段缺失（异常情况）
"""
import argparse
import json
import sys
import time

import requests


def check(url: str, timeout: float = 3.0) -> dict:
    """调 /health 端点，返回响应 dict。"""
    r = requests.get(f"{url.rstrip('/')}/health", timeout=timeout)
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser(description="检查本地 MinerU API 是否健康")
    ap.add_argument("--url", default="http://172.16.20.23:8000",
                    help="MinerU API base URL (default: http://172.16.20.23:8000)")
    ap.add_argument("--timeout", type=float, default=3.0,
                    help="HTTP 超时秒数 (default: 3)")
    args = ap.parse_args()

    t0 = time.time()
    try:
        data = check(args.url, timeout=args.timeout)
    except requests.exceptions.ConnectionError as e:
        print(f"❌ 无法连接到 {args.url}", file=sys.stderr)
        print(f"   原因: {e}", file=sys.stderr)
        print(f"\n排查步骤：", file=sys.stderr)
        print(f"  1) 容器在跑吗？  docker ps | grep mineru", file=sys.stderr)
        print(f"  2) 端口映射对吗？  docker port PDF2Markdown", file=sys.stderr)
        print(f"  3) 容器内 API 进程在吗？", file=sys.stderr)
        print(f"     docker exec PDF2Markdown ps -ef | grep mineru-api", file=sys.stderr)
        print(f"  4) 如果 API 没跑，重启：", file=sys.stderr)
        print(f"     docker exec -d PDF2Markdown bash -c \\", file=sys.stderr)
        print(f"       'nohup mineru-api --host 0.0.0.0 --port 8000 \\", file=sys.stderr)
        print(f"        > /tmp/mineru-api.log 2>&1 &'", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"❌ {args.url}/health 超时 ({args.timeout}s)", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"❌ {args.url}/health 返回 HTTP {e.response.status_code}", file=sys.stderr)
        print(f"   body: {e.response.text[:200]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ 未知错误: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    elapsed_ms = (time.time() - t0) * 1000

    # 状态判断
    status = data.get("status", "missing")
    if status != "healthy":
        print(f"❌ API 返回非 healthy 状态: {status!r}", file=sys.stderr)
        print(f"   完整响应: {json.dumps(data, ensure_ascii=False)}", file=sys.stderr)
        sys.exit(2)

    version = data.get("version")
    if not version:
        print(f"❌ 响应缺 version 字段: {data}", file=sys.stderr)
        sys.exit(3)

    print(f"✅ MinerU API 健康  ({elapsed_ms:.0f}ms)")
    print(f"   url:     {args.url}")
    print(f"   version: {version}")
    print(f"   tasks:   "
          f"queued={data.get('queued_tasks', '?')} "
          f"processing={data.get('processing_tasks', '?')} "
          f"completed={data.get('completed_tasks', '?')} "
          f"failed={data.get('failed_tasks', '?')}")
    print(f"   max_concurrent_requests: {data.get('max_concurrent_requests', '?')}")
    sys.exit(0)


if __name__ == "__main__":
    main()