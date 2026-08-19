"""step1_mineru.py - 调 mineru-pdf-parse skill 把 PDF 解析成 JSON + MD + HTML

目的：
  PDF -> MinerU API -> 3_中间产物/{city}_{period}_ocr/{stem}.{json,md,html}

依赖：
  1_脚本/mineru-pdf-parse/scripts/health_check.py  (健康检查)
  1_脚本/mineru-pdf-parse/scripts/parse_pdf.py   (主解析)

输入：
  2_输入/{city}_{period}_input.pdf  (源 PDF)

输出：
  3_中间产物/{city}_{period}_ocr/
    ├── {stem}.json   (完整 MinerU 响应，含 md_content + middle_json + content_list)
    ├── {stem}.md     (markdown 文本)
    └── {stem}.html   (HTML 预览)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ROOT, city_to_code, save_cache, log_error

# skill 脚本路径
SKILL_DIR = ROOT / "1_脚本" / "mineru-pdf-parse"
HEALTH_CHECK = SKILL_DIR / "scripts" / "health_check.py"
PARSE_PDF = SKILL_DIR / "scripts" / "parse_pdf.py"

# 子进程 env：强制 UTF-8，绕过 Windows GBK 编码（skill 里用了 ✅ 等 emoji）
# 2026-08-19 改：MinerU API 改用 171.212.159.15（mineru-pdf-parse SKILL 读 MINERU_API_URL）
_SUBPROCESS_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8",
                   "MINERU_API_URL": os.environ.get("MINERU_API_URL", "http://171.212.159.15:8000")}


def health_check(python_exe):
    """调 health_check.py 验证 MinerU API 在线"""
    r = subprocess.run(
        [python_exe, str(HEALTH_CHECK)],
        capture_output=True, text=True, timeout=30, errors="replace",
        env=_SUBPROCESS_ENV,
    )
    if r.returncode != 0:
        raise RuntimeError(f"MinerU 健康检查失败:\n{r.stdout}\n{r.stderr}")
    print((r.stdout or "").strip())


def parse_pdf(python_exe, pdf_path, output_dir):
    """调 parse_pdf.py 解析 PDF -> 输出到 output_dir"""
    r = subprocess.run(
        [python_exe, str(PARSE_PDF), str(pdf_path), str(output_dir)],
        capture_output=True, text=True, timeout=1800, errors="replace",  # 30 分钟
        env=_SUBPROCESS_ENV,
    )
    if r.returncode != 0:
        raise RuntimeError(f"parse_pdf 失败:\n{r.stdout}\n{r.stderr}")
    print((r.stdout or "").strip())


def run_step1(pdf_path, city, period, python_exe=None):
    """PDF -> MinerU -> 3_中间产物/{city}_{period}_ocr/

    python_exe: venv 里的 python.exe 路径（必传）
    """
    if python_exe is None:
        raise ValueError("必须传 python_exe（venv 里的 python.exe）")

    code = city_to_code(city)
    pdf_path = Path(pdf_path).resolve()
    out_dir = ROOT / "3_中间产物" / f"{code}_{period}_ocr"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[step1] {pdf_path.name} → MinerU")

    # 1. 健康检查
    print("[step1] 健康检查...")
    health_check(python_exe)

    # 2. parse_pdf
    print(f"[step1] 解析 PDF → {out_dir}/")
    parse_pdf(python_exe, pdf_path, out_dir)

    # 3. 验证 result.json 存在
    result_json = next(out_dir.glob("result.json"), None)
    if result_json is None:
        raise FileNotFoundError(f"未找到 {out_dir}/result.json")

    # 4. 解析 + 基础统计
    with result_json.open(encoding="utf-8") as f:
        data = json.load(f)

    # middle_json 和 content_list 是 JSON-encoded 字符串
    inner = data.get("results", {}).get("upload", {})
    middle_json_str = inner.get("middle_json", "{}")
    content_list_str = inner.get("content_list", "[]")

    pdf_info = json.loads(middle_json_str).get("pdf_info", [])
    content_list = json.loads(content_list_str)

    pages = len(pdf_info)
    items = len(content_list)
    tables = sum(1 for c in content_list if c.get("type") == "table")

    print(f"[step1] ✅ 完成：{pages} 页 / {items} items / {tables} 表格")
    print(f"[step1] 缓存目录: {out_dir}/")

    return {
        "result_json": str(result_json),
        "pages": pages,
        "items": items,
        "tables": tables,
    }


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("用法: python step1_mineru.py <pdf_path> <city> <period> <python_exe>", file=sys.stderr)
        sys.exit(1)
    run_step1(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
