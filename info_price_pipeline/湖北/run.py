"""湖北\run.py - 湖北专属 pipeline 入口 (2026-08-04 建)

用法:
  cd D:/AI学习/vs code/信息价提取/湖北
  ../.venv/Scripts/python.exe run.py <pdf_path> --city 湖北 --period 02 --year 2026
  ../.venv/Scripts/python.exe run.py <pdf_path> --city 湖北 --period 02 --year 2026 --offline

流程: step1 (mineru) → step2 (classify) → step3 (extract_hb)
     → step4 (clean) → step5 (report) → step6 (output xlsx)

依赖: .venv\Scripts\python.exe (根目录共享 venv, 带 requests)
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # 湖北/

# P1.5 pycache 自动清理 (防旧 .pyc 加载新代码)
_pycache = ROOT / "1_脚本" / "__pycache__"
if _pycache.exists():
    shutil.rmtree(_pycache)

# 加 1_脚本 到 sys.path
sys.path.insert(0, str(ROOT / "1_脚本"))

from step1_mineru import run_step1
from step2_classify import classify
from step3_extract_hb import extract
from step4_clean import clean
from step5_report import report
from step6_output_hb import write_xlsx_hb  # hb 专属 xlsx writer (7列/6列 schema)
from utils import city_to_code


def _find_python_exe():
    """自动检测 venv python.exe 路径"""
    candidates = [
        ROOT.parent / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def _find_ocr_cache(code, period, year=None):
    """offline 模式下找缓存 result.json"""
    candidates = []
    if year is not None:
        candidates += [
            ROOT / "3_中间产物" / f"{code}_{year}_{period}_ocr" / "result.json",
            ROOT.parent / "3_中间产物" / f"{code}_{year}_{period}_ocr" / "result.json",
        ]
    candidates += [
        ROOT / "3_中间产物" / f"{code}_{period}_ocr" / "result.json",
        ROOT.parent / "3_中间产物" / f"{code}_{period}_ocr" / "result.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        f"未找到缓存 result.json。已查路径:\n  " +
        "\n  ".join(str(c) for c in candidates) +
        f"\n请确认 OCR 已跑过, 或去掉 --offline 重跑"
    )


def main():
    p = argparse.ArgumentParser(description="信息价提取 (湖北 v21)")
    p.add_argument("pdf_path", help="PDF 路径")
    p.add_argument("--city", required=True, help="城市 (湖北)")
    p.add_argument("--period", required=True, help="出版期号, 如 02")
    p.add_argument("--year", required=True, help="年份, 如 2026")
    p.add_argument("--data-month", default="未知", help="数据月份")
    p.add_argument("--cycle-type", default="未知", help="周期类型: 月刊/季刊/半年刊")
    p.add_argument("--offline", action="store_true", help="用 3_中间产物/ 缓存, 跳过 step1")
    p.add_argument("--python", dest="python_exe", default=None,
                   help="venv python.exe 路径 (不传则自动检测)")
    args = p.parse_args()

    if args.python_exe is None:
        args.python_exe = _find_python_exe()
    print(f"[run.py 湖北] python: {args.python_exe}")

    city = args.city
    period = args.period
    code = city_to_code(city)
    work_dir = ROOT / "3_中间产物" / f"{code}_{period}_ocr"
    log_lines = []

    # Step 1: OCR
    if args.offline:
        result_json = _find_ocr_cache(code, period, year=args.year)
        print(f"[offline] 跳过 step1, 从缓存读: {result_json}")
        log_lines.append(f"[step1] offline: 使用 {result_json}")
    else:
        s1 = run_step1(args.pdf_path, city, period, year=args.year, python_exe=args.python_exe)
        result_json = Path(s1["result_json"])

    # Step 2: 章节分类
    classified_json, log_lines = classify(result_json, log_lines, city=city, period=period)

    # Step 3: 字段提取 (湖北专属)
    extract_json = extract(classified_json, city, log_lines)

    # Step 4: 区县识别
    clean_json, log_lines = clean(extract_json, city, log_lines)

    # Step 5: 残缺统计
    report(clean_json, city, period, log_lines)

    # Step 6: 写 xlsx (hb 专属 writer)
    pdf_stem = Path(args.pdf_path).stem
    xlsx_path = write_xlsx_hb(
        clean_json, city, period, args.year,
        args.data_month, args.cycle_type,
        pdf_stem,
    )

    print(f"\n[run.py 湖北] ✅ 全部完成 xlsx: {xlsx_path}")


if __name__ == "__main__":
    main()
