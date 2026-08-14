#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pdf_page_extract — 从 PDF 抽取指定页面并合并为单个新 PDF。

用法:
    python pdf_page_extract.py <pdf> <pages> [-o OUTPUT]

参数:
    pdf     源 PDF 路径
    pages   页码规格字符串,1-indexed(与 PDF 阅读器一致),支持:
              * 单页:    2
              * 区间:    2-4    (闭区间,等价于 2,3,4)
              * 混合:    2,4-5,7
              * 容忍空格: 2, 4 - 5 , 7
            按出现顺序保留,自动去重。
    -o      输出 PDF 路径(默认推导: <源 stem>_p<页码序列>.pdf,与源同目录)

退出码:
    0  成功
    2  源 PDF 不存在 / 无法打开
    3  页码规格格式非法
    4  页码超出 PDF 总页数范围(此时不写任何输出文件)
    5  抽取过程失败(已清理可能残留的半成品)

详见同目录 pdf_page_extract_SPEC.md。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF


TOKEN_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


# ─────────────────────────── 页码规格解析 ───────────────────────────
def parse_page_spec(spec: str) -> list[int]:
    """把 "2,4-5,7" 解析成去重、保序的 1-indexed 页码列表。

    Raises:
        ValueError: 规格为空、token 非法、区间反向、起始 < 1 等。
    """
    if not spec or not spec.strip():
        raise ValueError("页码规格不能为空")

    result: list[int] = []
    seen: set[int] = set()
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            raise ValueError(f"页码规格含空 token: '{spec}'")
        m = TOKEN_RE.match(token)
        if not m:
            raise ValueError(
                f"页码规格 token 非法: '{token}' (期望 '5' 或 '5-8' 形式)"
            )
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if start < 1:
            raise ValueError(f"页码必须 >= 1: '{token}'")
        if end < start:
            raise ValueError(f"区间反向 (start > end): '{token}'")
        for p in range(start, end + 1):
            if p not in seen:
                seen.add(p)
                result.append(p)

    if not result:
        raise ValueError("页码规格解析结果为空")
    return result


def compress_pages(nums: list[int]) -> str:
    """把 [2,4,5,7] 压成 '2,4-5,7',用于默认文件名后缀。"""
    if not nums:
        return ""
    nums = sorted(set(nums))
    parts: list[str] = []
    s = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{s}-{prev}" if s != prev else str(s))
        s = prev = n
    parts.append(f"{s}-{prev}" if s != prev else str(s))
    return ",".join(parts)


def derive_default_output(src: Path, pages: list[int]) -> Path:
    """推导默认输出路径: <stem>_p<压缩页码>.pdf,放在源 PDF 同目录。"""
    return src.parent / f"{src.stem}_p{compress_pages(pages)}.pdf"


# ─────────────────────────── 范围校验 ───────────────────────────
def get_page_count(pdf: Path) -> int:
    """读 PDF 总页数;无法打开时抛 RuntimeError。"""
    try:
        doc = fitz.open(str(pdf))
    except Exception as e:
        raise RuntimeError(f"无法打开 PDF(可能损坏或加密): {pdf}") from e
    try:
        return doc.page_count
    finally:
        doc.close()


def find_out_of_range(pages: list[int], total: int) -> list[int]:
    """返回所有不在 [1, total] 内的页码(原样保留,便于用户回溯)。"""
    return [p for p in pages if p < 1 or p > total]


# ─────────────────────────── 抽取与合并 ───────────────────────────
def extract_and_merge(
    src: Path, pages: list[int], dst: Path
) -> tuple[int, int]:
    """从 src 抽 pages(1-indexed)合并后写到 dst。

    约定:进入此函数前已校验完范围。返回 (源总页数, 实际写出页数)。
    失败时清理可能残留的 dst,然后 re-raise。
    """
    doc = fitz.open(str(src))
    out = fitz.open()
    try:
        total = doc.page_count
        for p in pages:
            out.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(dst))
        written = len(pages)
    except Exception:
        # 不留半成品文件
        out.close()
        doc.close()
        if dst.exists():
            try:
                dst.unlink()
            except OSError:
                pass
        raise
    finally:
        try:
            out.close()
        except Exception:
            pass
        try:
            doc.close()
        except Exception:
            pass
    return total, written


# ─────────────────────────── CLI ───────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf_page_extract",
        description="从 PDF 抽取指定页面并合并为单个新 PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "页码规格示例 (1-indexed,与 PDF 阅读器显示一致):\n"
            '  2                  单页\n'
            '  2-4                区间 [2,4] 闭区间\n'
            '  2,4-5,7            混合(顺序保留)\n'
            '  2, 4 - 5 , 7       容忍空白\n'
        ),
    )
    p.add_argument("pdf", help="源 PDF 路径")
    p.add_argument(
        "pages",
        help='页码规格,如 "2,4-5,7"',
    )
    p.add_argument(
        "-o",
        "--output",
        help="输出 PDF 路径(默认: <源 stem>_p<压缩页码>.pdf,与源同目录)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src = Path(args.pdf)

    # 1) 源文件存在性
    if not src.exists():
        print(f"[错误] 源 PDF 不存在: {src}", file=sys.stderr)
        return 2
    if not src.is_file():
        print(f"[错误] 源路径不是文件: {src}", file=sys.stderr)
        return 2

    # 2) 解析页码规格
    try:
        pages = parse_page_spec(args.pages)
    except ValueError as e:
        print(f"[错误] 页码规格非法: {e}", file=sys.stderr)
        return 3

    # 3) 读总页数
    try:
        total = get_page_count(src)
    except RuntimeError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2

    # 4) 范围校验 — 越界则终止,绝不写任何文件
    oob = find_out_of_range(pages, total)
    if oob:
        print(
            f"[错误] 页码超出范围: {oob} 不在 [1, {total}]\n"
            f"  源 PDF 总页数: {total}\n"
            f"  合法范围: 1-{total}\n"
            f"  不写任何输出文件,程序停止。",
            file=sys.stderr,
        )
        return 4

    # 5) 推导输出路径
    out_path = Path(args.output) if args.output else derive_default_output(src, pages)

    # 6) 抽取 + 合并
    try:
        total, written = extract_and_merge(src, pages, out_path)
    except Exception as e:
        print(f"[错误] 抽取页面失败: {e}", file=sys.stderr)
        return 5

    # 7) 报告
    print(f"[完成] 源 PDF      : {src}")
    print(f"[完成] 源总页数    : {total}")
    print(f"[完成] 抽出页数    : {written}")
    print(f"[完成] 页码清单    : {pages}  (1-indexed,按输入顺序,已去重)")
    print(f"[完成] 输出文件    : {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
