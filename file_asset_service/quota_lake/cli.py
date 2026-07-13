"""CLI 入口 — 串联完整解析管线.

用法::

    python -m quota_lake.cli parse \\
      --pdf "四川2020房建册.pdf" \\
      --book-id <uuid> \\
      --profile sichuan_2020 \\
      --pages 25-462 \\
      --output ./output

    python -m quota_lake.cli qa --book-id <uuid>
"""

import argparse
import json
import os
import re
import sys
from typing import List, Optional

from quota_lake.config import get_lake_config
from quota_lake.parser.page_classifier import PageType, classify_page
from quota_lake.parser.table_parser import (
    TableContext,
    html_table_to_grid,
    parse_quota_table,
    ParsedQuotaItem,
    ParsedResource,
    DEFAULT_CODE_PATTERN,
    RE_WORK,
    RE_CHAPTER,
    clean,
)


# ── Profile 注册表 ──

_PROFILES = {
    "sichuan_2020": "quota_lake.parser.profiles.sichuan_2020:SichuanProfile2020",
}


def load_profile(name: str):
    """加载省级 Profile."""
    if name not in _PROFILES:
        raise ValueError(f"未知 profile: {name}, 可用: {list(_PROFILES.keys())}")
    import importlib

    module_path, class_name = _PROFILES[name].split(":")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)()


# ── 水印字符集 (从 parse_dinge.py 提取) ──

WATERMARK_DEFAULT = set("四川省住房和城乡建设厅信息公开浏览专用")


# ── 管线 ──

def run_pipeline(
    pdf_path: str,
    out_dir: str,
    page_from: int,
    page_to: int,
    profile_name: str = "sichuan_2020",
    use_gpu: bool = False,
    skip_ocr: bool = False,
) -> dict:
    """运行完整解析管线: render → ocr → classify → table_parse → persist.

    Returns:
        统计摘要 dict.
    """
    config = get_lake_config()
    profile = load_profile(profile_name)
    watermarks = profile.watermark_chars or WATERMARK_DEFAULT

    # ── 1. Render: PDF → 页面图像 ──
    from quota_lake.parser.render import pdf_to_images

    img_dir = os.path.join(out_dir, "pages")
    pages = pdf_to_images(pdf_path, img_dir, page_from, page_to, dpi=config.render_dpi)
    print(f"[render] {len(pages)} 页图像 → {img_dir}")

    if skip_ocr:
        return {"pages_rendered": len(pages)}

    # ── 2. OCR ──
    from quota_lake.parser.ocr_engine import OcrEngine

    ocr = OcrEngine(lang=config.ocr_lang, use_gpu=use_gpu)

    # ── 3. 逐页分类 + 解析 ──
    code_pattern_re = re.compile(profile.code_pattern)
    chapter_pattern_re = re.compile(profile.chapter_pattern)

    chapter_path = []
    list_code_prefix = ""
    work_content = ""
    all_items: List[ParsedQuotaItem] = []
    all_chapter_notes = []
    stats = {
        "pages_total": len(pages),
        "pages_quota_table": 0,
        "pages_chapter": 0,
        "pages_notes": 0,
        "pages_cover_toc": 0,
        "pages_appendix": 0,
        "pages_other": 0,
        "items_total": 0,
    }

    for pno, img in pages:
        blocks = ocr.parse_page(img)
        page_type = classify_page(
            blocks,
            code_pattern=profile.code_pattern,
            chapter_pattern=profile.chapter_pattern,
        )

        if page_type == PageType.COVER_TOC:
            stats["pages_cover_toc"] += 1
            continue
        elif page_type == PageType.CHAPTER_TITLE:
            stats["pages_chapter"] += 1
        elif page_type == PageType.NOTES:
            stats["pages_notes"] += 1
        elif page_type == PageType.APPENDIX_PRICE:
            stats["pages_appendix"] += 1
            continue  # P0 阶段不解析附录价格表
        elif page_type == PageType.QUOTA_TABLE:
            stats["pages_quota_table"] += 1
        else:
            stats["pages_other"] += 1

        # 解析文本 blocks 中的章节/工作内容/附注
        page_text_blocks = []
        for blk in blocks:
            btype = blk.get("type") or blk.get("label", "")
            if btype == "table":
                html = blk.get("html") or blk.get("res", {}).get("html", "")
                if not html:
                    continue
                grid = html_table_to_grid(html)

                context = TableContext(
                    chapter_path=chapter_path,
                    list_code_prefix=list_code_prefix,
                    work_content=work_content,
                    page=pno,
                )
                items = parse_quota_table(
                    grid,
                    context,
                    code_pattern=code_pattern_re,
                    code_header_keywords=list(profile.code_header_keywords),
                    watermarks=watermarks,
                )
                all_items.extend(items)
                stats["items_total"] += len(items)
            else:
                txt = clean(
                    blk.get("text") or blk.get("res", ""), watermarks
                )
                if not txt:
                    continue
                page_text_blocks.append(txt)

                # 章节标题
                m = chapter_pattern_re.match(txt)
                if m:
                    level = m.group(1).count(".")
                    chapter_path = chapter_path[: level]
                    chapter_path = chapter_path[: level] + [
                        f"{m.group(1)} {m.group(2)}"
                    ]
                    if m.group(3):
                        list_code_prefix = m.group(3)

                # 工作内容
                mw = RE_WORK.search(txt)
                if mw:
                    work_content = mw.group(1)

                # 附注
                mn = re.search(r"^注\s*[::]\s*(.+)", txt)
                if mn and all_items:
                    for it in all_items:
                        if it.page == pno:
                            it.note = (it.note + ";" if it.note else "") + mn.group(1)

                # 说明
                if "说明" in txt[:4]:
                    all_chapter_notes.append(
                        {
                            "chapter": list(chapter_path),
                            "page": pno,
                            "text": txt,
                        }
                    )

    # ── 4. 落盘 ──
    _save_output(all_items, all_chapter_notes, out_dir)

    return stats


