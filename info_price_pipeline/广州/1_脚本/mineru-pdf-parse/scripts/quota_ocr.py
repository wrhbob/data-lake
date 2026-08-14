#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quota_ocr.py — 从零写的 PDF → Markdown/HTML 解析脚本（同步 /file_parse 端点）

设计动机:
  parse_chunked.py 在 214 页 PDF 第二段任务上静默退出，疑似 MinerU 单进程
  内部状态 bug（任务为单进程、进程内状态实现 — 截图 Tip）。
  因此本脚本**只用同步 /file_parse 端点**（一次只提交一个任务，等返回，
  无后台 in-process state 切换），避免连续任务带来的状态污染。

关键约束（来自 CLAUDE.md §4.2）:
  1. ASCII 文件名 `upload.pdf`：避免 multipart 双编码（Windows + Git Bash）
  2. Python `requests` 库：curl 在 Windows + Git Bash 下 multipart 不稳定
  3. 单 PDF 处理，无并发
  4. response_format_zip=true：拿到 zip 二进制再解压

用法:
    python quota_ocr.py <pdf_path> [-o OUTPUT_DIR]
    python quota_ocr.py <pdf_path> --url http://172.16.20.23:8000

输出（默认在 PDF 同目录，与原 PDF 同 stem）:
    <stem>.md       ← md_content 原文（含 <table> HTML）
    <stem>.html     ← 浏览器可看的预览页
    result.json     ← MinerU 完整响应（middle_json + content_list）

退出码:
    0  → 成功
    1  → PDF 不存在 / 不是文件
    2  → API 不通（连接失败 / 超时）
    3  → HTTP 非 200
    4  → response 不是 zip（response_format_zip=true 但返回了别的）
    5  → zip 解压失败 / 缺关键产物
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import requests


def copy_pdf_to_ascii_name(pdf_path: Path) -> tuple[Path, tempfile.TemporaryDirectory]:
    """把 PDF cp 到一个 ASCII 文件名 upload.pdf（multipart 编码稳定性）。

    返回: (上传用的 Path, 持有临时目录的对象)
    调用方负责退出时调用 tmp.cleanup()。
    """
    tmp = tempfile.TemporaryDirectory(prefix="quota_ocr_")
    upload_path = Path(tmp.name) / "upload.pdf"
    # 用 shutil.copy2 保留元数据；multipart 传输只看内容
    import shutil
    shutil.copy2(pdf_path, upload_path)
    return upload_path, tmp


def call_file_parse(
    upload_path: Path,
    api_url: str,
    *,
    return_md: bool = True,
    return_original_file: bool = True,
    response_format_zip: bool = True,
    timeout: float = 1800.0,  # 436 页 PDF 实测 ~9 分钟，给 30 分钟兜底
) -> bytes:
    """调 POST /file_parse，返回 zip bytes。

    关键 form fields（截图 API 契约）:
      - files=@upload.pdf（multipart）
      - return_md=true
      - return_original_file=true
      - response_format_zip=true
    """
    url = f"{api_url.rstrip('/')}/file_parse"

    with upload_path.open("rb") as f:
        files = {"files": ("upload.pdf", f, "application/pdf")}
        data = {
            "return_md": "true" if return_md else "false",
            "return_original_file": "true" if return_original_file else "false",
            "response_format_zip": "true" if response_format_zip else "false",
        }

        t0 = time.time()
        print(f"[OK] POST {url}", flush=True)
        print(f"     files: {upload_path.name}  ({upload_path.stat().st_size:,} bytes)", flush=True)
        print(f"     data:  {data}", flush=True)
        print(f"[..] 等待 MinerU 同步返回（214 页典型 ~3-9 min, 超时 {timeout:.0f}s）...", flush=True)

        r = requests.post(url, files=files, data=data, timeout=timeout)

    elapsed = time.time() - t0
    print(f"[OK] HTTP {r.status_code}  ({elapsed:.1f}s)", flush=True)
    print(f"     Content-Type: {r.headers.get('Content-Type', '?')}", flush=True)
    print(f"     Content-Length: {len(r.content):,} bytes", flush=True)

    if r.status_code != 200:
        # 可能是 zip 内的 error.json，先打 body 前 500 字
        print(f"[ERROR] HTTP 非 200", file=sys.stderr)
        print(f"        body 前 500 字符:", file=sys.stderr)
        print(f"        {r.text[:500]!r}", file=sys.stderr)
        raise RuntimeError(f"HTTP {r.status_code}")

    # 检查 content type 是不是 zip
    ct = r.headers.get("Content-Type", "")
    if "zip" not in ct.lower() and "octet-stream" not in ct.lower():
        # 也许 server 没设 zip content-type 但 body 仍是 zip？
        # 检查 magic bytes: PK\x03\x04
        if not r.content.startswith(b"PK\x03\x04"):
            print(f"[ERROR] 响应不是 zip (Content-Type={ct}, magic bytes 不匹配)",
                  file=sys.stderr)
            print(f"        body 前 500 字符:", file=sys.stderr)
            print(f"        {r.content[:500]!r}", file=sys.stderr)
            raise RuntimeError(f"response not a zip (Content-Type={ct})")

    return r.content


