"""
MinerU 分段串行解析（避免大 PDF 让 uvicorn OOM）（v0.2 函数化版本）。

函数级入口（Worker 使用）:
    from external.mineru_pdf_parse.scripts.parse_chunked import parse_chunked
    info = parse_chunked(
        pdf_path="D:/.../big.pdf",
        output_dir="D:/.../ocr/",
        chunk_size=100,
    )

    返回 dict:
        {
            "markdown_path": str | None,
            "html_path": str | None,
            "result_json_path": str | None,
            "chunks": [
                {"index": 1, "pages": [1, 100], "status": "succeeded", "seconds": float},
                ...
            ],
            "seconds": float,
        }

    失败抛 RuntimeError，Worker 自行映射到 OcrUnavailableError。

CLI 入口（开发调试）:
    python parse_chunked.py <pdf_path> [--chunk-size 100] [--api-url ...]
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import fitz          # PyMuPDF
import requests


SCRIPT_DIR = Path(__file__).parent
DEFAULT_API = os.environ.get("MINERU_API_URL", "http://172.16.20.23:8000")
EXTRACT_SCRIPT = SCRIPT_DIR.parent.parent / "pdf-page-extract" / "pdf_page_extract.py"


def wait_until_idle(api_url: str, timeout_s: int = 300, poll_s: int = 10) -> bool:
    """调 /health 等 processing_tasks 归零（确认服务端该任务 worker 已退出、显存已释放）。"""
    print(f"⏳ 等服务端 processing_tasks=0 (超时 {timeout_s}s)...")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{api_url.rstrip('/')}/health", timeout=5).json()
            proc = r.get("processing_tasks", 0)
            queue = r.get("queued_tasks", 0)
            queued_only = r.get("queued_tasks", 0)  # 老版本字段
            pending = proc + queue + queued_only
            if pending == 0:
                print(f"   ✅ idle (proc={proc} queue={queue})")
                return True
            print(f"   [{time.strftime('%H:%M:%S')}] proc={proc} queue={queue} — wait...")
        except Exception as e:
            print(f"   health error: {e}")
        time.sleep(poll_s)
    print(f"   ❌ 超时 {timeout_s}s 未归零")
    return False


def client_cleanup(*objs):
    """显式删引用 + 触发 GC，让客户端 Python 进程尽快释放大对象。"""
    for o in objs:
        try:
            del o
        except NameError:
            pass
    gc.collect()


def split_pdf(pdf: Path, chunk_size: int, out_dir: Path,
              extract_script_path: Path | None = None) -> list[Path]:
    """拆 PDF 成 chunk_size 页/段，返回每段 PDF 路径列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    total = doc.page_count
    doc.close()

    chunks = []
    n_chunks = (total + chunk_size - 1) // chunk_size
    print(f"📖 源 PDF: {pdf.name}  ({total} 页)")
    print(f"📐 拆成 {n_chunks} 段（每段 ≤{chunk_size} 页）")

    extract_script = Path(extract_script_path) if extract_script_path else EXTRACT_SCRIPT
    if not extract_script.exists():
        raise RuntimeError(f"拆段脚本不存在: {extract_script}")

    for i in range(n_chunks):
        start = i * chunk_size + 1   # 1-indexed
        end = min((i + 1) * chunk_size, total)
        pages_spec = f"{start}-{end}" if start != end else f"{start}"

        chunk_pdf_dir = out_dir / f"chunk_{i+1:03d}_pages_{start:03d}-{end:03d}"
        chunk_pdf_dir.mkdir(parents=True, exist_ok=True)
        chunk_pdf = chunk_pdf_dir / f"chunk_{i+1:03d}_pages_{start:03d}-{end:03d}.pdf"

        # 用 pdf-page-extract skill 拆
        cmd = [
            sys.executable, str(extract_script),
            str(pdf), pages_spec,
            "-o", str(chunk_pdf),
        ]
        # v0.2 patch: 子进程在 Windows 下若没强制 utf-8 会按 GBK 输出
        # 撞 utf-8 解码就 UnicodeDecodeError 整段崩。两层兜底：
        #   1. env 强 PYTHONIOENCODING / PYTHONUTF8 让子进程输出 utf-8
        #   2. errors='replace' 兜底异常字节（v0.1 行为对非出错路径零变更）
        _sub_env = os.environ.copy()
        _sub_env.setdefault("PYTHONIOENCODING", "utf-8")
        _sub_env.setdefault("PYTHONUTF8", "1")
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=_sub_env,
        )
        if r.returncode != 0:
            raise RuntimeError(f"拆段失败 (chunk {i+1}):\n{r.stderr}")

        print(f"  [{i+1}/{n_chunks}] pages {start}-{end} → {chunk_pdf.name}")
        chunks.append(chunk_pdf)

    return chunks


