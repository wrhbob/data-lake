#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
to_xlsx.py — 给 v2 多 sheet xlsx 套上分组大纲 + 段行合并 + 加粗（XLSX 版）

输入：<stem>_待审核.xlsx（前 4 步 clean_empty_qty / drop_toc_sections /
      fill_work_content / space_split_materials 跑完后产出的 xlsx；含
      "定额条目 / 册说明 / 章" sheet）

行为：
  1. 读「定额条目」sheet 的所有行
  2. 段行加粗 + 段行 C-D 列合并
  3. 四级嵌套分组：大类包含所有 A.X，中类包含所有 A.X.Y，小类包含其定额，
     定额行本身作为 outline_level=4 的分组
  4. 把已格式化的「定额条目」sheet 写回；其它 sheet（册说明 / 章）保持不动
  5. 默认原地覆盖写入（与同 pipeline 中其它 4 步一致）

CLI:
    python to_xlsx.py <input.xlsx> [output.xlsx]

不传第 2 个参数 → 原地覆盖输入文件。
传第 2 个参数 → 写入指定输出文件，不修改输入文件。

退出码：
  1  文件不存在 / sheet 缺失 / 异常
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font

TARGET_SHEET = "定额条目"


def get_section_depth(section_id: str) -> int:
    """A→1, A.1→2, A.1.1→3."""
    if not section_id:
        return 1
    return section_id.count(".") + 1


def find_end_row(sections: list[dict], idx: int, total_rows: int) -> int:
    """找到当前段之后第一个同级/更高级段的位置。"""
    sec_depth = sections[idx]["depth"]
    for j in range(idx + 1, len(sections)):
        if sections[j]["depth"] <= sec_depth:
            return sections[j]["row"] - 1
    return total_rows


def _read_sheet_rows(ws) -> list[list]:
    return [
        [str(v) if v is not None else "" for v in row]
        for row in ws.iter_rows(values_only=True)
    ]


def process_xlsx(input_path: Path, output_path: Path | None = None) -> Path:
    """给 input xlsx 的「定额条目」sheet 套格式;其它 sheet 保持不动。

    Args:
        input_path: 输入 xlsx 路径
        output_path: 输出 xlsx 路径;None → 原地覆盖 input_path

    Returns:
        实际写入的路径
    """
    if output_path is None:
        output_path = input_path

    if not input_path.exists():
        print(f"[ERROR] 文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    wb = load_workbook(input_path, read_only=False)
    if TARGET_SHEET not in wb.sheetnames:
        print(
            f"[ERROR] xlsx 中没有 {TARGET_SHEET!r} sheet;现有 sheet: {wb.sheetnames}",
            file=sys.stderr,
        )
        sys.exit(1)

    ws = wb[TARGET_SHEET]
    rows = _read_sheet_rows(ws)
    if not rows:
        print("[WARN] 空「定额条目」sheet")
        wb.save(output_path)
        return output_path

    ws.sheet_properties.outlinePr.summaryBelow = False
    total_rows = len(rows)

    # 1. 把数据写回(确认所有 cell 都在位) + 段行加粗
    bold_font = Font(bold=True)
    for r_idx, row in enumerate(rows, start=1):
        is_section = (str(row[0]).strip() if row else "") == "段"
        for c_idx, val in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            if is_section:
                cell.font = bold_font
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # 2. 段行 C-D 列合并
    for r_idx, row in enumerate(rows, start=1):
        if row and str(row[0]).strip() == "段":
            ws.merge_cells(
                start_row=r_idx, start_column=3, end_row=r_idx, end_column=4
            )

    # 3. 收集段与定额位置
    sections: list[dict] = []
    quotas: list[dict] = []

    for r_idx, row in enumerate(rows, start=1):
        if not row:
            continue
        row_type = str(row[0]).strip()
        if row_type == "段":
            sec_id = str(row[1]).strip() if len(row) > 1 else ""
            sections.append({
                "row": r_idx,
                "depth": get_section_depth(sec_id),
                "id": sec_id,
            })
        elif row_type == "定":
            quotas.append({"row": r_idx})

    # 4. 计算段结束位置
    for i in range(len(sections)):
        sections[i]["end_row"] = find_end_row(sections, i, total_rows)

    # 5. 定额结束位置(到下一定额之前,再限制在所属段内)
    for i, q in enumerate(quotas):
        if i + 1 < len(quotas):
            q["end_row"] = quotas[i + 1]["row"] - 1
        else:
            q["end_row"] = total_rows
        for sec in reversed(sections):
            if sec["row"] <= q["row"]:
                q["end_row"] = min(q["end_row"], sec["end_row"])
                break

    # 6. 建立分组区间
    groups: list[tuple[int, int, int]] = []

    for sec in sections:
        start = sec["row"] + 1
        end = sec["end_row"]
        if start <= end:
            groups.append((start, end, sec["depth"]))

    for q in quotas:
        start = q["row"] + 1
        end = q["end_row"]
        if start <= end:
            groups.append((start, end, 4))

    # 先低 level(大范围),后高 level(小范围),
    # 这样小范围 group 不会覆盖大范围 group 的端点
    groups.sort(key=lambda x: x[2])

    for start, end, level in groups:
        ws.row_dimensions.group(start, end, outline_level=level)

    wb.save(output_path)

    print(f"[OK] 输入: {input_path}")
    print(f"[OK] 输出: {output_path}")
    print(
        f"[OK] sheet: {TARGET_SHEET} 总行数 {total_rows}, "
        f"段数 {len(sections)}, 定额数 {len(quotas)}"
    )
    print(f"[OK] 分组区间数: {len(groups)}")
    print(
        f"[OK] 其它保留 sheet: {[s for s in wb.sheetnames if s != TARGET_SHEET]}"
    )
    return output_path


def main():
    ap = argparse.ArgumentParser(
        description="XLSX → 带分组大纲 + 段行合并的 Excel（quota-csv-finalize 第 5 步）"
    )
    ap.add_argument("input_xlsx", help="输入 xlsx 路径（典型为 <stem>_待审核.xlsx）")
    ap.add_argument(
        "output_xlsx",
        nargs="?",
        default=None,
        help="输出 xlsx 路径（默认 = 原地覆盖输入文件）",
    )
    args = ap.parse_args()

    input_path = Path(args.input_xlsx).resolve()
    if not input_path.exists():
        print(f"[ERROR] 文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output_xlsx).resolve() if args.output_xlsx else None
    process_xlsx(input_path, output_path)


if __name__ == "__main__":
    main()