def extract_zip(zip_bytes: bytes, output_dir: Path) -> list[Path]:
    """解压 zip 到 output_dir，返回产物路径列表。

    MinerU zip 典型包含:
      - <stem>.md
      - <stem>.html
      - <stem>_origin.pdf（如果 return_original_file=true）
      - middle.json（结构化中间结果）
      - content_list.json（块级结构）
    """
    if not zip_bytes.startswith(b"PK\x03\x04"):
        raise RuntimeError(f"not a valid zip (magic={zip_bytes[:4]!r})")

    output_dir.mkdir(parents=True, exist_ok=True)

    extracted: list[Path] = []
    with zipfile.ZipFile(__import__("io").BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        print(f"[OK] zip 包含 {len(names)} 个文件:", flush=True)
        for n in names:
            print(f"     - {n}", flush=True)

        # 校验关键产物
        if not any(n.endswith(".md") for n in names):
            raise RuntimeError("zip 里没有任何 .md 文件")

        for n in names:
            # 跳过目录项 / __MACOSX
            if n.endswith("/") or n.startswith("__MACOSX"):
                continue
            target = output_dir / Path(n).name  # 去嵌套目录，统一平铺
            with zf.open(n) as src, target.open("wb") as dst:
                dst.write(src.read())
            extracted.append(target)
            print(f"[OK]  写出: {target}", flush=True)

    return extracted


def main():
    ap = argparse.ArgumentParser(
        description="PDF → MD/HTML（同步 /file_parse 端点，无异步任务）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("pdf_path", help="源 PDF 绝对路径")
    ap.add_argument("-o", "--output-dir", default=None,
                    help="产物落盘目录（默认 = PDF 同目录）")
    ap.add_argument("--url", default="http://172.16.20.23:8000",
                    help="MinerU API base URL (default: http://172.16.20.23:8000)")
    ap.add_argument("--timeout", type=float, default=1800.0,
                    help="HTTP 超时秒数 (default: 1800 = 30 分钟)")
    args = ap.parse_args()

    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.exists() or not pdf_path.is_file():
        print(f"[ERROR] PDF 不存在或不是文件: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = (Path(args.output_dir).resolve()
                  if args.output_dir else pdf_path.parent)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[OK] PDF:  {pdf_path}", flush=True)
    print(f"[OK] OUT:  {output_dir}", flush=True)
    print(f"[OK] API:  {args.url}", flush=True)
    print(flush=True)

    # ── 1. cp 到 ASCII 名 ──
    upload_path, tmp = copy_pdf_to_ascii_name(pdf_path)
    try:
        # ── 2. 调 /file_parse ──
        zip_bytes = call_file_parse(
            upload_path, args.url, timeout=args.timeout,
        )

        # ── 3. 解压到 output_dir ──
        extracted = extract_zip(zip_bytes, output_dir)

        print(flush=True)
        print(f"[DONE] PDF → {len(extracted)} 个产物，落盘到 {output_dir}", flush=True)
        # 检查关键产物
        has_md = any(p.suffix == ".md" for p in extracted)
        if not has_md:
            print(f"[ERROR] 缺 .md 产物", file=sys.stderr)
            sys.exit(5)
        sys.exit(0)
    except requests.exceptions.ConnectionError as e:
        print(f"[ERROR] API 连接失败: {e}", file=sys.stderr)
        sys.exit(2)
    except requests.exceptions.Timeout as e:
        print(f"[ERROR] API 超时 ({args.timeout}s): {e}", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(3)
    except zipfile.BadZipFile as e:
        print(f"[ERROR] zip 解压失败: {e}", file=sys.stderr)
        sys.exit(4)
    except Exception as e:
        print(f"[ERROR] 未预期错误: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(5)
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    main()