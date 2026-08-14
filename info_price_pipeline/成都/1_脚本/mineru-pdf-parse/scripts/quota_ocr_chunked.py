#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quota_ocr_chunked.py — PDF → MD 分段 OCR（不杀进程，段间静默 10s）

设计动机（来自用户 2026-07-24 反馈迭代）:
  v1 (放弃): 214 页 PDF 88MB 单段跑 sync /file_parse 触发**系统 RAM OOM**
    → docker stats 显示内存从 0 涨到 ~17.6/18 GB → 容器内 mineru-api 主进程被
      OOM kill，VLLM::EngineCore 变 orphan，客户端 RemoteDisconnected
  v2 (放弃): 每段后杀 mineru-api + VLLM 重启 fresh
    → 用户实测发现**杀 API 会导致爆显存**：杀掉进程 → VLLM 半连接状态被破坏
      → 下一次处理时显存使用暴涨
  v3 (现行): **不杀进程，段间静默 10s**
    → sleep 期间依赖 MinerU 内部 Python GC 释放上一段的 middle_json / content_list
    → 依赖 VLLM 自动释放 KV cache（上一段推理结束后的空闲期）
    → 段间不 poll API（避免打断清理），也不调任何"清理端点"（截图中没有 DELETE）

解决的关键问题:
  1. **每段 ≤ --chunk-size 页（默认 100）**：单段内存峰值压到 ~10GB 容器上限以下
  2. **段间 sleep 10s**：给 GC 留时间，不动进程

流程:
    PDF (214p) → pdf_page_extract 切 [1-100], [101-200], [201-214]
                 ↓ health check  ↓ health check        ↓ health check
                 quota_ocr       quota_ocr              quota_ocr
                 (sync /file_parse, /file_parse,      /file_parse,
                  2400s timeout)  2400s timeout)        2400s timeout)
                 ↓ .md           ↓ .md                 ↓ .md
                 sleep 10s       sleep 10s              (最后一段不 sleep)
                 → 合并 → <stem>.md

用法:
    python quota_ocr_chunked.py <pdf> [-o OUTPUT_DIR]
    python quota_ocr_chunked.py <pdf> --chunk-size 80

输出（默认在 PDF 同目录）:
    <stem>.md     ← 合并后的完整 MD（按段顺序拼接）
    chunks/       ← 可选中间产物目录，每段 .md 留底（不删，方便调试）

退出码:
    0  → 成功
    1  → PDF 不存在
    2  → pdf_page_extract 抽页失败
    3  → quota_ocr 跑段失败
    4  → 合并失败
    10 → 等 health_check ready 超时
    20 → 杀 mineru-api 失败（容器内 pkill 出错）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import fitz  # PyMuPDF 取总页数
import requests


HERE = Path(__file__).resolve().parent
# HERE = .../定额解析/.claude/skills/mineru-pdf-parse/scripts/
# parents 倒序:
#   parents[0] = mineru-pdf-parse/
#   parents[1] = skills/
#   parents[2] = .claude/
#   parents[3] = 定额解析/  ← PROJECT_ROOT
PROJECT_ROOT = HERE.parents[3]
PDF_EXTRACT_SCRIPT = PROJECT_ROOT / ".claude" / "skills" / "pdf-page-extract" / "pdf_page_extract.py"
QUOTA_OCR_SCRIPT = HERE / "quota_ocr.py"

CONTAINER_NAME = "PDF2Markdown"
MINERU_LOG = "/tmp/mineru-api.log"
HEALTH_URL = "http://172.16.20.23:8000/health"
HEALTH_MAX_WAIT = 240  # 含 VLLM preload 的冷启动最长 4 分钟
DEFAULT_CHUNK_SIZE = 100

# Windows GBK 不打印 emoji
E_OK = "[OK]"
E_ERR = "[ERROR]"
E_WARN = "[WARN]"


