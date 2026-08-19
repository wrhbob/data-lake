"""step1_ocr.py — 调 MinerU 把 PDF 拆成 JSON 缓存

风险点：normalize_blocks() 的字段映射，按明天真实 schema 微调即可。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests
from utils import ROOT, save_cache, log_error, city_to_code

API_BASE = "http://171.212.159.15:8000"
ENDPOINT = "/parse"  # ← 明天跑不通就改这里（常见：/file_parse /api/v1/parse）


def call_mineru(pdf_path):
    """调 MinerU，返完整 PDF 的 JSON。"""
    with open(pdf_path, "rb") as f:
        r = requests.post(
            f"{API_BASE}{ENDPOINT}",
            files={"file": (Path(pdf_path).name, f, "application/pdf")},
            timeout=1800,
        )
    r.raise_for_status()
    return r.json()


def normalize_blocks(api_response):
    """把 MinerU 响应转成我们的标准 blocks[] 格式。

    字段映射按 MinerU 真实 schema 微调，下面是**先验猜测**。
    明天拿到真 schema 后只改这一函数（30 分钟）。
    """
    blocks = []
    pages = api_response.get("pages") or api_response.get("content_list") or []
    for page_idx, page in enumerate(pages, start=1):
        items = page.get("blocks") or page.get("items") or page.get("content_list") or []
        for item in items:
            blocks.append({
                "page": page_idx,
                "type": item.get("type", "text"),       # title / table / text / image
                "text": item.get("text", ""),
                "html": item.get("html"),
                "bbox": item.get("bbox"),
                "confidence": item.get("score") or item.get("confidence") or 0.0,
            })
    return blocks


def run_step1(pdf_path, city, period):
    """PDF → MinerU → 缓存到 3_中间产物/{code}_{period}_ocr/"""
    code = city_to_code(city)
    print(f"[step1] {pdf_path} → MinerU ..."  )

    try:
        api_response = call_mineru(pdf_path)
    except requests.exceptions.RequestException as e:
        log_error(f"step1 API 失败: {e}")
        raise

    # 1. 原始响应存一份（debug + schema 比对用）
    raw_path = ROOT / "3_中间产物" / f"{code}_{period}_ocr" / "_raw_response.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(api_response, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2. 转标准 blocks
    blocks = normalize_blocks(api_response)

    # 3. 按页存缓存
    page_blocks = {}
    for b in blocks:
        page_blocks.setdefault(b["page"], []).append(b)
    for page_idx in sorted(page_blocks):
        save_cache(code, period, page_idx, page_blocks[page_idx])

    print(f"[step1] 完成：{len(blocks)} blocks，{len(page_blocks)} 页，缓存目录 {code}_{period}_ocr/")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: python step1_ocr.py <pdf_path> <city> <period>", file=sys.stderr)
        print("例: python step1_ocr.py D:\\Desktop\\成都市_2026年06期.pdf 成都 06", file=sys.stderr)
        sys.exit(1)
    run_step1(sys.argv[1], sys.argv[2], sys.argv[3])
