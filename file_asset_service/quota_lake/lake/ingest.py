"""PDF 入湖 — 上传 / 指纹 / 查重 / 清晰度探测."""

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IngestResult:
    """入湖结果."""
    ok: bool
    sha256: str = ""
    file_size: int = 0
    page_count: int = 0
    has_text_layer: bool = False
    clarity_samples: list = field(default_factory=list)  # [(page, mean_brightness), ...]
    object_key: str = ""
    duplicate: bool = False
    duplicate_file_id: str = None
    message: str = ""


def compute_sha256(file_path: str) -> str:
    """计算文件 SHA-256."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_pdf(pdf_path: str, sample_pages: tuple = (0, 25, 50)) -> dict:
    """完整性探测: 页数, 文字层, 抽样清晰度.

    Args:
        pdf_path: PDF 路径.
        sample_pages: 抽样的 0-based 页码列表.

    Returns:
        dict with page_count, has_text_layer, clarity_samples.
    """
    from quota_lake.parser.render import pdf_page_count, pdf_has_text_layer

    page_count = pdf_page_count(pdf_path)
    has_text = pdf_has_text_layer(pdf_path)

    # 抽样清晰度 (平均亮度)
    clarity = _sample_clarity(pdf_path, sample_pages, page_count)

    return {
        "page_count": page_count,
        "has_text_layer": has_text,
        "clarity_samples": clarity,
    }


def _sample_clarity(pdf_path: str, page_nums: tuple, total_pages: int) -> list:
    """抽样页面清晰度 (平均像素亮度)."""
    try:
        import fitz
    except ImportError:
        return []

    doc = fitz.open(pdf_path)
    samples = []
    for pno in page_nums:
        if pno >= total_pages:
            continue
        pix = doc[pno].get_pixmap(dpi=150, colorspace=fitz.csGRAY)
        if pix.samples:
            avg = sum(pix.samples) / len(pix.samples)
            samples.append((pno + 1, round(avg, 1)))
    return samples


def ingest_pdf(
    pdf_path: str,
    output_dir: str = None,
) -> IngestResult:
    """入湖一个 PDF 文件.

    Args:
        pdf_path: PDF 文件路径.
        output_dir: 可选输出目录.

    Returns:
        IngestResult.
    """
    if not os.path.isfile(pdf_path):
        return IngestResult(ok=False, message=f"文件不存在: {pdf_path}")

    file_size = os.path.getsize(pdf_path)
    sha256 = compute_sha256(pdf_path)
    probe = probe_pdf(pdf_path)

    return IngestResult(
        ok=True,
        sha256=sha256,
        file_size=file_size,
        page_count=probe["page_count"],
        has_text_layer=probe["has_text_layer"],
        clarity_samples=probe["clarity_samples"],
        message=f"入湖成功: {os.path.basename(pdf_path)} ({probe['page_count']} 页)",
    )


def build_object_key(province: str, edition_year: int, filename: str) -> str:
    """构建对象存储 key."""
    return f"quota/{province}/{edition_year}/{filename}"