def _save_output(
    items: List[ParsedQuotaItem],
    chapter_notes: list,
    out_dir: str,
) -> None:
    """将解析结果写入 JSON 文件."""
    os.makedirs(out_dir, exist_ok=True)

    data = []
    for it in items:
        item_dict = {
            "code": it.code,
            "name": it.name,
            "name_parts": it.name_parts,
            "unit": it.unit,
            "base_price": it.base_price,
            "labor_cost": it.labor_cost,
            "material_cost": it.material_cost,
            "machine_cost": it.machine_cost,
            "work_content": it.work_content,
            "note": it.note,
            "chapter_path": it.chapter_path,
            "list_code_prefix": it.list_code_prefix,
            "page": it.page,
            "resources": [
                {
                    "category": r.get("category", getattr(r, "category", "")),
                    "name": r.get("name", getattr(r, "name", "")),
                    "spec": r.get("spec", getattr(r, "spec", "")),
                    "unit": r.get("unit", getattr(r, "unit", "")),
                    "price": r.get("price", getattr(r, "price", None)),
                    "quantity": r.get("quantity", getattr(r, "quantity", None)),
                }
                if isinstance(r, dict)
                else {
                    "category": getattr(r, "category", ""),
                    "name": getattr(r, "name", ""),
                    "spec": getattr(r, "spec", ""),
                    "unit": getattr(r, "unit", ""),
                    "price": getattr(r, "price", None),
                    "quantity": getattr(r, "quantity", None),
                }
                for r in it.resources
            ],
        }
        data.append(item_dict)

    output = {"items": data, "chapter_notes": chapter_notes}
    output_path = os.path.join(out_dir, "parsed_quota.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)
    print(f"[output] {len(items)} 子目 → {output_path}")


# ── QA 子命令 ──

def run_qa(book_id: str = None, items_path: str = None) -> dict:
    """运行 QA 校验.

    Args:
        book_id: 定额册 ID (从 DB 读取).
        items_path: 解析 JSON 路径 (从文件读取).

    Returns:
        QA 摘要.
    """
    from quota_lake.parser.qa import run_qa as run_checks

    if items_path:
        with open(items_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
        # 还原为 ParsedQuotaItem
        quota_items = []
        for d in items:
            qi = ParsedQuotaItem(
                code=d.get("code", ""),
                name=d.get("name", ""),
                name_parts=d.get("name_parts", []),
                unit=d.get("unit", ""),
                base_price=d.get("base_price"),
                labor_cost=d.get("labor_cost"),
                material_cost=d.get("material_cost"),
                machine_cost=d.get("machine_cost"),
                work_content=d.get("work_content", ""),
                note=d.get("note", ""),
                chapter_path=d.get("chapter_path", []),
                list_code_prefix=d.get("list_code_prefix", ""),
                page=d.get("page", 0),
            )
            for r in d.get("resources", []):
                qi.resources.append(
                    ParsedResource(
                        category=r.get("category", ""),
                        name=r.get("name", ""),
                        spec=r.get("spec", ""),
                        unit=r.get("unit", ""),
                        price=r.get("price"),
                        quantity=r.get("quantity"),
                    )
                )
            quota_items.append(qi)
    elif book_id:
        # 从 DB 读取 (P0 阶段 placeholder)
        raise NotImplementedError("DB 读取 QA 尚未实现, 请使用 --items 参数")
    else:
        raise ValueError("需要 --book-id 或 --items 参数")

    config = get_lake_config()
    result = run_checks(
        quota_items,
        tolerance=config.qa_price_tolerance,
    )

    summary = {
        "total_items": result.total_items,
        "passed": result.passed,
        "failed": result.failed,
        "warnings": result.warnings,
        "pass_rate": result.pass_rate,
        "base_price_checks": result.base_price_checks,
        "gap_checks": result.gap_checks,
        "structural_checks": result.structural_checks,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


# ── CLI ──

def main():
    ap = argparse.ArgumentParser(
        description="定额数据湖解析管线",
        prog="python -m quota_lake.cli",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # parse
    p = sub.add_parser("parse", help="完整解析管线")
    p.add_argument("--pdf", required=True, help="PDF 文件路径")
    p.add_argument("--book-id", help="定额册 UUID")
    p.add_argument("--profile", default="sichuan_2020", help="解析 Profile")
    p.add_argument("--pages", default="1-462", help="页面范围, 如 25-462")
    p.add_argument("--output", "-o", default="./output", help="输出目录")
    p.add_argument("--gpu", action="store_true", help="启用 GPU (需 paddlepaddle-gpu)")
    p.add_argument("--skip-ocr", action="store_true", help="仅渲染 PDF, 不 OCR")

    # qa
    q = sub.add_parser("qa", help="运行 QA 校验")
    q.add_argument("--book-id", help="定额册 UUID (从 DB 读取)")
    q.add_argument("--items", help="解析 JSON 路径 (从文件读取)")
    q.add_argument("--tolerance", type=float, default=0.05, help="基价校验容差")

    args = ap.parse_args()

    if args.command == "parse":
        p1, p2 = (int(x) for x in args.pages.split("-"))
        stats = run_pipeline(
            pdf_path=args.pdf,
            out_dir=args.output,
            page_from=p1,
            page_to=p2,
            profile_name=args.profile,
            use_gpu=args.gpu,
            skip_ocr=args.skip_ocr,
        )
        if not args.skip_ocr:
            print(f"\n完成: {stats['items_total']} 个子目")
            print(f"页分类: 定额表={stats['pages_quota_table']} "
                  f"章节={stats['pages_chapter']} "
                  f"说明={stats['pages_notes']} "
                  f"附录={stats['pages_appendix']} "
                  f"封面目录={stats['pages_cover_toc']} "
                  f"其他={stats['pages_other']}")

    elif args.command == "qa":
        run_qa(
            book_id=args.book_id,
            items_path=args.items,
        )


if __name__ == "__main__":
    main()
