"""
MinerU PDF 串行批处理。

⚠️ 严格串行，绝不并行 —— 12GB 显卡扛不住 2 个并发请求。

用法:
    python parse_all.py <dir_or_pdf> [<dir_or_pdf> ...]
    python parse_all.py <dir>            # 递归扫整个目录的 PDF
    python parse_all.py --pattern "*.pdf" <dir>

行为:
  - 健康检查 1 次（开头）
  - 对每个 PDF 串行调 parse_pdf.py
  - 每个 PDF 处理完后 sleep 5s（让 vLLM worker 完全释放显存）
  - 中途失败不打断剩余任务，最后打印汇总报告
  - 任何时刻监控 `processing_tasks`：>0 时强制等

参数透传给 parse_pdf.py:
  --output-base <dir>   把所有 PDF 的输出集中到一个大目录下（默认各自同名文件夹）
  --no-render           只生成 JSON
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

DEFAULT_API = os.environ.get("MINERU_API_URL", "http://171.212.159.15:8000")
SCRIPT_DIR = Path(__file__).parent
PARSE_PDF = SCRIPT_DIR / "parse_pdf.py"
HEALTH_CHECK = SCRIPT_DIR / "health_check.py"

INTER_PDF_SLEEP_S = 5  # 每个 PDF 处理完后等几秒，让 vLLM worker 释放显存


def health_check(api_url: str) -> bool:
    """调 health_check.py。返回 True/False，不 raise。"""
    r = subprocess.run([sys.executable, str(HEALTH_CHECK), "--url", api_url])
    return r.returncode == 0


def wait_until_idle(api_url: str, timeout_s: int = 600) -> bool:
    """等 API 的 processing_tasks 回到 0。"""
    print(f"⏳ 等 processing_tasks 归零（最多 {timeout_s}s）...")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{api_url}/health", timeout=3).json()
            proc = r.get("processing_tasks", 0)
            queue = r.get("queued_tasks", 0)
            print(f"   [{time.strftime('%H:%M:%S')}] processing={proc} queued={queue}")
            if proc == 0 and queue == 0:
                return True
        except Exception as e:
            print(f"   health check 失败: {e}")
        time.sleep(10)
    print(f"❌ 超时 {timeout_s}s，仍有处理中的任务")
    return False


def collect_pdfs(paths: list[str], pattern: str) -> list[Path]:
    """从文件/目录列表收集所有 PDF。"""
    out: list[Path] = []
    for p in paths:
        pp = Path(p).resolve()
        if pp.is_file():
            if pp.suffix.lower() == ".pdf":
                out.append(pp)
            else:
                print(f"⚠ 跳过非 PDF: {pp}")
        elif pp.is_dir():
            out.extend(sorted(pp.rglob(pattern)))
        else:
            print(f"⚠ 不存在: {pp}")
    # 去重
    seen = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def parse_one(pdf: Path, args, idx: int, total: int) -> bool:
    """处理单个 PDF。返回 True 成功 / False 失败。"""
    print()
    print("=" * 70)
    print(f"📄 [{idx}/{total}] {pdf.name}")
    print(f"   size: {pdf.stat().st_size:,} bytes")
    print(f"   path: {pdf}")
    print("=" * 70)

    # 等显存彻底释放（vLLM worker 退出）
    if not wait_until_idle(args.api_url, timeout_s=120):
        print(f"❌ GPU 未空闲，跳过 {pdf.name}")
        return False

    cmd = [sys.executable, str(PARSE_PDF), str(pdf), "--api-url", args.api_url]
    if args.no_render:
        cmd.append("--no-render")
    elif args.output_base:
        out_dir = Path(args.output_base).resolve() / pdf.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend([str(out_dir)])

    print(f"🚀 {' '.join(cmd)}")
    t0 = time.time()
    r = subprocess.run(cmd)
    elapsed = time.time() - t0

    success = (r.returncode == 0)
    icon = "✅" if success else "❌"
    print(f"{icon} [{idx}/{total}] {pdf.name}  ({elapsed:.1f}s)")

    # 中间休息，确保显存释放
    if idx < total:  # 不是最后一个
        print(f"💤 等待 {INTER_PDF_SLEEP_S}s 让显存释放...")
        time.sleep(INTER_PDF_SLEEP_S)

    return success


def main():
    ap = argparse.ArgumentParser(
        description="MinerU 串行批处理 PDF（严禁并行，12GB 显卡会 OOM）")
    ap.add_argument("paths", nargs="+",
                    help="PDF 文件或目录（可多个）")
    ap.add_argument("--pattern", default="*.pdf",
                    help="目录扫描时的 glob pattern (default: *.pdf)")
    ap.add_argument("--api-url", default=DEFAULT_API,
                    help=f"MinerU API 地址 (default: {DEFAULT_API})")
    ap.add_argument("--output-base", default=None,
                    help="把所有 PDF 的输出集中到一个大目录下（默认各自同名文件夹）")
    ap.add_argument("--no-render", action="store_true",
                    help="只生成 JSON")
    args = ap.parse_args()

    # 0. 一次健康检查
    print("🔍 健康检查...")
    if not health_check(args.api_url):
        sys.exit(1)

    # 1. 收集 PDF
    pdfs = collect_pdfs(args.paths, args.pattern)
    if not pdfs:
        print("❌ 没找到任何 PDF")
        sys.exit(1)
    print(f"\n📚 找到 {len(pdfs)} 个 PDF，将串行处理")

    # 2. 串行处理
    results = []
    t_start = time.time()
    for i, pdf in enumerate(pdfs, 1):
        ok = parse_one(pdf, args, i, len(pdfs))
        results.append((pdf.name, ok))

    # 3. 汇总
    total_time = time.time() - t_start
    print()
    print("=" * 70)
    print("📊 批处理汇总")
    print("=" * 70)
    succ = sum(1 for _, ok in results if ok)
    fail = len(results) - succ
    for name, ok in results:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}")
    print()
    print(f"成功: {succ} / {fail} 失败")
    print(f"总耗时: {total_time/60:.1f} 分钟")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()