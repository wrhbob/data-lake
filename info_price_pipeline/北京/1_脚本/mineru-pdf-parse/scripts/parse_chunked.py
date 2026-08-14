"""
MinerU 分段串行解析（避免大 PDF 让 uvicorn OOM）。

用法:
    python parse_chunked.py <pdf_path> [--chunk-size 100] [--backend hybrid-engine] [--effort high]

为什么分段:
  - 容器系统 RAM ~15 GB，uvicorn 处理大 PDF（>300 页）会触发 OOM killer
  - 拆成 ~100 页/段，单次请求小，uvicorn 内存峰值低
  - 中间产物保留：每个 chunk 单独保存 result.json/.md/.html
  - 任何一段失败，之前的都保留，不丢全部

流程:
  1. 用 pdf-page-extract skill 拆 PDF 成 N 段（每段默认 100 页）
  2. 每段串行调 /tasks 异步端点（HTTP 短连接，uvicorn 不积累 buffer）
  3. 每段单独保存到 <pdf_stem>/chunks/chunk_NNN/
  4. 最后合并：所有 chunk 的 md_content 拼成总 .md（按页内顺序）

参数:
  <pdf_path>      必填，单个 PDF
  --chunk-size    每段页数（默认 100）。最后一段可能更小。
  --backend       hybrid-engine / vlm-engine / pipeline（默认 hybrid-engine）
  --effort        high / medium（默认 high；仅 hybrid-engine 生效）
  --no-merge      只产出分块结果，不合并成总 .md/.html
  --api-url       MinerU API 地址（默认 http://172.16.20.23:8000）

输出结构:
  <pdf_dir>/<pdf_stem>/
  ├── chunks/
  │   ├── chunk_001_pages_001-100/
  │   │   ├── chunk_001_pages_001-100.pdf
  │   │   ├── result.json
  │   │   ├── chunk_001_pages_001-100.md
  │   │   └── chunk_001_pages_001-100.html
  │   ├── chunk_002_pages_101-200/
  │   │   └── ...
  │   └── chunk_005_pages_401-436/
  │       └── ...
  ├── <pdf_stem>.md         ← 合并后的总 markdown
  └── <pdf_stem>.html       ← 合并后的总 html 预览
"""
import argparse
import gc
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

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


def split_pdf(pdf: Path, chunk_size: int, out_dir: Path) -> list[Path]:
    """拆 PDF 成 chunk_size 页/段，返回每段 PDF 路径列表。"""
    # 2026-08-13: 拆段前清理残留 chunk（上次失败残留导致 Permission denied）
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    total = doc.page_count
    doc.close()

    chunks = []
    n_chunks = (total + chunk_size - 1) // chunk_size
    print(f"📖 源 PDF: {pdf.name}  ({total} 页)")
    print(f"📐 拆成 {n_chunks} 段（每段 ≤{chunk_size} 页）")

    for i in range(n_chunks):
        start = i * chunk_size + 1   # 1-indexed
        end = min((i + 1) * chunk_size, total)
        pages_spec = f"{start}-{end}" if start != end else f"{start}"

        chunk_pdf_dir = out_dir / f"chunk_{i+1:03d}_pages_{start:03d}-{end:03d}"
        chunk_pdf_dir.mkdir(parents=True, exist_ok=True)
        chunk_pdf = chunk_pdf_dir / f"chunk_{i+1:03d}_pages_{start:03d}-{end:03d}.pdf"

        # 用 pdf-page-extract skill 拆
        cmd = [
            sys.executable, str(EXTRACT_SCRIPT),
            str(pdf), pages_spec,
            "-o", str(chunk_pdf),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
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
    last_pct = -1
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
        # 用 progress 字段（如果有）
        # 注意：MinerU 的 task schema 不一定有 progress 字段，先 status-only
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
    merged_md = []
    merged_content_list = []
    merged_middle_pages = []
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

    # 写合并后的 .md（最简形式：直接拼 md_content）
    md_path = out_dir / f"{pdf.stem}.md"
    md_path.write_text("\n\n".join(merged_md), encoding="utf-8")
    print(f"  ✅ {md_path}  ({md_path.stat().st_size:,} chars)")

    # 写合并 result.json（包含 md_content + 简化版 middle/content_list）
    merged = {
        "task_id": merged_task_id,
        "status": "completed",
        "backend": "merged-chunks",
        "version": "1.0",
        "file_names": [pdf.name],
        "results": {
            "upload": {
                "md_content": "\n\n".join(merged_md),
                "content_list": json.dumps(merged_content_list, ensure_ascii=False),
                "middle_json": json.dumps({"pdf_info": merged_middle_pages}, ensure_ascii=False),
            }
        }
    }
    merged_result = out_dir / "result.json"
    merged_result.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ {merged_result}  ({merged_result.stat().st_size:,} bytes)")


def main():
    ap = argparse.ArgumentParser(
        description="MinerU 分段串行解析（避免大 PDF OOM）")
    ap.add_argument("pdf_path", help="PDF 文件绝对路径")
    ap.add_argument("--output-dir", default=None,
                    help="产物落盘目录（默认 = PDF 同目录的同名文件夹；阶段 0 走流程根/OCR中间/<stem>_OCR中间/）")
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
    chunks_dir = out_dir / "chunks"
    out_dir.mkdir(parents=True, exist_ok=True)

    render_script = SCRIPT_DIR / "render.py"

    # 1. 拆 PDF
    chunks = split_pdf(pdf, args.chunk_size, chunks_dir)

    # 2. 串行处理每段
    result_paths = []
    for i, chunk_pdf in enumerate(chunks, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(chunks)}] {chunk_pdf.name}  ({chunk_pdf.stat().st_size:,} bytes)")
        print('='*60)

        try:
            # 提交
            t0 = time.time()
            task_id = submit_task(chunk_pdf, args.api_url, args.backend, args.effort)
            print(f"  task_id: {task_id}  ({time.time()-t0:.1f}s)")

            # 轮询
            info = poll_task(task_id, args.api_url, f"chunk {i}/{len(chunks)}")
            if info.get("status") != "completed":
                print(f"  ❌ 失败: {info.get('error')}")
                continue

            # 取结果
            result_path = chunk_pdf.parent / "result.json"
            fetch_result(task_id, args.api_url, result_path)
            print(f"  💾 {result_path.name}  ({result_path.stat().st_size:,} bytes)")
            result_paths.append(result_path)

            # 渲染单 chunk
            render_chunk(result_path, chunk_pdf, render_script)

            # 中间休息（让 vLLM worker 完全退出）
            if i < len(chunks):
                print(f"  💤 等待 5s 让显存释放...")
                time.sleep(5)

        except Exception as e:
            print(f"  ❌ chunk {i} 异常: {type(e).__name__}: {e}")
            continue

    # 3. 合并
    if not args.no_merge and result_paths:
        merge_chunks(chunks, result_paths, pdf, out_dir)
        # 渲染合并的 .md
        merged_result = out_dir / "result.json"
        render_chunk(merged_result, pdf, render_script)

    # 4. 汇总
    print(f"\n{'='*60}")
    print(f"✅ 完成 {len(result_paths)}/{len(chunks)} 段")
    print(f"📂 产物: {out_dir}")
    print('='*60)
    sys.exit(0 if len(result_paths) == len(chunks) else 1)


if __name__ == "__main__":
    main()