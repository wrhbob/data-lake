"""run.py - 主入口，一键跑完整 6 步流水线（成都独立目录版）

用法：
  cd D:/AI学习/vs code/信息价提取/成都
  ../.venv/Scripts/python.exe run.py <pdf_path> \
    --city 成都 --period 06 --year 2026 --data-month 5 --cycle-type 月刊
  ../.venv/Scripts/python.exe run.py <pdf_path> \
    --city 成都 --period 06 --offline  （用 3_中间产物/ 缓存，跳过 step1）

2026-07-31 改（5 个判断坑自动处理）：
  - P1.1 venv 自动检测：先 ../.venv，再 ./.venv，再 sys.executable
  - P1.2 --offline 缓存多路径：先 成都/3_中间产物/，再 ../3_中间产物/
  - P1.5 pycache 自动清理：启动时清本目录 1_脚本/__pycache__/
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # 成都/

# P1.5 pycache 自动清理（防旧 .pyc 加载新 .py 代码）
_pycache = ROOT / "1_脚本" / "__pycache__"
if _pycache.exists():
    shutil.rmtree(_pycache)

# 加 1_脚本 到 sys.path
sys.path.insert(0, str(ROOT / "1_脚本"))

from step1_mineru import run_step1
from step2_classify import classify
from step4_clean import clean
from step5_report import report
from step6_output import write_xlsx
from utils import city_to_code  # ROOT 用本目录的，不是 utils 的

# P1.1 venv 自动检测：先 ../.venv（项目根共享），再 ./.venv（本目录），再 sys.executable（兜底）
def _find_python_exe():
    """自动检测 venv python.exe 路径"""
    candidates = [
        ROOT.parent / ".venv" / "Scripts" / "python.exe",   # ../.venv（根共享）
        ROOT / ".venv" / "Scripts" / "python.exe",         # ./.venv（本目录）
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # 兜底：用当前 python.exe（用户主动指定 python 时）
    return sys.executable


# 城市 → step3 模块路由（per-city 独立架构）
CITY_STEP3 = {
    "成都": "step3_extract_cd",
    "湖北": "step3_extract_hb",
}


# P1.2 --offline 缓存多路径查找
def _find_ocr_cache(code, period, year=None):
    """offline 模式下找缓存 result.json，多路径尝试

    2026-07-31 改：加新命名规则 cd_{year}_{period}_ocr（向后兼容旧命名 cd_{period}_ocr）
    """
    candidates = []
    if year is not None:
        # 新命名规则优先（成都 2026-07-31 改）
        candidates += [
            ROOT / "3_中间产物" / f"{code}_{year}_{period}_ocr" / "result.json",
            ROOT.parent / "3_中间产物" / f"{code}_{year}_{period}_ocr" / "result.json",
        ]
    # 旧命名规则兜底（向后兼容）
    candidates += [
        ROOT / "3_中间产物" / f"{code}_{period}_ocr" / "result.json",
        ROOT.parent / "3_中间产物" / f"{code}_{period}_ocr" / "result.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    # 都没有 → 报错列出候选路径
    raise FileNotFoundError(
        f"未找到缓存 result.json。已查路径：\n  " +
        "\n  ".join(str(c) for c in candidates) +
        f"\n请确认 OCR 已跑过，或去掉 --offline 重跑"
    )


def main():
    p = argparse.ArgumentParser(description="信息价提取 v2 (成都)")
    p.add_argument("pdf_path", help="PDF 路径")
    p.add_argument("--city", required=True, help="城市（成都）")
    p.add_argument("--period", required=True, help="出版期号，如 06")
    p.add_argument("--year", required=True, help="年份，如 2026")
    p.add_argument("--data-month", default="未知", help="数据月份")
    p.add_argument("--cycle-type", default="未知", help="周期类型：月刊/季刊/半年刊")
    p.add_argument("--offline", action="store_true", help="用 3_中间产物/ 缓存，跳过 step1")
    p.add_argument("--python", dest="python_exe", default=None,
                   help="venv 里的 python.exe 路径（不传则自动检测）")
    args = p.parse_args()

    # P1.1 venv 自动检测
    if args.python_exe is None:
        args.python_exe = _find_python_exe()
    print(f"[run.py] python: {args.python_exe}")

    city = args.city
    period = args.period
    code = city_to_code(city)
    ocr_dir = ROOT / "3_中间产物" / f"{code}_{period}_ocr"
    log_lines = []

    # city 路由检查
    if city not in CITY_STEP3:
        raise NotImplementedError(
            f"城市 '{city}' 未在 CITY_STEP3 注册。已注册: {sorted(CITY_STEP3.keys())}"
        )
    step3_module_name = CITY_STEP3[city]
    step3_module = __import__(step3_module_name)
    extract = step3_module.extract

    # Step 1: OCR
    if args.offline:
        # P1.2 缓存多路径查找
        result_json = _find_ocr_cache(code, period, year=args.year)
        print(f"[offline] 跳过 step1，从缓存读: {result_json}")
        log_lines.append(f"[step1] offline: 使用 {result_json}")
    else:
        s1 = run_step1(args.pdf_path, city, period, year=args.year, python_exe=args.python_exe)
        result_json = Path(s1["result_json"])

    # Step 2: 章节分类
    classified_json, log_lines = classify(result_json, log_lines, city=city, period=period)

    # Step 3: 字段提取
    extract_json, log_lines = extract(classified_json, city, log_lines)

    # Step 4: 区县识别
    clean_json, log_lines = clean(extract_json, city, log_lines)

    # Step 5: 残缺统计
    report(clean_json, city, period, log_lines)

    # Step 6: 写 xlsx
    # 2026-07-31 改：xlsx 文件名直接用 PDF stem，传给 step6 第 7 个参数
    pdf_stem = Path(args.pdf_path).stem
    xlsx_path = write_xlsx(
        clean_json, city, period, args.year,
        args.data_month, args.cycle_type,
        pdf_stem,
    )

    print(f"\n[run.py] ✅ 全部完成 xlsx: {xlsx_path}")


if __name__ == "__main__":
    main()