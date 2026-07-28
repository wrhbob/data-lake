"""临时驱动：跑 run_quota_pipeline 全流程（OCR + extract + 5 步 autofinalize）。
产出落 D:/工程造价学习/data_lake0714/data_lake0714/quota/parser/tests/out/pipeline_real_ocr_v2/
日志同步打 stdout/stderr。
"""
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent                  # .../quota/parser/tests
QUOTA_PARSER_DIR = HERE.parent                            # .../quota/parser
sys.path.insert(0, str(QUOTA_PARSER_DIR))

PDF = HERE / "fixtures" / "sichuan" / "sample.pdf"
WORK = HERE / "out" / "pipeline_real_ocr_v2"

print(f"[driver] PDF  = {PDF}")
print(f"[driver] WORK = {WORK}")
print(f"[driver] t0   = {time.strftime('%H:%M:%S')}")

from quota_parser import run_quota_pipeline  # noqa: E402

try:
    result = run_quota_pipeline(
        pdf_path=str(PDF),
        work_dir=str(WORK),
        province="sc",
        ocr_api_url=os.environ.get("OCR_URL", "http://172.16.20.23:8000"),
    )
    print()
    print("=== STAGE A RESULT ===")
    print(f"task_id           = {result.task_id}")
    print(f"status            = {result.status}")
    print(f"candidate         = {result.candidate_xlsx_path}")
    print(f"sha256            = {result.candidate_xlsx_sha256}")
    print(f"ocr_markdown      = {result.ocr_markdown_path}")
    print(f"issues_md_path    = {result.issues_md_path}")
    print(f"warnings          = {result.warnings}")
    print(f"metrics           = {result.metrics}")
except Exception as e:
    print()
    print(f"=== EXCEPTION: {type(e).__name__}: {e} ===")
    traceback.print_exc()
    sys.exit(1)

print(f"[driver] t1 = {time.strftime('%H:%M:%S')}  done.")
