"""run.py - 广州 pipeline 入口 (2026-08-05 建, Step 1)

Step 1: 跳过 step1 (OCR 已跑过), 只跑 step3 + step6
  - step3: result.json → extract.json (--only 调试参数透传)
  - step6: extract.json → 4_输出/<name>.xlsx

用法:
  python run.py                            # 跑全部 35 个 table
  python run.py --only 0                   # 只跑 table[0] (Step 1 调试)
  python run.py --name 10696342_full.xlsx  # 自定义 xlsx 文件名
"""
import argparse
import sys
from pathlib import Path

# Windows GBK 编码修复
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils_gz import ROOT

import step3_extract_gz as step3
import step6_output_gz as step6


def main():
    parser = argparse.ArgumentParser(description="广州 pipeline (Step 1: step3+step6)")
    parser.add_argument("--only", type=int, default=None, help="只跑第 N 个 table (调试)")
    parser.add_argument("--name", default="10696342_p6.xlsx", help="xlsx 输出文件名")
    parser.add_argument("--skip-step3", action="store_true", help="跳过 step3 (用已有 extract.json)")
    parser.add_argument("--skip-step6", action="store_true", help="跳过 step6 (只要 extract.json)")
    args = parser.parse_args()

    log_lines = []
    result_json = ROOT / "3_中间产物" / "gz_ocr" / "result.json"

    # step3
    if not args.skip_step3:
        if not result_json.exists():
            print(f"[run 广州] ❌ OCR result.json 不存在: {result_json}")
            print(f"[run 广州] 先跑 step1 OCR: 见 step1_ocr.py (TODO)")
            sys.exit(1)
        print(f"[run 广州] === step3: {result_json} ===")
        extract_path = step3.extract(str(result_json), only_idx=args.only, log_lines=log_lines)
        if extract_path is None:
            print(f"[run 广州] ❌ step3 验证失败, 终止")
            sys.exit(1)
    else:
        extract_path = str(ROOT / "3_中间产物" / "gz_ocr" / "extract.json")
        print(f"[run 广州] --skip-step3, 用: {extract_path}")

    # step6
    if not args.skip_step6:
        print(f"[run 广州] === step6: {extract_path} ===")
        # Step 1 调试时 xlsx 文件名加 _p6 后缀标识
        xlsx_name = args.name
        if args.only is not None and "p" not in args.name:
            xlsx_name = f"10696342_table{args.only}.xlsx"
        xlsx_path = step6.output(extract_path, xlsx_name=xlsx_name, log_lines=log_lines)
        print(f"[run 广州] ✅ 输出: {xlsx_path}")

    # 日志
    if log_lines:
        log_path = ROOT / "5_日志" / f"run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        print(f"[run 广州] 日志: {log_path}")


if __name__ == "__main__":
    main()