def submit_task(pdf: Path, api_url: str, backend: str, effort: str) -> str:
    """提交异步任务，返回 task_id。"""
    url = f"{api_url.rstrip('/')}/tasks"
    data = {
        "backend": backend,
        "effort": effort,
        "parse_method": "auto",
        "return_md": "true",
        "return_middle_json": "true",
        "return_content_list": "true",
        "formula_enable": "true",
        "table_enable": "true",
        "image_analysis": "true",
        "start_page_id": "0",
        "end_page_id": "99999",
    }
    with open(pdf, "rb") as f:
        files = {"files": (pdf.name, f, "application/pdf")}
        r = requests.post(url, files=files, data=data, timeout=300)
    r.raise_for_status()
    return r.json()["task_id"]


class PollTaskLost(Exception):
    """task 在 minerU 端连续 N 次 404，视为 task 已丢失（minerU OOM / 容器重启 / 后端 bug）。

    v0.7 引入 — 病因未知，先在客户端阻断死循环，由 caller（worker）捕获后标 failed_permanent。
    """


class PollTaskTimeout(Exception):
    """poll 总时长超过阈值，放弃等待。"""


def poll_task(
    task_id: str,
    api_url: str,
    label: str,
    poll_interval: int = 15,
    max_consecutive_404: int = 3,
    max_total_seconds: int = 1800,
):
    """轮询直到任务完成/失败。

    v0.7 防死循环（症状层）：
      - 连续 max_consecutive_404 次 HTTP 404 → raise PollTaskLost
        （默认 15s × 3 = 45s 内 3 次 404,视为 task 在 minerU 端不存在）
      - 总耗时超过 max_total_seconds → raise PollTaskTimeout
        （默认 1800s = 30min,对齐 sweeper 阈值）
      - 网络瞬断（ConnectionError / Timeout）/ 其他 HTTPError（5xx）不计入 404 计数,只 print + continue
    返回最终 info dict。
    """
    url = f"{api_url}/tasks/{task_id}"
    t0 = time.time()
    consecutive_404 = 0
    while True:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            info = r.json()
            consecutive_404 = 0  # 成功拿到响应 → 重置计数
        except requests.exceptions.HTTPError as e:
            # 只对 404 计数 + 持续判断;其他 HTTPError（5xx）按瞬断处理
            if getattr(r, "status_code", None) == 404:
                consecutive_404 += 1
                print(f"  ⚠ poll 404 ({consecutive_404}/{max_consecutive_404}): {e}")
                if consecutive_404 >= max_consecutive_404:
                    raise PollTaskLost(
                        f"task {task_id} 在 minerU 端连续 {max_consecutive_404} 次 404,"
                        f"视为 task 丢失（病因未知,可能是 minerU OOM / 容器重启 / 后端 bug）"
                    )
            else:
                print(f"  ⚠ poll HTTPError {getattr(r, 'status_code', '?')}: {e}")
            time.sleep(poll_interval)
            continue
        except Exception as e:
            # 网络瞬断 / JSON 解析失败 → 不计入 404
            print(f"  ⚠ poll error: {e}")
            time.sleep(poll_interval)
            continue

        status = info.get("status")
        elapsed = int(time.time() - t0)
        print(f"  [{label}] {elapsed:>5d}s  status={status}")
        if status in ("completed", "failed"):
            return info
        if elapsed > max_total_seconds:
            raise PollTaskTimeout(
                f"task {task_id} poll 超时 {max_total_seconds}s 仍未完成"
            )
        time.sleep(poll_interval)


