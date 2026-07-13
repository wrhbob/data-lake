"""PDF → 页面图像渲染 (从 parse_dinge.py §1 零改动提取)."""

import os


def pdf_to_images(
    pdf_path: str, out_dir: str, page_from: int, page_to: int, dpi: int = 300
):
    """用 PyMuPDF 渲染 PDF 页面为图像文件.

    Args:
        pdf_path: PDF 文件路径.
        out_dir: 输出目录.
        page_from: 起始页码 (1-based, inclusive).
        page_to: 结束页码 (1-based, inclusive).
        dpi: 渲染 DPI, 默认 300 (扫描件 1:1).

    Returns:
        list[tuple[int, str]]: [(页码, 图像路径), ...].
    """
    import fitz

    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    paths = []
    zoom = dpi / 72
    for pno in range(page_from - 1, min(page_to, len(doc))):
        pix = doc[pno].get_pixmap(
            matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY
        )
        p = os.path.join(out_dir, f"page_{pno + 1:04d}.png")
        pix.save(p)
        paths.append((pno + 1, p))
    return paths


def pdf_page_count(pdf_path: str) -> int:
    """返回 PDF 总页数."""
    import fitz

    doc = fitz.open(pdf_path)
    return len(doc)


def pdf_has_text_layer(pdf_path: str) -> bool:
    """探测 PDF 是否含可提取的文字层 (抽样前 3 页)."""
    import fitz

    doc = fitz.open(pdf_path)
    total_chars = 0
    for pno in range(min(3, len(doc))):
        total_chars += len(doc[pno].get_text())
    return total_chars > 100
