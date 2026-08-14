r"""北京\run.py - 北京专属 pipeline 入口（2026-07-29 首次建）

用法：
  python run.py --offline                      # 用 bj_06_ocr 缓存，跳过 step1
  python run.py --pdf <path> --period 06 --year 2026 --month 5

流程：
  step1 (mineru) → step2 (classify) → step3 (extract_bj)
  → step4 (clean) → step5 (report) → step6 (output xlsx)

依赖：
  .venv\Scripts\python.exe（根目录共享）带 requests 给 mineru 用
"""
import argparse
import glob
import sys
from pathlib import Path

# 把 1_脚本 加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent / "1_脚本"))

from utils import ROOT


def _venv_python():
    r"""找 .venv\Scripts\python.exe（带 requests 给 mineru 用）"""
    candidates = [
        ROOT.parent / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return sys.executable


def find_pdf():
    r"""默认从 2_输入\ 找第一个 PDF"""
    pdfs = glob.glob(str(ROOT / "2_输入" / "*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"未找到 PDF in {ROOT/'2_输入'}")
    return pdfs[0]


def run_pipeline(pdf_path, period, year, month, offline=False):
    """跑完整 pipeline"""
    import step1_mineru
    import step2_classify
    import step3_extract_bj
    import step4_clean
    import step5_report
    import step6_output

    if pdf_path and not period:
        pdf_name = Path(pdf_path).stem
        import re as _re
        m = _re.search(r"(\d{4})年?(\d{1,2})期", pdf_name)
        if m:
            year = year or int(m.group(1))
            period = period or m.group(2).zfill(2)

    work_dir = ROOT / "3_中间产物" / f"bj_{period}_ocr"
    work_dir.mkdir(parents=True, exist_ok=True)
    venv_py = _venv_python()
    print(f"[run.py] venv python: {venv_py}")

    # step1: PDF → result.json
    print("=" * 60)
    print("[1/6] step1 mineru 解析 PDF")
    print("=" * 60)
    result_json = work_dir / "result.json"
    if offline:
        print(f"  [offline] 使用缓存: {result_json}")
        if not result_json.exists():
            raise FileNotFoundError(f"未找到缓存 {result_json}")
    elif pdf_path and not result_json.exists():
        step1_mineru.parse_pdf(venv_py, pdf_path, str(work_dir))
    else:
        print(f"  跳过（已存在 {result_json.name} 或无 PDF）")

    # step2: result.json → classified.json
    print("=" * 60)
    print("[2/6] step2 classify 章节分类")
    print("=" * 60)
    classified_json = work_dir / "classified.json"
    if not classified_json.exists():
        step2_classify.classify(str(result_json), city="北京市")
    else:
        print(f"  跳过（已存在 {classified_json.name}）")

    # step3: classified.json → extract.json
    print("=" * 60)
    print("[3/6] step3 北京专属字段提取")
    print("=" * 60)
    extract_json = work_dir / "extract.json"
    if not extract_json.exists():
        step3_extract_bj.extract(str(classified_json), "北京市")
    else:
        print(f"  跳过（已存在 {extract_json.name}）")

    # step4: extract.json → clean.json
    print("=" * 60)
    print("[4/6] step4 clean 区县识别")
    print("=" * 60)
    clean_json = work_dir / "clean.json"
    if not clean_json.exists():
        step4_clean.clean(str(extract_json), "北京市")
    else:
        print(f"  跳过（已存在 {clean_json.name}）")

    # step5: 残缺统计
    print("=" * 60)
    print("[5/6] step5 report 残缺统计")
    print("=" * 60)
    step5_report.report(str(clean_json), "北京市", period, [])

    # step6: clean.json → xlsx
    print("=" * 60)
    print("[6/6] step6 output 写 xlsx")
    print("=" * 60)
    cycle_type = "月"
    step6_output.write_xlsx(
        str(clean_json), "北京市", period, str(year), str(month), cycle_type,
    )


def main():
    p = argparse.ArgumentParser(description="北京信息价提取 pipeline")
    p.add_argument("--pdf", help="PDF 路径（默认读 2_输入\\*.pdf）")
    p.add_argument("--period", help="出版期 (如 06)")
    p.add_argument("--year", type=int, default=2026, help="年份")
    p.add_argument("--month", type=int, default=5, help="数据月份")
    p.add_argument("--offline", action="store_true", help="用 bj_06_ocr 缓存，跳过 step1")
    args = p.parse_args()

    pdf = args.pdf
    if not pdf and not args.offline:
        pdf = find_pdf()
    print(f"[run.py] PDF: {pdf or '(offline 无 PDF)'}")
    run_pipeline(pdf, args.period, args.year, args.month, offline=args.offline)


if __name__ == "__main__":
    main()