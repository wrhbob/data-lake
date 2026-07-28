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
from typing import Any

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


def poll_task(task_id: str, api_url: str, label: str, poll_interval: int = 15):
    """轮询直到任务完成/失败。返回最终 info dict。"""
    url = f"{api_url}/tasks/{task_id}"
    t0 = time.time()
    while True:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            info = r.json()
        except Exception as e:
            print(f"  ⚠ poll error: {e}")
            time.sleep(poll_interval)
            continue

        status = info.get("status")
        elapsed = int(time.time() - t0)
        print(f"  [{label}] {elapsed:>5d}s  status={status}")
        if status in ("completed", "failed"):
            return info
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
) -> dict[str, Any]:
    """Worker 调用入口。>100 页 PDF 自动分段。

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
        try:
            t0 = time.time()
            task_id = submit_task(chunk_pdf, api_url, backend, effort)
            print(f"  task_id: {task_id}  ({time.time()-t0:.1f}s)")

            info = poll_task(task_id, api_url, f"chunk {i}/{chunk_count}",
                             poll_interval=poll_interval_s)
            if info.get("status") != "completed":
                chunk_error = info.get("error")
                print(f"  ❌ 失败: {chunk_error}")
                continue

            result_path = chunk_pdf.parent / "result.json"
            fetch_result(task_id, api_url, result_path)
            print(f"  💾 {result_path.name}  ({result_path.stat().st_size:,} bytes)")
            result_paths.append(result_path)

            render_chunk(result_path, chunk_pdf, render_script)
            chunk_status = "succeeded"

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
