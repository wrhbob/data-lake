"""打包前自检 + 报告 + 自动打 zip (2026-08-10)。

同事拿这个脚本在新机器上跑 `python package.py --check` 检查:
  1. PIPELINE_BASE 路径存在
  2. 6 城 (成都/重庆/北京/武汉/湖北/广州) 目录都在
  3. 6 城都含 <city>/run.py (广州在 1_脚本/run.py)
  4. 6 城都含 <city>/1_脚本/mineru-pdf-parse/scripts/parse_pdf.py
  5. PIPELINE_BASE/.venv/Scripts/python.exe 可用 (PyMuPDF/openpyxl/requests/PyYAML)
  6. data_lake0714/.env 含 INFO_PRICE_MINERU_API_URL + INFO_PRICE_PIPELINE_BASE
  7. info_price_parse.py 能 import, 6 城 handler pipeline_root 都能 resolve
  8. 6 城 pipeline_output_dir 都可写

用法:
    python package.py --check                       # 自检,退出码 0=全过 1=有缺失
    python package.py --list                        # 列出必带文件清单(给同事拷目录参考)
    python package.py --zip [输出路径]              # 打 zip 包(含网站全部 + 6 城处理脚本,不含密钥)
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent  # main 仓根目录（脚本已内迁，见 CLAUDE.md §分发）
PIPELINE_BASE = Path(os.getenv("INFO_PRICE_PIPELINE_BASE", str(PROJECT_ROOT / "info_price_pipeline")))
# 2026-08-13: 解析脚本改用 file-asset 环境（web 同款 python），不再维护独立 .venv
VENV_PYTHON = Path(sys.executable)

CITIES = ["成都", "重庆", "北京", "武汉", "湖北", "广州"]

# zip 包排除规则(2026-08-10,跟 信息价提取/.gitignore 对齐)
EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    "2_输入", "2_输入文件",
    "3_中间产物",
    "4_输出",
    "5_日志",
    "_verify_tmp",
    "_backup_2026-07-29", "_backup_2026-07-31_b-fix",  # 已挪到 _历史归档
    "_idx1309_raw",  # 噪音 HTML
}
EXCLUDE_FILE_PATTERNS = {
    "_idx1309.html",
    "_idx1309_raw.html",
    "console_service.log",
    "*.pyc", "*.pyo",
    ".DS_Store",
    "~$*.pdf",  # Office 临时文件
}


def _check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "✅" if ok else "❌"
    print(f"{mark} {name}" + (f"  ({detail})" if detail else ""))
    return ok


def run_check() -> int:
    failures = 0

    # 1. PIPELINE_BASE 存在
    if not _check("PIPELINE_BASE 存在", PIPELINE_BASE.is_dir(), str(PIPELINE_BASE)):
        failures += 1
        print(f"   修复: export INFO_PRICE_PIPELINE_BASE=<信息价提取根目录绝对路径>")
        return failures  # 后续检查都依赖它,直接退

    # 2. 6 城目录
    print()
    print("=== 6 城目录 ===")
    for city in CITIES:
        city_dir = PIPELINE_BASE / city
        if not _check(f"{city}/", city_dir.is_dir(), str(city_dir)):
            failures += 1
            continue

    # 3. 6 城 run.py
    print()
    print("=== 6 城 run.py (广州在 1_脚本/ 子目录) ===")
    for city in CITIES:
        if city == "广州":
            run_py = PIPELINE_BASE / "广州" / "1_脚本" / "run.py"
        else:
            run_py = PIPELINE_BASE / city / "run.py"
        if not _check(f"{city}/run.py", run_py.is_file(), str(run_py)):
            failures += 1

    # 4. 6 城 mineru-pdf-parse
    print()
    print("=== 6 城 mineru-pdf-parse/scripts/parse_pdf.py ===")
    for city in CITIES:
        mineru = PIPELINE_BASE / city / "1_脚本" / "mineru-pdf-parse" / "scripts" / "parse_pdf.py"
        if not _check(f"{city} mineru", mineru.is_file(), str(mineru)):
            failures += 1

    # 5. python 环境（file-asset）+ 必需包
    print()
    print("=== python 环境 + 依赖 ===")
    if _check("python.exe (file-asset)", VENV_PYTHON.is_file(), str(VENV_PYTHON)):
        try:
            import subprocess  # noqa: PLC0415

            r = subprocess.run(
                [str(VENV_PYTHON), "-c", "import fitz, openpyxl, requests, yaml; print('OK')"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                _check("必需包 (PyMuPDF/openpyxl/requests/PyYAML)", True, "import OK")
            else:
                _check("必需包", False, r.stderr.strip()[:200])
                failures += 1
        except Exception as exc:
            _check("必需包", False, str(exc)[:200])
            failures += 1
    else:
        failures += 1

    # 6. .env
    print()
    print("=== .env ===")
    env_file = PROJECT_ROOT / ".env"
    if not _check(".env 存在", env_file.is_file(), str(env_file)):
        failures += 1
        print("   修复: cp .env.example .env")
    else:
        env_content = env_file.read_text(encoding="utf-8")
        # 2026-08-13: INFO_PRICE_PIPELINE_BASE 已改为代码内相对推导，.env 不再需要
        for key in ("INFO_PRICE_MINERU_API_URL",):
            if not _check(f".env 含 {key}", key in env_content):
                failures += 1

    # 7. info_price_parse.py import + 6 城 handler
    print()
    print("=== info_price_parse 6 城 handler ===")
    sys.path.insert(0, str(ROOT))
    try:
        from app.info_price_parse import CITY_HANDLERS  # noqa: PLC0415

        for city, handler in CITY_HANDLERS.items():
            if not _check(f"{city} pipeline_root 存在", handler.pipeline_root.is_dir(), str(handler.pipeline_root)):
                failures += 1
            # 4_输出 是运行时产物,解析时才自动 mkdir,这里只查 pipeline_root 可写即可
            if not _check(f"{city} pipeline_root 可写", handler.pipeline_root.is_dir() and os.access(handler.pipeline_root, os.W_OK), str(handler.pipeline_root)):
                failures += 1
    except Exception as exc:
        _check("info_price_parse import", False, str(exc)[:200])
        failures += 1

    # 8. summary
    print()
    print("=" * 60)
    if failures == 0:
        print(f"🎉 全部通过 — 这台机器可以跑 6 城信息价解析流水线")
        return 0
    print(f"❌ {failures} 项缺失,按上面提示修复后再跑一次")
    return 1


def run_list() -> int:
    print("=== 必带文件清单 (同事照搬) ===")
    print()
    print(f"📁 {PROJECT_ROOT}/  (main 仓,单目录自包含)")
    print(f"   ├── file_asset_service/         # 主控台 web UI (FastAPI)")
    print(f"   │   ├── app/                    # 后端 Python 包 (含 info_price_parse.py)")
    print(f"   │   ├── ui/                     # 前端静态资源")
    print(f"   │   ├── serve.py                # 启动脚本")
    print(f"   │   ├── requirements.txt")
    print(f"   │   └── package.py              # ← 这个自检脚本")
    print(f"   ├── quota/                      # 定额解析 pipeline (parser/worker/sweeper)")
    print(f"   ├── info_price_pipeline/        # 信息价解析脚本 (6 城)")
    for city in CITIES:
        if city == "广州":
            print(f"   │   ├── {city}/1_脚本/run.py   # 广州 run.py 在 1_脚本/ 子目录")
        else:
            print(f"   │   ├── {city}/run.py          # 入口")
    print(f"   ├── .env.example                # 环境变量模板 (复制为 .env 后填真实连接)")
    print(f"   ├── CLAUDE.md                   # 项目说明 + 分发准备")
    print(f"   └── DEPLOY.md                   # 部署手册")
    print()
    print("❌ 不要带:")
    print(f"   - .env (含真实口令)")
    print(f"   - logs/ (本机日志 + pid)")
    print(f"   - info_price_pipeline/{{3_中间产物, 4_输出, 5_日志}}/*")
    print(f"   - __pycache__ / *.pyc / _baseline / *.bak*")
    return 0


def _should_skip(path: Path) -> bool:
    """判断是否应跳过(目录名/文件名匹配排除规则)。"""
    if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
        return True
    if path.name in EXCLUDE_FILE_PATTERNS:
        return True
    # 通配符匹配 (例如 *.pyc)
    for pat in EXCLUDE_FILE_PATTERNS:
        if "*" in pat and path.match(pat):
            return True
    return False


def run_zip(output_path: Path) -> int:
    """打 zip 包: 整个 main 仓(自包含,含 info_price_pipeline),自动跳过中间产物/缓存/.env。

    输出结构:
        <output_path>
        └── main/
            ├── .env.example
            ├── CLAUDE.md / DEPLOY.md
            ├── file_asset_service/
            │   ├── app/ (含 info_price_parse.py)
            │   ├── ui/
            │   ├── serve.py
            │   ├── package.py
            │   └── requirements.txt
            ├── quota/
            ├── info_price_pipeline/   (6 城脚本:成都/重庆/北京/武汉/湖北/广州)
            └── ...
    """
    output_path = output_path.resolve()
    if output_path.exists():
        print(f"❌ 输出文件已存在: {output_path}")
        return 1

    # zip 根:用 output_path.parent / output_path.stem 作临时目录
    zip_root_name = output_path.stem  # 例如 data_lake_handoff_2026-08-10
    tmp_dir = output_path.parent / zip_root_name
    if tmp_dir.exists():
        print(f"❌ 临时目录已存在: {tmp_dir}")
        return 1
    tmp_dir.mkdir(parents=True)

    # 单层复制整个 main 仓（info_price_pipeline 已内迁,无需第二层）
    print(f"📁 复制 main 仓 → {tmp_dir / 'main'}")
    shutil.copytree(
        PROJECT_ROOT,
        tmp_dir / "main",
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".git", "console_service.log",
            "*.pyc", "*.pyo", "*.tmp", "*.bak*",
            ".env",  # ⚠️ 不带密钥
            "2_输入", "2_输入文件", "3_中间产物", "4_输出", "5_日志",
            "logs", "_baseline",
            "_verify_tmp", "_idx1309.html", "_idx1309_raw.html",
            "_backup_2026-07-29", "_backup_2026-07-31_b-fix",
            "~$*.pdf",
        ),
    )

    # 第三层:打 zip
    print(f"\n📦 打 zip → {output_path}")
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        file_count = 0
        for f in tmp_dir.rglob("*"):
            if f.is_file():
                arcname = f.relative_to(tmp_dir.parent)
                zf.write(f, arcname)
                file_count += 1
                if file_count % 100 == 0:
                    print(f"  ... {file_count} files zipped", flush=True)

    # 第四层:清理临时目录
    shutil.rmtree(tmp_dir, ignore_errors=True)

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"\n🎉 完成 — {output_path}")
    print(f"   {file_count} files, {size_mb:.1f} MB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="打包前自检 + 报告 + 自动 zip")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="跑自检")
    g.add_argument("--list", action="store_true", help="列必带清单")
    g.add_argument("--zip", nargs="?", const="default", metavar="OUTPUT",
                   help="打 zip 包(默认输出 D:/AI学习/vs code/data_lake_handoff_<日期>.zip)")
    args = parser.parse_args()
    if args.check:
        return run_check()
    if args.list:
        return run_list()
    if args.zip:
        if args.zip == "default":
            today = datetime.date.today().isoformat()
            output = PROJECT_ROOT.parent / f"data_lake_handoff_{today}.zip"
        else:
            output = Path(args.zip)
        return run_zip(output)


if __name__ == "__main__":
    sys.exit(main())