def fetch_result(task_id: str, api_url: str, out_path: Path) -> dict:
    """取结果并保存，返回 result dict。"""
    url = f"{api_url}/tasks/{task_id}/result"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    out_path.write_bytes(r.content)
    return json.loads(r.content)


def render_chunk(result_path: Path, chunk_pdf: Path, render_script: Path):
    """调 render.py 渲染单个 chunk。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        [sys.executable, str(render_script), str(result_path), str(chunk_pdf)],
        env=env, capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ⚠ render 失败: {r.stderr[:200]}")
    else:
        print(f"  ✅ rendered")


# v0.8 chunk 粒度 PollTaskLost 重试参数
# 设计: PollTaskLost 是 minerU 端瞬时不可用 (OOM/重启) 触发的客户端异常,
#       大概率下一次 submit/poll 就会恢复,但若立即重试会撞同一波故障。
#       实证: b2107a91 chunk 1 fail → 后续 chunks 仍能成,说明仅 chunk 1 受影响。
# 决策: 默认 2 次重试 + 30s 间隔 (≈ 矿工U container 从 OOM kill 到 K8s 重启完成的时间窗)。
#       仅 PollTaskLost 重试,其他 Exception (网络瞬断/5xx) 走原 except 立即 continue。
CHUNK_POLL_LOST_RETRIES = 2
CHUNK_POLL_LOST_RETRY_INTERVAL_S = 30


def _process_chunk_once(
    *,
    chunk_pdf: Path,
    chunk_index: int,
    chunk_count: int,
    api_url: str,
    backend: str,
    effort: str,
    render_script: Path,
    poll_interval_s: int,
    result_paths: list[Path],
) -> None:
    """单次 chunk 全流程: submit → poll → fetch → render.

    任何一步失败 (含 PollTaskLost/PollTaskTimeout/网络错误/poll status != completed)
    都向上 raise,由外层决定是否重试或终止该 chunk。
    """
    t0 = time.time()
    task_id = submit_task(chunk_pdf, api_url, backend, effort)
    print(f"  task_id: {task_id}  ({time.time()-t0:.1f}s)")

    info = poll_task(task_id, api_url, f"chunk {chunk_index}/{chunk_count}",
                     poll_interval=poll_interval_s)
    if info.get("status") != "completed":
        err = info.get("error") or "unknown"
        raise RuntimeError(f"chunk {chunk_index} minerU 端 status={info.get('status')!r}: {err}")

    result_path = chunk_pdf.parent / "result.json"
    fetch_result(task_id, api_url, result_path)
    print(f"  💾 {result_path.name}  ({result_path.stat().st_size:,} bytes)")
    result_paths.append(result_path)

    render_chunk(result_path, chunk_pdf, render_script)


def merge_chunks(chunks: list[Path], result_paths: list[Path], pdf: Path, out_dir: Path):
    """合并所有 chunk 的 md_content 成总 .md，并触发合并 render。"""
    print(f"\n📦 合并 {len(result_paths)} 个 chunk ...")
    merged_md: list[str] = []
    merged_content_list: list[Any] = []
    merged_middle_pages: list[Any] = []
    merged_task_id = "merged-" + "-".join(p.parent.name for p in chunks[:3])

    for chunk_pdf, result_path in zip(chunks, result_paths):
        data = json.loads(result_path.read_text(encoding="utf-8"))
        result_key = next(iter(data["results"]))
        content = data["results"][result_key]

        merged_md.append(content["md_content"])
        try:
            merged_content_list.extend(json.loads(content["content_list"]))
        except Exception:
            pass
        try:
            merged_middle_pages.extend(json.loads(content["middle_json"]).get("pdf_info", []))
        except Exception:
            pass

    md_path = out_dir / f"{pdf.stem}.md"
    md_path.write_text("\n\n".join(merged_md), encoding="utf-8")
    print(f"  ✅ {md_path}  ({md_path.stat().st_size:,} chars)")

    merged = {
        "task_id": merged_task_id,
        "status": "completed",
        "backend": "merged-chunks",
        "version": "1.0",
        "file_names": [pdf.name],
        "results": {
            pdf.name: {
                "md_content": "\n\n".join(merged_md),
                "content_list": json.dumps(merged_content_list, ensure_ascii=False),
                "middle_json": json.dumps({"pdf_info": merged_middle_pages}, ensure_ascii=False),
            }
        }
    }
    merged_result = out_dir / "result.json"
    merged_result.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ {merged_result}  ({merged_result.stat().st_size:,} bytes)")


# ─────────────────────────────────────────────────────
# v0.2 函数级入口
# ─────────────────────────────────────────────────────
def parse_chunked(
    *,
    pdf_path: str,
    output_dir: str,
    chunk_size: int = 100,
    api_url: str = DEFAULT_API,
    backend: str = "hybrid-engine",
    effort: str = "high",
    no_merge: bool = False,
    extract_script_path: str | None = None,
    idle_timeout_s: int = 300,
    poll_interval_s: int = 15,
    wait_between_chunks_s: int = 5,
    on_chunk_done: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Worker 调用入口。>100 页 PDF 自动分段。

    Args:
        on_chunk_done: 可选回调,签名 on_chunk_done(chunk_index, chunk_count, status) -> None.
            - chunk_index: 1-based
            - chunk_count: 总段数
            - status: "succeeded" | "failed"
            回调内异常被 parse_chunked 捕获并 print,不影响 OCR 主流程。
            None = 旧行为（不调）— 向后兼容所有现有调用方。

    返回 dict:
        {
            "markdown_path": str | None,
            "html_path": str | None,
            "result_json_path": str | None,
            "chunks": [...],
            "seconds": float,
            "all_succeeded": bool,
        }
    """
    pdf = Path(pdf_path).resolve()
    if not pdf.exists():
        raise RuntimeError(f"PDF 不存在: {pdf}")

    out_dir = Path(output_dir).resolve()
    chunks_dir = out_dir / "chunks"
    out_dir.mkdir(parents=True, exist_ok=True)

    render_script = SCRIPT_DIR / "render.py"
    if not render_script.exists():
        raise RuntimeError(f"render.py 不存在: {render_script}")

    started = time.time()

    # 1. 拆 PDF
    chunks = split_pdf(
        pdf, chunk_size, chunks_dir,
        extract_script_path=Path(extract_script_path) if extract_script_path else None,
    )

    # 2. 串行处理每段
    result_paths: list[Path] = []
    chunk_metrics: list[dict[str, Any]] = []
    chunk_count = len(chunks)
    for i, chunk_pdf in enumerate(chunks, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{chunk_count}] {chunk_pdf.name}  ({chunk_pdf.stat().st_size:,} bytes)")
        print('='*60)

        chunk_started = time.time()
        chunk_status = "failed"
        chunk_error = None
        # v0.8 PollTaskLost 重试状态: 重试期间保留 exception 对象到下一轮 try,
        # 让 except 只在外层 except 处理 PollTaskLost;其他 Exception 维持原行为。
        try:
            for attempt in range(1, CHUNK_POLL_LOST_RETRIES + 2):  # 1 + retries
                try:
                    _process_chunk_once(
                        chunk_pdf=chunk_pdf,
                        chunk_index=i,
                        chunk_count=chunk_count,
                        api_url=api_url,
                        backend=backend,
                        effort=effort,
                        render_script=render_script,
                        poll_interval_s=poll_interval_s,
                        result_paths=result_paths,
                    )
                    chunk_status = "succeeded"
                    break  # 成功 — 跳出重试循环
                except PollTaskLost as e:
                    if attempt <= CHUNK_POLL_LOST_RETRIES:
                        print(
                            f"  ⚠ chunk {i} PollTaskLost 第 {attempt}/{CHUNK_POLL_LOST_RETRIES} 次, "
                            f"等 {CHUNK_POLL_LOST_RETRY_INTERVAL_S}s 后重试..."
                        )
                        time.sleep(CHUNK_POLL_LOST_RETRY_INTERVAL_S)
                        continue
                    # 重试用完仍未恢复 — 视为永久失败,抛给外层 except
                    raise
                # 其他 Exception (网络瞬断/5xx/PollTaskTimeout/RuntimeError) 不重试,直接抛

            # v0.6 §#6: chunk 粒度回调由 finally 块统一处理（成功后 chunk_status="succeeded"）
            # 这里不再单独调用,避免与 finally 块形成双调用导致 chunks_done 被冗余写两次。
            # F10 修复: 2026-08-03 fanggu 任务暴露的 on_chunk_done 双调用问题。

            if i < chunk_count:
                print(f"  💤 等待 {wait_between_chunks_s}s 让显存释放...")
                time.sleep(wait_between_chunks_s)

        except Exception as e:
            chunk_error = f"{type(e).__name__}: {e}"
            print(f"  ❌ chunk {i} 异常: {chunk_error}")
            continue
        finally:
            chunk_metrics.append({
                "index": i,
                "pages": [int(chunk_pdf.name.split("_pages_")[1].split("-")[0]),
                          int(chunk_pdf.name.split("_pages_")[1].split("-")[1].split(".")[0])],
                "status": chunk_status,
                "seconds": time.time() - chunk_started,
                "error": chunk_error,
            })
            # v0.6 §#6: 失败 chunk 也写一次心跳（chunks_done 不递增，但 heartbeat 刷）
            if on_chunk_done is not None:
                try:
                    on_chunk_done(i, chunk_count, chunk_status)
                except Exception as _cb_err:
                    print(f"  ⚠ on_chunk_done 回调异常 (chunk {i} {chunk_status}): {_cb_err}")

    # 3. 合并
    md_path: Path | None = None
    html_path: Path | None = None
    merged_result_path: Path | None = None
    if not no_merge and result_paths:
        merge_chunks(chunks, result_paths, pdf, out_dir)
        merged_result_path = out_dir / "result.json"
        if merged_result_path.exists():
            render_chunk(merged_result_path, pdf, render_script)
        md_path_candidate = out_dir / f"{pdf.stem}.md"
        md_path = md_path_candidate if md_path_candidate.exists() else None
        html_path_candidate = out_dir / f"{pdf.stem}.html"
        html_path = html_path_candidate if html_path_candidate.exists() else None

    return {
        "markdown_path": str(md_path) if md_path else None,
        "html_path": str(html_path) if html_path else None,
        "result_json_path": str(merged_result_path) if merged_result_path else None,
        "chunks": chunk_metrics,
        "seconds": time.time() - started,
        "all_succeeded": len(result_paths) == chunk_count,
    }


