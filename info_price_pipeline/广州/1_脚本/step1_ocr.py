"""step1_ocr.py - 广州 OCR (MinerU v3.4.4 API)

输入: 2_输入文件/<stem>.pdf（默认 10857690.pdf，可 --pdf 指定）
输出: 3_中间产物/gz_ocr/
  ├── result.json     ← MinerU 完整响应 (data["results"]["upload"]["md_content"] 给 step3)
  ├── <stem>.md       ← md_content 原文
  └── <stem>.html     ← 浏览器预览

API: 环境变量 MINERU_API_URL (默认 http://171.212.159.15:8000)
约束 (2026-08-19 v1):
  - PDF ≤ 100 页走同步 /file_parse (49 页实测 ~60s)
  - 必须 ASCII multipart filename (upload.pdf) 避免中文双编码
  - hybrid-engine high 模式 (精度 95.39)
  - 单 PDF 串行 (12GB 显卡禁止并发)

用法:
  python step1_ocr.py
  python step1_ocr.py --pdf 10857690.pdf
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Windows GBK 编码修复
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF = ROOT / "2_输入文件" / "10857690.pdf"
OUT_DIR = ROOT / "3_中间产物" / "gz_ocr"
API_URL = os.environ.get("MINERU_API_URL", "http://171.212.159.15:8000")


def health_check() -> bool:
    """Step 0: 健康检查"""
    import requests
    try:
        r = requests.get(f"{API_URL}/health", timeout=10)
        if r.status_code == 200:
            data = r.json()
            print(f"[step1 广州] ✅ MinerU 健康: {data.get('version')} "
                  f"queue={data.get('queued_tasks')} processing={data.get('processing_tasks')}")
            return True
        print(f"[step1 广州] ❌ /health 返回 {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"[step1 广州] ❌ /health 失败: {e}")
        return False


def parse_pdf(pdf_path: Path, out_dir: Path) -> dict | None:
    """Step 1-5: 上传 + /file_parse + 写 result.json / .md / .html"""
    import requests

    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 拷贝为 ASCII 文件名（避免 multipart 中文双编码）
    work_pdf = out_dir / "upload.pdf"
    shutil.copy2(pdf_path, work_pdf)
    print(f"[step1 广州] → 拷贝 {pdf_path.name} → {work_pdf.name}")

    # Step 3: 调 /file_parse
    data = {
        "backend": "hybrid-engine",
        "effort": "high",
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
    print(f"[step1 广州] → POST {API_URL}/file_parse (hybrid-engine high) ...")
    with open(work_pdf, "rb") as f:
        files = {"files": ("upload.pdf", f, "application/pdf")}
        r = requests.post(f"{API_URL}/file_parse", files=files, data=data, timeout=1800)
    r.raise_for_status()

    # Step 4: 写 result.json
    result_path = out_dir / "result.json"
    result_path.write_bytes(r.content)
    print(f"[step1 广州] ✅ result.json ({len(r.content)/1024:.1f} KB)")

    # 解析出 md_content / html（中间产物）
    try:
        body = r.json()
        results = body.get("results", {})
        upload = results.get("upload", {})
        md_content = upload.get("md_content", "")
        html_content = upload.get("html_content", "") or upload.get("content_list_html", "")

        if md_content:
            md_path = out_dir / f"{pdf_path.stem}.md"
            md_path.write_text(md_content, encoding="utf-8")
            print(f"[step1 广州] ✅ {md_path.name} ({len(md_content)/1024:.1f} KB)")
        if html_content:
            html_path = out_dir / f"{pdf_path.stem}.html"
            html_path.write_text(html_content, encoding="utf-8")
            print(f"[step1 广州] ✅ {html_path.name} ({len(html_content)/1024:.1f} KB)")

        # 简短统计
        n_tables = md_content.count("<table>")
        print(f"[step1 广州] → md_content 包含 {n_tables} 个 <table>")
    except Exception as e:
        print(f"[step1 广州] ⚠️  解析 md/html 失败（不影响 result.json）: {e}")

    return r.json() if r.content else None


def main():
    parser = argparse.ArgumentParser(description="广州 OCR step1 (MinerU)")
    parser.add_argument("--pdf", type=str, default=None,
                        help=f"PDF 文件名（默认 {DEFAULT_PDF.name}，位于 2_输入文件/）")
    parser.add_argument("--output-dir", type=str, default=None,
                        help=f"输出目录（默认 {OUT_DIR}）")
    args = parser.parse_args()

    pdf_path = ROOT / "2_输入文件" / args.pdf if args.pdf else DEFAULT_PDF
    out_dir = Path(args.output_dir) if args.output_dir else OUT_DIR

    if not pdf_path.exists():
        print(f"[step1 广州] ❌ PDF 不存在: {pdf_path}")
        print(f"[step1 广州] 请把 PDF 放到 {ROOT / '2_输入文件'} 或 --pdf 指定")
        sys.exit(1)

    print(f"[step1 广州] PDF: {pdf_path}")
    print(f"[step1 广州] OUT:  {out_dir}")
    print(f"[step1 广州] API:  {API_URL}")

    if not health_check():
        print("[step1 广州] ❌ 健康检查失败，终止")
        sys.exit(1)

    body = parse_pdf(pdf_path, out_dir)
    if body is None:
        print("[step1 广州] ❌ OCR 失败")
        sys.exit(1)

    print(f"[step1 广州] ✅ 完成 → 下一步: python run.py")


if __name__ == "__main__":
    main()