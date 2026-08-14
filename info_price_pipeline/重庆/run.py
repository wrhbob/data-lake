"""重庆\run.py - 重庆专属 pipeline 入口（2026-07-29）

用法：
  python run.py                                    # 默认读 2_输入\*.pdf
  python run.py --pdf <path>                       # 指定 PDF
  python run.py --period 06 --year 2026 --month 5  # 指定期/年/月

流程（重庆独立）：
  step1 (mineru) → step2 (classify) → step3 (extract_cq)
  → step4 (clean) → step6 (output xlsx)

依赖：mineru skill 需 .venv\Scripts\python.exe（带 requests）
  其他步骤用当前 Python（重庆\1_脚本\ 自带 utils.py）
"""
import argparse
import glob
import sys
from pathlib import Path

# 把 1_脚本 加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent / "1_脚本"))

from utils import ROOT


def _venv_python():
    """找 .venv\Scripts\python.exe（带 requests 给 mineru 用）

    优先 ../.venv（根目录共享 venv），其次 ../重庆/.venv
    """
    candidates = [
        ROOT.parent / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return sys.executable  # fallback


def find_pdf():
    """默认从 2_输入\ 找第一个 PDF"""
    pdfs = glob.glob(str(ROOT / "2_输入" / "*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"未找到 PDF in {ROOT/'2_输入'}")
    return pdfs[0]


def run_pipeline(pdf_path, period, year, month):
    """跑完整 pipeline"""
    import step1_mineru
    import step2_classify
    import step3_extract_cq
    import step4_clean
    import step6_output

    pdf_name = Path(pdf_path).stem
    if not period:
        import re as _re
        m = _re.search(r"(\d{4})年?(\d{1,2})期", pdf_name)
        if m:
            year = year or int(m.group(1))
            period = period or m.group(2).zfill(2)

    work_dir = ROOT / "3_中间产物" / f"cq_{period}_ocr"
    work_dir.mkdir(parents=True, exist_ok=True)
    venv_py = _venv_python()
    print(f"[run.py] venv python: {venv_py}")

    # step1: PDF → result.json（用 venv python，mineru skill 需要 requests）
    print("=" * 60)
    print("[1/5] step1 mineru 解析 PDF")
    print("=" * 60)
    result_json = work_dir / "result.json"
    if not result_json.exists():
        step1_mineru.parse_pdf(venv_py, pdf_path, str(work_dir))
    else:
        print(f"  跳过（已存在 {result_json.name}）")

    # step2: result.json → classified.json
    print("=" * 60)
    print("[2/5] step2 classify 章节分类")
    print("=" * 60)
    classified_json = work_dir / "classified.json"
    if not classified_json.exists():
        step2_classify.classify(str(result_json), city="重庆市")
    else:
        print(f"  跳过（已存在 {classified_json.name}）")

    # step3: classified.json → extract.json
    print("=" * 60)
    print("[3/5] step3 重庆专属字段提取")
    print("=" * 60)
    extract_json = work_dir / "extract.json"
    if not extract_json.exists():
        step3_extract_cq.extract(str(classified_json))
    else:
        print(f"  跳过（已存在 {extract_json.name}）")

    # step4: extract.json → clean.json
    print("=" * 60)
    print("[4/5] step4 clean 区县识别")
    print("=" * 60)
    clean_json = work_dir / "clean.json"
    if not clean_json.exists():
        step4_clean.clean(str(extract_json), "重庆市")
    else:
        print(f"  跳过（已存在 {clean_json.name}）")

    # step6: clean.json → xlsx
    print("=" * 60)
    print("[5/5] step6 output 写 xlsx")
    print("=" * 60)
    cycle_type = "月"
    step6_output.write_xlsx(
        str(clean_json), "重庆市", period, str(year), str(month), cycle_type,
    )


def main():
    p = argparse.ArgumentParser(description="重庆信息价提取 pipeline")
    p.add_argument("--pdf", help="PDF 路径（默认读 2_输入\\*.pdf）")
    p.add_argument("--period", help="出版期 (如 06)")
    p.add_argument("--year", type=int, default=2026, help="年份")
    p.add_argument("--month", type=int, default=5, help="数据月份")
    args = p.parse_args()

    pdf = args.pdf or find_pdf()
    print(f"[run.py] PDF: {pdf}")
    run_pipeline(pdf, args.period, args.year, args.month)


if __name__ == "__main__":
    main()