def main():
    ap = argparse.ArgumentParser(
        description="MinerU 分段串行解析（避免大 PDF OOM）")
    ap.add_argument("pdf_path", help="PDF 文件绝对路径")
    ap.add_argument("--output-dir", default=None,
                    help="产物落盘目录")
    ap.add_argument("--chunk-size", type=int, default=100,
                    help="每段页数（默认 100）")
    ap.add_argument("--backend", default="hybrid-engine",
                    choices=["hybrid-engine", "vlm-engine", "pipeline"])
    ap.add_argument("--effort", default="high", choices=["high", "medium"])
    ap.add_argument("--api-url", default=DEFAULT_API)
    ap.add_argument("--no-merge", action="store_true",
                    help="只产分块结果，不合并")
    args = ap.parse_args()

    pdf = Path(args.pdf_path).resolve()
    if not pdf.exists():
        sys.exit(f"❌ PDF 不存在: {pdf}")

    out_dir = (Path(args.output_dir).resolve()
              if args.output_dir else pdf.parent / pdf.stem)

    info = parse_chunked(
        pdf_path=str(pdf),
        output_dir=str(out_dir),
        chunk_size=args.chunk_size,
        api_url=args.api_url,
        backend=args.backend,
        effort=args.effort,
        no_merge=args.no_merge,
    )

    print(f"\n{'='*60}")
    print(f"✅ 完成 {sum(1 for c in info['chunks'] if c['status']=='succeeded')}/{len(info['chunks'])} 段")
    print(f"📂 产物: {out_dir}")
    print('='*60)
    sys.exit(0 if info["all_succeeded"] else 1)


if __name__ == "__main__":
    main()