def pdf_total_pages(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def plan_chunks(total: int, chunk_size: int) -> list[tuple[int, int]]:
    """把 [1, total] 分成 N 段。例: 214/100 → [(1,100),(101,200),(201,214)]."""
    if chunk_size < 1:
        raise ValueError(f"chunk_size 必须 ≥ 1, got {chunk_size}")
    chunks = []
    start = 1
    while start <= total:
        end = min(start + chunk_size - 1, total)
        chunks.append((start, end))
        start = end + 1
    return chunks


def run_subprocess(cmd: list[str], *, timeout: int = 1800, label: str = "") -> subprocess.CompletedProcess:
    """跑子进程，把 stdout/stderr 实时打，捕获 tail 备查。

    必须 PYTHONIOENCODING=utf-8，否则子进程 Python 脚本在 Windows GBK 下会抛 UnicodeError。
    """
    print(f"$ {' '.join(str(c) for c in cmd[:6])}{'...' if len(cmd) > 6 else ''}",
          flush=True)
    env = {**__import__('os').environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=env,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        print(f"{E_ERR} {label} returncode={r.returncode}", file=sys.stderr)
        if r.stdout:
            print(f"  stdout tail: {r.stdout[-400:]}", file=sys.stderr)
        if r.stderr:
            print(f"  stderr tail: {r.stderr[-400:]}", file=sys.stderr)
    return r


def extract_chunk(pdf: Path, pages: tuple[int, int], out_pdf: Path) -> None:
    """调 pdf_page_extract.py 抽该段。"""
    pages_spec = f"{pages[0]}-{pages[1]}"
    cmd = [sys.executable, str(PDF_EXTRACT_SCRIPT), str(pdf), pages_spec, "-o", str(out_pdf)]
    r = run_subprocess(cmd, timeout=120, label=f"pdf_page_extract {pages_spec}")
    if r.returncode != 0:
        raise RuntimeError(f"pdf_page_extract {pages_spec} failed (rc={r.returncode})")
    if not out_pdf.exists() or out_pdf.stat().st_size == 0:
        raise RuntimeError(f"pdf_page_extract 未生成有效文件: {out_pdf}")


def kill_and_restart_mineru_api() -> None:
    """兜底用：杀 mineru-api + VLLM 重启。

    ⚠ 仅在 /health 不通时调用，不在常规段间清理时调用（避免爆显存）。
    正常段间只用 sleep(10)，不动进程。
    """
    kill_cmd = [
        "docker", "exec", CONTAINER_NAME, "bash", "-c",
        "pkill -9 -f mineru-api 2>/dev/null; "
        "pkill -9 -f 'VLLM::EngineCore' 2>/dev/null; "
        "pkill -9 -f vllm 2>/dev/null; "
        "sleep 2; "
        "pkill -9 -f mineru-api 2>/dev/null; "
        "pkill -9 -f 'VLLM::EngineCore' 2>/dev/null; "
        "pkill -9 -f vllm 2>/dev/null; "
        "true"
    ]
    r = subprocess.run(kill_cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f"{E_WARN} pkill 退出码 {r.returncode}, stderr: {r.stderr[:200]}",
              file=sys.stderr)

    start_cmd = [
        "docker", "exec", "-d", CONTAINER_NAME, "bash", "-c",
        f"nohup mineru-api --host 0.0.0.0 --port 8000 --enable-vlm-preload true "
        f"> {MINERU_LOG} 2>&1 &"
    ]
    r = subprocess.run(start_cmd, capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError(f"启 mineru-api 失败: rc={r.returncode}, stderr={r.stderr[:300]}")


def wait_health_ready(max_wait: int = HEALTH_MAX_WAIT) -> bool:
    """轮询 /health，直到 status==healthy 或超时。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            r = requests.get(HEALTH_URL, timeout=3)
            if r.status_code == 200:
                data = r.json()
                status = data.get("status", "?")
                if status == "healthy":
                    elapsed = time.time() - t0
                    print(f"{E_OK} /health ready ({elapsed:.1f}s)")
                    return True
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout):
            pass
        except Exception as e:
            print(f"{E_WARN} health check 异常: {type(e).__name__}: {e}",
                  file=sys.stderr)
        time.sleep(5)
    return False


CHUNK_BREATH_SECS = 10  # 每段完成后静默 10s（给 MinerU in-process state + VLLM KV cache GC 时间）


def ensure_mineru_api_alive() -> None:
    """每段前的健康检查。

    原则（用户 2026-07-24 反馈）:
      - **绝不主动杀 mineru-api/VLLM**——杀 API 会导致爆显存（VLLM 半连接状态被破坏）
      - 段间只靠 sleep(10) 让 GC 自然清理
      - 仅在 /health 真不通时**兜底杀重启**一次
      - 重启后 VLLM 模型权重要 重新加载（80s+），仅在 API 真挂时承担此成本

    设计: 第一次跑假设 API 已经手工启好（用户责任）；脚本开始时只 health-check，
    不 healthy 才兜底重启。
    """
    def _check_healthy() -> bool:
        try:
            r = requests.get(HEALTH_URL, timeout=3)
            return r.status_code == 200 and r.json().get("status") == "healthy"
        except Exception:
            return False

    if _check_healthy():
        print(f"{E_OK} /health healthy（沿用现有进程）")
        return

    print(f"{E_WARN} /health 不通, 兜底杀重启...")
    kill_and_restart_mineru_api()
    print(f"{E_OK} mineru-api 已重启, 等 ready (最长 {HEALTH_MAX_WAIT}s)...")
    if not wait_health_ready():
        raise RuntimeError(f"等 health_check ready 超时 ({HEALTH_MAX_WAIT}s)")


def rest_between_chunks() -> None:
    """段间静默（用户硬约束: 不杀进程, 只 sleep）。

    给 MinerU 内部 in-process state 自然清理 + VLLM KV cache 释放留时间。
    """
    print(f"[.] 段间静默 {CHUNK_BREATH_SECS}s（不杀进程, 等 GC 自然清理）...")
    time.sleep(CHUNK_BREATH_SECS)


def call_quota_ocr(chunk_pdf: Path, chunk_out_dir: Path, label: str) -> Path:
    """调 quota_ocr.py 跑该段，产物落到 chunk_out_dir。返回 .md 路径。"""
    cmd = [sys.executable, str(QUOTA_OCR_SCRIPT), str(chunk_pdf), "-o", str(chunk_out_dir)]
    r = run_subprocess(cmd, timeout=2400, label=f"quota_ocr {label}")  # 40 min/段兜底
    if r.returncode != 0:
        raise RuntimeError(f"quota_ocr {label} 失败 (rc={r.returncode})")

    # quota_ocr 把 zip 解压到 chunk_out_dir，产物文件名 = upload.md（因为 cp 到 upload.pdf）
    md = chunk_out_dir / "upload.md"
    if not md.exists():
        # 兜底：找目录下唯一的 .md
        mds = list(chunk_out_dir.glob("*.md"))
        if not mds:
            raise RuntimeError(f"段 {label}: 没找到 .md 产物，目录: {chunk_out_dir}")
        md = mds[0]
    return md


def merge_chunks_md(chunk_mds: list[Path], output_md: Path) -> None:
    """按段顺序拼接 .md，中间加注释方便人工定位段边界."""
    if not chunk_mds:
        raise RuntimeError("没有可合并的 chunk .md")
    parts: list[str] = []
    for i, md_path in enumerate(chunk_mds, start=1):
        content = md_path.read_text(encoding="utf-8")
        # 加页范围注释（仅方便人工看，不影响后续 extract_quota）
        # 段范围从 chunk_dir 名字里推
        chunk_dir = md_path.parent
        marker = f"<!-- chunk {chunk_dir.name} -->"
        parts.append(f"{marker}\n{content}")
    output_md.write_text("\n\n".join(parts), encoding="utf-8")
    print(f"{E_OK} 合并 → {output_md}  ({output_md.stat().st_size:,} bytes)")


def main():
    ap = argparse.ArgumentParser(
        description="PDF → MD 分段 OCR（每段 fresh mineru-api，避开 RAM OOM + in-process state bug）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("pdf_path", help="源 PDF 绝对路径")
    ap.add_argument("-o", "--output-dir", default=None,
                    help="产物落盘目录（默认 = PDF 同目录）")
    ap.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                    help=f"每段页数（默认 {DEFAULT_CHUNK_SIZE}）")
    ap.add_argument("--keep-chunks", action="store_true",
                    help="保留 segments/ 中间产物目录（默认删）")
    args = ap.parse_args()

    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.exists() or not pdf_path.is_file():
        print(f"{E_ERR} PDF 不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = (Path(args.output_dir).resolve()
                  if args.output_dir else pdf_path.parent)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. 算分段 ──
    total = pdf_total_pages(pdf_path)
    chunks = plan_chunks(total, args.chunk_size)
    print(f"{E_OK} PDF: {pdf_path.name}")
    print(f"{E_OK} 总页数: {total}")
    print(f"{E_OK} 分段 (chunk_size={args.chunk_size}): {chunks}")
    print(f"{E_OK} 段数: {len(chunks)}")
    print(f"{E_OK} 输出目录: {output_dir}")
    print()

    # ── 2. 临时目录 ──
    temp_dir = Path(tempfile.mkdtemp(prefix="quota_ocr_chunked_"))
    print(f"{E_OK} 临时目录: {temp_dir}")
    print()

    chunk_md_paths: list[Path] = []

    try:
        for i, pages in enumerate(chunks, start=1):
            label = f"p{pages[0]:03d}-p{pages[1]:03d}"
            print(f"═══════════════════════════════════════════════════════════")
            print(f"  段 {i}/{len(chunks)}:  pages {pages[0]}-{pages[1]}  ({label})")
            print(f"═══════════════════════════════════════════════════════════")

            chunk_root = temp_dir / f"chunk_{i:03d}_{label}"
            chunk_pdf = chunk_root / f"chunk_{i:03d}.pdf"
            chunk_out = chunk_root / "ocr_out"
            chunk_root.mkdir(parents=True)
            chunk_out.mkdir(parents=True)

            # [1] 抽 PDF
            print(f"[1/4] 抽 PDF: pages {pages[0]}-{pages[1]}")
            extract_chunk(pdf_path, pages, chunk_pdf)

            # [2] 健康检查（用户硬约束: 不主动杀, 仅兜底）
            print(f"[2/4] health-check（不杀进程，挂了才兜底重启）")
            ensure_mineru_api_alive()

            # [3] 跑 quota_ocr
            print(f"[3/4] 调 quota_ocr.py 跑该段（同步 /file_parse, 超时 2400s）")
            md = call_quota_ocr(chunk_pdf, chunk_out, label)
            chunk_md_paths.append(md)
            print(f"{E_OK} 段 {i} md: {md}  ({md.stat().st_size:,} bytes)")

            # [4] 段间静默 10s（用户硬约束: 不杀进程, sleep 自然 GC）
            if i < len(chunks):
                print(f"[4/4] 段 {i} 完成, 静默 {CHUNK_BREATH_SECS}s 后开始段 {i+1}")
                rest_between_chunks()
            else:
                print(f"[4/4] 段 {i} 完成, 这是最后一段")
            print()

        # ── 3. 合并 MD ──
        print(f"═══════════════════════════════════════════════════════════")
        print(f"  合并 {len(chunk_md_paths)} 段 MD")
        print(f"═══════════════════════════════════════════════════════════")
        final_md = output_dir / f"{pdf_path.stem}.md"
        merge_chunks_md(chunk_md_paths, final_md)

        # ── 4. 保留 segments 目录（可选用） ──
        if args.keep_chunks:
            target = output_dir / f"{pdf_path.stem}_chunks"
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(temp_dir, target)
            print(f"{E_OK} 中间产物保留: {target}")

        print()
        print(f"{'═' * 60}")
        print(f"{E_OK} DONE: {len(chunks)} 段全部完成 → {final_md}")
        print(f"{'═' * 60}")
        sys.exit(0)

    except subprocess.TimeoutExpired as e:
        print(f"{E_ERR} 子进程超时: {e}", file=sys.stderr)
        sys.exit(3)
    except RuntimeError as e:
        print(f"{E_ERR} {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"{E_ERR} 未预期错误: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(20)
    finally:
        if not args.keep_chunks:
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"{E_OK} 清理临时目录: {temp_dir}")


if __name__ == "__main__":
    main()