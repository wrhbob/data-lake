"""
MinerU PDF 解析主入口（v0.2 函数化版本）。

函数级入口（Worker 使用）:
    from external.mineru_pdf_parse.scripts.parse_pdf import parse_pdf
    info = parse_pdf(
        pdf_path="D:/.../sample.pdf",
        output_dir="D:/.../ocr/",
    )

    返回:
        {
            "markdown_path": str | None,
            "html_path": str | None,
            "result_json_path": str,
            "page_count": int,
            "seconds": float,
        }

    失败 raise OcrUnavailableError / OcrTransientError。
    业务异常详见 exceptions.py（v0.2 阶段抛 RuntimeError 占位，
       Worker 自行 catch 后映射到 OcrUnavailableError）。

CLI 入口（开发调试）:
    python parse_pdf.py <pdf_path> [output_dir]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests


DEFAULT_API = os.environ.get("MINERU_API_URL", "http://172.16.20.23:8000")
UPLOAD_NAME = "upload.pdf"  # 永远用 ASCII 文件名上传，规避 double-encode
DEFAULT_BACKEND = "hybrid-engine"
DEFAULT_EFFORT = "high"


def health_check_inline(api_url: str) -> None:
    """内部 /health 探针；不通 raise SystemExit，保留原 SKILL.md 风格。"""
    try:
        r = requests.get(f"{api_url.rstrip('/')}/health", timeout=3)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.ConnectionError as e:
        raise SystemExit(
            f"❌ 无法连接 MinerU API: {api_url}\n"
            f"   原因: {e}\n\n"
            f"排查步骤：\n"
            f"  1) 容器在跑吗？  docker ps | grep mineru\n"
            f"  2) 端口映射对吗？  docker port PDF2Markdown\n"
            f"  3) 容器内 API 进程在吗？\n"
            f"     docker exec PDF2Markdown ps -ef | grep mineru-api\n"
            f"  4) 如果 API 没跑，重启：\n"
            f"     docker exec -d PDF2Markdown bash -c \\\n"
            f"       'nohup mineru-api --host 0.0.0.0 --port 8000 \\\n"
            f"        > /tmp/mineru-api.log 2>&1 &'"
        )
    except Exception as e:
        raise SystemExit(f"❌ API 健康检查失败: {type(e).__name__}: {e}")

    status = data.get("status")
    if status != "healthy":
        raise SystemExit(
            f"❌ API 返回非 healthy 状态: {status!r}\n"
            f"   响应: {data}"
        )

    print(f"✅ MinerU API 健康 (v{data.get('version', '?')})")


def upload_and_parse(pdf_path: Path, out_dir: Path, api_url: str) -> Path:
    """上传 PDF + 调 /file_parse，返回 result.json 路径。"""
    upload_path = out_dir / UPLOAD_NAME
    shutil.copy2(pdf_path, upload_path)
    print(f"📋 已拷贝: {pdf_path} → {upload_path}  ({upload_path.stat().st_size:,} bytes)")

    url = f"{api_url.rstrip('/')}/file_parse"
    data = {
        "backend": DEFAULT_BACKEND,
        "effort": DEFAULT_EFFORT,
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

    print(f"🚀 调 /file_parse (backend={DEFAULT_BACKEND}, effort={DEFAULT_EFFORT}) ...")
    print(f"   提示：首次调用模型加载约 3-5 分钟，后续 ~1-2 秒/页")
    t0 = time.time()
    with open(upload_path, "rb") as f:
        files = {"files": (UPLOAD_NAME, f, "application/pdf")}
        r = requests.post(url, files=files, data=data, timeout=1800)
    elapsed = time.time() - t0

    print(f"✅ HTTP {r.status_code} ({len(r.content):,} bytes, {elapsed:.1f}s)")
    r.raise_for_status()

    result_path = out_dir / "result.json"
    result_path.write_bytes(r.content)
    print(f"💾 saved: {result_path}")
    return result_path


def render(result_path: Path, pdf_path: Path) -> None:
    """调 render.py 生成 .md + .html。"""
    render_script = Path(__file__).parent / "render.py"
    if not render_script.exists():
        raise SystemExit(f"❌ 找不到 {render_script}")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    cmd = [sys.executable, str(render_script), str(result_path), str(pdf_path)]
    print(f"🎨 调 render.py: {' '.join(cmd)}")
    r = subprocess.run(cmd, env=env)
    if r.returncode != 0:
        raise SystemExit(f"❌ render.py 退出码 {r.returncode}")


def cleanup(out_dir: Path, keep_upload: bool = False) -> None:
    """删中间文件 upload.pdf。"""
    p = out_dir / UPLOAD_NAME
    if p.exists() and not keep_upload:
        p.unlink()
        print(f"🧹 清理中间文件: {p.name}")


# ─────────────────────────────────────────────────────
# v0.2 函数级入口
# ─────────────────────────────────────────────────────
def parse_pdf(
    *,
    pdf_path: str,
    output_dir: str,
    api_url: str = DEFAULT_API,
    keep_upload: bool = False,
    no_render: bool = False,
    skip_health_check: bool = False,
) -> dict[str, Any]:
    """Worker 调用入口。

    返回 dict（同上）。失败抛 RuntimeError，子类型由调用方映射到
    OcrUnavailableError / OcrTransientError。
    """
    pdf_p = Path(pdf_path).resolve()
    if not pdf_p.exists():
        raise RuntimeError(f"PDF 不存在: {pdf_p}")
    if not pdf_p.is_file():
        raise RuntimeError(f"不是文件: {pdf_p}")
    if pdf_p.suffix.lower() != ".pdf":
        raise RuntimeError(f"不是 .pdf 文件: {pdf_p} (suffix={pdf_p.suffix})")

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()

    # 1. 健康检查（可选；Worker 启动时已调用过，可省）
    if not skip_health_check:
        health_check_inline(api_url)

    # 2. 上传 + 解析
    try:
        result_path = upload_and_parse(pdf_p, out_dir, api_url)
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"OCR HTTP 错误 {e.response.status_code}: {e.response.text[:200]}")
    except requests.exceptions.Timeout:
        raise RuntimeError(f"OCR 调用超时")
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"OCR 连接失败: {e}")

    md_path: Path | None = None
    html_path: Path | None = None
    if not no_render:
        render(result_path, pdf_p)
        md_path = out_dir / f"{pdf_p.stem}.md"
        html_path = out_dir / f"{pdf_p.stem}.html"
        if not md_path.exists():
            md_path = None
        if not html_path.exists():
            html_path = None

    cleanup(out_dir, keep_upload=keep_upload)

    page_count = 0
    try:
        import fitz  # type: ignore
        with fitz.open(pdf_p) as doc:
            page_count = doc.page_count
    except Exception:
        page_count = 0

    return {
        "markdown_path": str(md_path) if md_path else None,
        "html_path": str(html_path) if html_path else None,
        "result_json_path": str(result_path),
        "page_count": page_count,
        "seconds": time.time() - started,
    }


def main():
    ap = argparse.ArgumentParser(
        description="用本地 MinerU 服务解析 PDF → JSON + Markdown + HTML")
    ap.add_argument("pdf_path", help="PDF 文件绝对路径")
    ap.add_argument("output_dir", nargs="?", default=None,
                    help="输出目录（默认 = 源 PDF 同位置同名文件夹）")
    ap.add_argument("--api-url", default=DEFAULT_API,
                    help=f"MinerU API 地址 (default: {DEFAULT_API}, 可用 env MINERU_API_URL)")
    ap.add_argument("--keep-upload", action="store_true",
                    help="保留中间 upload.pdf（调试用）")
    ap.add_argument("--no-render", action="store_true",
                    help="只调 MinerU，不渲染 .md/.html")
    args = ap.parse_args()

    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.exists():
        raise SystemExit(f"❌ 找不到 PDF: {pdf_path}")

    out_dir = (Path(args.output_dir).resolve()
              if args.output_dir else pdf_path.parent / pdf_path.stem)

    info = parse_pdf(
        pdf_path=str(pdf_path),
        output_dir=str(out_dir),
        api_url=args.api_url,
        keep_upload=args.keep_upload,
        no_render=args.no_render,
    )

    print()
    print("=" * 60)
    print(f"✨ 完成！产物：")
    for f in sorted(out_dir.iterdir()):
        size = f.stat().st_size
        print(f"   {f.name}  ({size:,} bytes)")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    main()
