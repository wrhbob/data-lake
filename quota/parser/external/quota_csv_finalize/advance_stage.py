#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
advance_stage.py — 通用阶段包装脚本（删旧目录 + 建新目录 + cp PDF + xlsx 改名）

⚠️ 已废弃 (2026-07-27)：旧的「格式待审核 → 最终输出」两阶段流转已被合并。
现在 extract_quota.py 默认内嵌 autofinalize，5 步流水线原地覆盖写入
`<stem>_待审核.xlsx`，不再有格式待审核 / 最终输出阶段。

本脚本保留只为不破坏历史脚本引用；若仍有 3 阶段目录残留数据需要迁移，
可作为一次性命令手动跑一次把数据搬到 `最终输出/<stem>/`。日常流水线不再调用。

历史文档（CLAUDE.md §8.7）定义的"方案 B"：to_xlsx.py --stage 格式待审核
已内置阶段 2 包装，advance_stage.py 用于阶段 3（格式 → 最终）；也可参数化复用。

行为:
    1. 校验 --from-dir 存在、--to-dir 不存在（拒绝覆盖）、--src-pdf 存在
    2. 在 --from-dir 里找 *.xlsx（任意一个，警告多个）
    3. mkdir --to-dir
    4. cp xlsx → --to-dir/<xlsx-name>（默认 = <to_dir_basename>.xlsx，去后缀）
    5. cp PDF  → --to-dir/<PDF 原名>
    6. 删 --from-dir 整目录

退出码:
    0  成功
    1  参数解析失败
    2  源目录不存在
    3  目标目录已存在
    4  源目录里没有 xlsx
    5  --src-pdf 不存在 / 不是文件
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(
        description="通用阶段包装（删旧目录+建新目录+cp PDF+xlsx 改名）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--from-dir", required=True,
                    help="源子目录路径（被删除）")
    ap.add_argument("--to-dir", required=True,
                    help="目标子目录路径（被创建）")
    ap.add_argument("--src-pdf", required=True,
                    help="原始 PDF 路径（被 cp 到目标目录）")
    ap.add_argument("--xlsx-name", default=None,
                    help="目标 xlsx 文件名（默认 = 目标目录 basename + '.xlsx'）")
    args = ap.parse_args()

    from_dir = Path(args.from_dir).resolve()
    to_dir = Path(args.to_dir).resolve()
    src_pdf = Path(args.src_pdf).resolve()

    # ── 校验 ──
    if not from_dir.exists() or not from_dir.is_dir():
        print(f"[ERROR] 源目录不存在或不是目录: {from_dir}", file=sys.stderr)
        sys.exit(2)

    if to_dir.exists():
        print(f"[ERROR] 目标目录已存在: {to_dir}（拒绝覆盖）", file=sys.stderr)
        sys.exit(3)

    if not src_pdf.exists() or not src_pdf.is_file():
        print(f"[ERROR] --src-pdf 不存在或不是文件: {src_pdf}", file=sys.stderr)
        sys.exit(5)

    # ── 找源目录里的 xlsx ──
    xlsx_files = sorted(from_dir.glob("*.xlsx"))
    if not xlsx_files:
        print(f"[ERROR] 源目录没有 xlsx: {from_dir}", file=sys.stderr)
        sys.exit(4)
    if len(xlsx_files) > 1:
        print(f"[WARN] 源目录有 {len(xlsx_files)} 个 xlsx, 使用第一个: {xlsx_files[0].name}",
              file=sys.stderr)
    src_xlsx = xlsx_files[0]

    # ── 决定目标 xlsx 文件名 ──
    if args.xlsx_name:
        target_xlsx_name = args.xlsx_name
    else:
        # 默认: <to_dir_basename>.xlsx
        # 阶段 3 场景: to_dir basename = "<stem>" → "<stem>.xlsx"（无后缀）
        target_xlsx_name = to_dir.name + ".xlsx"

    # ── 1. mkdir 目标目录 ──
    to_dir.mkdir(parents=True)
    print(f"[OK] 建目录: {to_dir}")

    # ── 2. cp xlsx (改名) ──
    target_xlsx = to_dir / target_xlsx_name
    shutil.copy2(src_xlsx, target_xlsx)
    print(f"[OK] cp {src_xlsx.name} → {target_xlsx}")

    # ── 3. cp PDF (保留原名) ──
    target_pdf = to_dir / src_pdf.name
    shutil.copy2(src_pdf, target_pdf)
    print(f"[OK] cp {src_pdf.name} → {target_pdf}")

    # ── 4. 删源目录 ──
    shutil.rmtree(from_dir)
    print(f"[OK] 删源目录: {from_dir}")

    print(f"\n[DONE] 阶段包装完成: {from_dir} → {to_dir}")


if __name__ == "__main__":
    main()