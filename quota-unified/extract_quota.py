#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_quota.py — 定额 Markdown → 多 sheet xlsx（薄壳入口，自动判断省份）

本文件是 quota-md-to-csv-v2 skill 的入口薄壳:
  1. 接收 MD 文件路径
  2. 自动判断省份（路径关键词 → MD 前段关键词 → --province 手动覆盖）
  3. 透明转发到 extractors/<prov>/extract_quota.py（每个省份独立子脚本）
  4. **默认**不再产 CSV：用 tempfile 收省子脚本的 CSV 结果，
     xlsx_writer 读 tempfile 生成多 sheet xlsx,然后 tempfile 自动清理。
     想要 CSV 用 `--keep-csv` 显式开。
  5. **默认**自动跑 finalize 6 步流水线（clean / drop_toc / fill /
     space_split / normalize_unit / finalize_last_step）原地覆盖写入 `<stem>_待审核.xlsx`，
     直接产出待人工审核的最终 xlsx。中间不再有"格式待审核/最终输出"阶段。
     想要只产生 raw xlsx（不跑 finalize）用 `--no-finalize`。

v0.2 函数级入口（Worker 使用）:
    from external.quota_md_to_csv_v2.extract_quota import process_md_file
    info = process_md_file(
        md_path=".../sample.md",
        work_dir=".../jobs/run1",
        province="sc",
        keep_csv=False,
        run_finalize=True,
    )

    返回 dict（含 xlsx_path / rows_count / sheet_names / finalize_steps 等）。
    失败抛 FileNotFoundError / ValueError / RuntimeError。

支持省份（在 PROVINCE_KEYWORDS 中声明 + extractors/<prov>/extract_quota.py 子脚本存在）：
  - sc（四川）  ← extractors/sc/extract_quota.py（v3 完整逻辑）
  - cq（重庆）  ← extractors/cq/extract_quota.py（三对应决策 + 人工识别 + 一般风险费 + v2.2 编码列跳过）

用法：
  python extract_quota.py <md_path>           # 默认：xlsx + autofinalize，无 CSV
  python extract_quota.py <md_path> --keep-csv  # 保留 <stem>_待审核.csv
  python extract_quota.py <md_path> --no-finalize  # 只产 raw xlsx 不格式化
  python extract_quota.py <md_path> --no-xlsx  # 仅产 CSV（人工调试）
  python extract_quota.py <md_path> --province sc
  python extract_quota.py <md_path> --xlsx path/to/manual.xlsx
  python extract_quota.py --list-province     # 列当前可用省份

退出码：
  0  → 成功
  1  → MD 文件不存在 / 不是文件
  2  → 自动判断省份失败（让用户 --province 手动指定；或新省份走"复用 vs 新写"决策）
  3  → extractors/<prov>/extract_quota.py 子脚本不存在
        → 这是一种"省份已声明但子脚本未到位"的临时状态
  4+ → 由各省 extract_quota.py 返回
  5  → xlsx 生成失败
  6  → finalize 6 步中某一步失败（哪一步失败由子脚本 stderr 自报）

新省份决策（强制约束，详见 SKILL.md §11）：
  - 必须新建 extractors/<新 prov>/extract_quota.py，**不允许**直接调用已有省份子脚本作为代理
  - 复用现有省份实现：cp extractors/<近>/extract_quota.py extractors/<新>/extract_quota.py 后改
  - 新写提取脚本：选最相似省份 cp 当模板，做更彻底的改写

最终产物：
  默认：<MD 同目录>/<stem>_待审核.xlsx（含定额条目/册说明/各章 sheet；段行加粗、
                                         段行 C-D 合并、4 级分组已套上）
  + --keep-csv：同目录还多出 <stem>_待审核.csv 与 <stem>_待审核_issues.md（若省子脚本写了）
  + --no-finalize：<stem>_待审核.xlsx 是 raw xlsx（未跑 6 步）
  + --no-xlsx / --xlsx OTHER：CSV 落到 <stem>_待审核.csv，xlsx 走自定义路径
"""
from __future__ import annotations

import argparse
import importlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# ── 兼容两种调用方式 ──
# 1. 当脚本方式运行（cwd = HERE）时, extractors/ 自动可发现
# 2. 当作为 Python 包被 import 时（cwd 在仓库根），
#    需要把 HERE 加进 sys.path 才能 import extractors._common
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from extractors._common import narrative_parser, xlsx_writer  # noqa: E402 必须在 sys.path 设置后

# 省份 → 关键词列表（先匹配前面的 = 优先级高）
PROVINCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sc": ("四川", "川建"),   # "川建" 兜底（部分老 PDF 路径用简称）
    "cq": ("重庆",),          # 重庆没有简称，路径必带"重庆"
    "gd": ("广东", "粤"),     # v0.13 已落地 extractors/gd/
    "hu": ("湖北", "鄂"),     # hu 提取器已落地 extractors/hu/
    "bj": ("北京", "京"),     # v0.15 已落地 extractors/bj/ (北京消耗量定额)
}

PROVINCE_NAMES: dict[str, str] = {
    "sc": "四川",
    "cq": "重庆",
    "gd": "广东",
    "hu": "湖北",
    "bj": "北京",
}

EXTRACTORS_DIR = HERE / "extractors"


# ──────────────────────────────────────────────────────────────────
# 省份自检 + 错误决策提示
# ──────────────────────────────────────────────────────────────────
def list_existing_provinces() -> list[str]:
    """列出 extractors/ 下**实际有** extract_quota.py 的省份 code（按字母排序）。

    只有声明在 PROVINCE_KEYWORDS 且子脚本存在的省份才是真正可调用的省份。
    """
    if not EXTRACTORS_DIR.exists():
        return []
    out: list[str] = []
    for d in sorted(EXTRACTORS_DIR.iterdir()):
        if d.is_dir() and (d / "extract_quota.py").exists():
            out.append(d.name)
    return out


def print_decision_hint(*, problem: str, detected_keywords: list[str] | None = None,
                        declared_but_missing: str | None = None) -> None:
    """打印"复用 vs 新写"决策提示。

    三类调用场景：
    1. detected_keywords: 自动识别找不到省份，给出"复用 vs 新写"决策
    2. declared_but_missing: PROVINCE_KEYWORDS 里有某省份 code 但子脚本不存在
                           （常见：刚加 PROVINCE_KEYWORDS entry 但忘记 cp 子脚本）
    3. 默认：仅打印当前可用省份清单
    """
    existing = list_existing_provinces()

    print(f"[ERROR] {problem}", file=sys.stderr)
    print("", file=sys.stderr)

    if detected_keywords is not None:
        print(f"  路径与 MD 前段已搜关键词: {', '.join(repr(k) for k in detected_keywords)}",
              file=sys.stderr)
        print(f"  均未命中现有省份关键词（{', '.join(PROVINCE_KEYWORDS.keys())}）",
              file=sys.stderr)
        print("", file=sys.stderr)

    if declared_but_missing:
        print(f"  PROVINCE_KEYWORDS 已声明省份 '{declared_but_missing}', 但",
              file=sys.stderr)
        print(f"    extractors/{declared_but_missing}/extract_quota.py 不存在",
              file=sys.stderr)
        print("", file=sys.stderr)
        print(f"  ⚠ 必须先建子脚本, 才能调用（即使规则与某省份相近）", file=sys.stderr)
        print("", file=sys.stderr)

    print("  当前可用省份（声明 + 子脚本就位）:", file=sys.stderr)
    if existing:
        for p in existing:
            name = PROVINCE_NAMES.get(p, "?")
            print(f"    - {p} ({name}): extractors/{p}/extract_quota.py", file=sys.stderr)
    else:
        print(f"    （空）", file=sys.stderr)
    print("", file=sys.stderr)

    # 决策提示
    print("  这是一个新省份吗?你有两条路:", file=sys.stderr)
    print("", file=sys.stderr)
    print("  [A] 复用现有省份实现 → 复制 + 修改", file=sys.stderr)
    print(f"      1) 选最相似的现有省份复制:", file=sys.stderr)
    print(f"         cp extractors/<近>/extract_quota.py extractors/<新 code>/extract_quota.py",
          file=sys.stderr)
    print(f"      2) 在 PROVINCE_KEYWORDS / PROVINCE_NAMES 加新 entry",
          file=sys.stderr)
    print(f"      3) 按新省份差异改 extractors/<新 code>/extract_quota.py",
          file=sys.stderr)
    print(f"      4) v2/SPEC.md 加 §X「<省>差异」; v2/README.md 同步「差异表」",
          file=sys.stderr)
    print("", file=sys.stderr)
    print("  [B] 新写提取脚本 → 复制当模板, 但做更彻底改写", file=sys.stderr)
    print(f"      步骤同 [A], 但 extractors/<新 code>/extract_quota.py 是从头改起的",
          file=sys.stderr)
    print(f"      （而不是从某个省份微调; 适合规则跟任何省份都差很多的样本）",
          file=sys.stderr)
    print("", file=sys.stderr)
    print("  ⚠ 强制约束:", file=sys.stderr)
    print("    - 不允许直接调用已有省份的子脚本作为代理（即使规则相同）",
          file=sys.stderr)
    print("    - 每个新省份必须有自己独立的 extractors/<prov>/extract_quota.py",
          file=sys.stderr)
    print("", file=sys.stderr)

    print("  详细流程见 SKILL.md §11「新增省份流程」", file=sys.stderr)
    print("", file=sys.stderr)

    # 给现有省份作为 --province 候选
    if existing:
        print(f"  快速跳过决策（如果当前 MD 本来就属于已有省份）:",
              file=sys.stderr)
        print(f"    --province {' | '.join(existing)}", file=sys.stderr)


def detect_province(md_path: Path) -> str | None:
    """省份自动识别：先路径关键词，再读 MD 前 5KB 找关键词。

    返回:
      - 省份 code（"sc" / "cq"）：正常识别
      - 特殊字符串 "MISSING:<prov>:<kw>"：路径/MD 中识别到了省份 keyword，但子脚本未建
      - None：识别完全失败（让用户 --province 手动指定）
    """
    path_str = str(md_path)

    # 1. 路径关键词（最可靠，零 IO）
    for prov, kws in PROVINCE_KEYWORDS.items():
        for kw in kws:
            if kw in path_str:
                if not (EXTRACTORS_DIR / prov / "extract_quota.py").exists():
                    return f"MISSING:{prov}:{kw}"
                return prov

    # 2. MD 前 5KB
    try:
        head = md_path.read_text(encoding="utf-8", errors="replace")[:5000]
        for prov, kws in PROVINCE_KEYWORDS.items():
            for kw in kws:
                if kw in head:
                    if not (EXTRACTORS_DIR / prov / "extract_quota.py").exists():
                        return f"MISSING:{prov}:{kw}"
                    return prov
    except Exception as e:
        print(f"[WARN] 读 MD 前段失败: {type(e).__name__}: {e}", file=sys.stderr)

    return None


# ──────────────────────────────────────────────────────────────────
# finalize 6 步流水线（subprocess 调用同目录的 5 个脚本）
# ──────────────────────────────────────────────────────────────────
FINALIZE_SCRIPTS = [
    "clean_empty_qty.py",
    "drop_toc_sections.py",
    "fill_work_content.py",
    "space_split_materials.py",
    "normalize_unit.py",  # v0.1 第 6 步: 计量单位字面规范化
    "to_xlsx.py",
]


def run_finalize_pipeline(xlsx_path: Path,
                          finalize_dir: Path,
                          *,
                          verbose: bool = True) -> int:
    """依序跑 5 个 finalize 脚本，全部原地覆盖同一个 xlsx 文件。

    Returns:
        0  全部成功
        非零 第一个失败的脚本返回码（已自动 stderr 报错）
    """
    if not finalize_dir.is_dir():
        print(f"[ERROR] finalize 脚本目录不存在: {finalize_dir}", file=sys.stderr)
        return 1

    for fname in FINALIZE_SCRIPTS:
        # 第 6 步已重命名为 finalize_last_step.py
        if fname == "to_xlsx.py":
            fname = "finalize_last_step.py"
        script = finalize_dir / fname
        if not script.exists():
            print(f"[ERROR] finalize 脚本缺失: {script}", file=sys.stderr)
            return 1
        cmd = [sys.executable, str(script), str(xlsx_path)]
        if verbose:
            print(f"[STEP] {fname}  {xlsx_path.name}")
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(
                f"[ERROR] {fname} 返回 {r.returncode}（失败前 xlsx 仍是上一次成功的结果）",
                file=sys.stderr,
            )
            return r.returncode
    return 0


# ──────────────────────────────────────────────────────────────────
# v0.2 函数级入口（Worker 使用）
# ──────────────────────────────────────────────────────────────────
def _load_province_module(prov: str):
    """动态 import extractors/<prov>/extract_quota.py 模块。"""
    if prov not in PROVINCE_KEYWORDS:
        raise ValueError(
            f"未知省份 code: {prov!r};"
            f"已声明: {', '.join(PROVINCE_KEYWORDS.keys())}"
        )
    script = EXTRACTORS_DIR / prov / "extract_quota.py"
    if not script.exists():
        raise FileNotFoundError(
            f"省份 '{prov}' 已声明, 但子脚本不存在: {script}"
        )
    # 把 extractors/ 父目录加进 sys.path（province 模块顶层 from bs4 import …）
    pkg_root = HERE  # quota_md_to_csv_v2/
    parent = pkg_root.parent
    parent_str = str(parent)
    if parent_str not in sys.path:
        sys.path.insert(0, parent_str)
    # province 子脚本顶层有 import extractors._common ... 必须把 quota_md_to_csv_v2/
    # 当成 'extractors' 的兄弟包根使用,所以用 importlib 按文件加载更稳。
    module_name = f"_extractors_{prov}_extract_quota"
    spec = importlib.util.spec_from_file_location(
        module_name, str(script),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法创建 spec for {script}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def process_md_file(
    *,
    md_path: str | Path,
    work_dir: str | Path,
    province: str,
    keep_csv: bool = False,
    run_finalize: bool = True,
    finalize_dir: str | Path | None = None,
) -> dict[str, Any]:
    """v0.2 函数级入口：MD → 多 sheet xlsx（含可选 autofinalize 6 步）。

    不依赖 subprocess;直接 import province 子脚本 + Python 调用 finalize 步骤。

    Args:
        md_path:    含 HTML <table> 的 .md 文件路径
        work_dir:   任务工作目录（函数会自己建子目录）
        province:   省份 code（'sc' / 'cq' / …）
        keep_csv:   是否保留 CSV 中间产物（默认 False, 用 tempfile 收）
        run_finalize: 是否跑 autofinalize 6 步（默认 True）
        finalize_dir: finalize 脚本所在目录（默认 = 同包下 quota_csv_finalize/）

    Returns:
        dict:
            {
                "province": str,
                "md_path": str,
                "work_dir": str,
                "csv_path": str | None,           # 临时或持久
                "issues_md_path": str | None,
                "xlsx_path": str,                 # 最终多 sheet xlsx
                "finalize_steps": list[str],      # 跑了哪几步
                "rows_count": int,
                "sections_count": int,
                "issues_count": int,
                "sheet_names": list[str],
                "n_rows_quota": int,
                "n_chapters": int,
                "preface_chars": int,
                "warnings": list[str],
            }

    Raises:
        FileNotFoundError: MD / 子脚本缺失
        ValueError: 未知省份
        RuntimeError: 子脚本解析失败 / finalize 失败
    """
    md_p = Path(md_path).resolve()
    if not md_p.exists():
        raise FileNotFoundError(f"MD 文件不存在: {md_p}")
    if not md_p.is_file():
        raise FileNotFoundError(f"不是文件: {md_p}")

    work_dir_p = Path(work_dir).resolve()
    work_dir_p.mkdir(parents=True, exist_ok=True)

    # ── 解析省份 → import 子脚本 → process_md_file ──
    prov_mod = _load_province_module(province)
    try:
        rows, issues = prov_mod.process_md_file(md_p)
    except Exception as e:
        raise RuntimeError(
            f"省份 '{province}' 子脚本解析失败: {type(e).__name__}: {e}"
        ) from e

    if not isinstance(rows, list):
        raise RuntimeError(
            f"省份 '{province}' 子脚本 process_md_file 返回异常: rows type={type(rows)}"
        )
    issues = issues or []

    n_sec = sum(1 for r in rows if len(r) > 0 and r[0] == "段")
    n_proj = sum(1 for r in rows if len(r) > 0 and r[0] == "定")

    # ── 写 CSV ──
    if keep_csv:
        csv_target = work_dir_p / f"{md_p.stem}_待审核.csv"
    else:
        # 用 NamedTemporaryFile 在 work_dir 下建临时文件,结束后由调用方清理
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"_extract_quota_{md_p.stem}_", suffix=".csv",
            dir=str(work_dir_p), delete=False,
        )
        tmp.close()
        csv_target = Path(tmp.name)
    with csv_target.open("w", encoding="utf-8-sig", newline="") as f:
        import csv as _csv
        _csv.writer(f).writerows(rows)

    # ── 写 issues.md ──
    issues_md_path: Path | None = None
    if issues:
        issues_md_path = work_dir_p / f"{md_p.stem}_待审核_issues.md"
        with issues_md_path.open("w", encoding="utf-8") as f:
            f.write("# 解析问题报告\n\n")
            f.write(f"> 来源: {md_p.name}\n")
            f.write(f"> 省份: {province} ({PROVINCE_NAMES.get(province, '?')})\n")
            f.write(f"> 生成时间: {datetime.now(timezone.utc).isoformat()}\n\n")
            f.write("---\n\n")
            for i, issue in enumerate(issues, 1):
                f.write(f"## 异常表 {i}\n\n")
                f.write(f"- **章节定位**: `{issue.get('section_id', '-')}'`\n")
                f.write(f"- **项目编码**: `{issue.get('project_id', '-')}`\n")
                f.write(f"- **失败原因**: {issue.get('reason', '-')}\n")
                f.write(f"- **原始表前文本**: {issue.get('prefix', '-')}\n")
                f.write(f"- **原始 HTML 摘要**:\n  ```html\n  {issue.get('html', '')[:500]}\n  ```\n\n")

    # ── 写多 sheet xlsx ──
    xlsx_path = work_dir_p / f"{md_p.stem}_待审核.xlsx"
    md_text = md_p.read_text(encoding="utf-8", errors="replace")
    narrative = narrative_parser.parse_narrative(md_text)

    try:
        summary = xlsx_writer.write_quota_xlsx(
            csv_path=csv_target,
            xlsx_path=xlsx_path,
            narrative=narrative,
            issues_md_path=issues_md_path,
        )
    except Exception as e:
        raise RuntimeError(
            f"xlsx 写出失败: {type(e).__name__}: {e}"
        ) from e

    warnings = list(narrative.get("warnings") or [])

    # ── autofinalize 6 步 ──
    finalize_steps: list[str] = []
    if run_finalize:
        if finalize_dir is None:
            fd = HERE.parent / "quota_csv_finalize"
        else:
            fd = Path(finalize_dir).resolve()
        fd = fd.resolve()
        if not fd.is_dir():
            raise RuntimeError(f"finalize 脚本目录不存在: {fd}")

        for fname in FINALIZE_SCRIPTS:
            # 第 6 步已重命名为 finalize_last_step.py
            if fname == "to_xlsx.py":
                fname = "finalize_last_step.py"
            script = fd / fname
            if not script.exists():
                raise RuntimeError(f"finalize 脚本缺失: {script}")
            try:
                _run_finalize_step_inproc(script, xlsx_path)
            except Exception as e:
                raise RuntimeError(
                    f"finalize {fname} 失败: {type(e).__name__}: {e}"
                ) from e
            finalize_steps.append(fname)

    return {
        "province": province,
        "md_path": str(md_p),
        "work_dir": str(work_dir_p),
        "csv_path": str(csv_target),
        "issues_md_path": str(issues_md_path) if issues_md_path else None,
        "xlsx_path": str(xlsx_path),
        "finalize_steps": finalize_steps,
        "rows_count": len(rows),
        "sections_count": n_sec,
        "projects_count": n_proj,
        "issues_count": len(issues),
        "sheet_names": summary["sheet_names"],
        "n_rows_quota": summary["n_rows_quota"],
        "n_chapters": summary["n_chapters"],
        "preface_chars": summary["preface_chars"],
        "warnings": warnings,
    }


def _run_finalize_step_inproc(script: Path, xlsx_path: Path) -> dict:
    """用 importlib 执行单个 finalize 脚本的 process_xlsx()。

    每个 finalize 脚本顶层有 process_xlsx(input_path, output_path=None) -> dict;
    我们把脚本作为模块加载,直接调函数,不走 subprocess。
    """
    import importlib.util as _ilu

    module_name = f"_finalize_{script.stem}_{id(script)}"
    spec = _ilu.spec_from_file_location(module_name, str(script))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 finalize 脚本: {script}")
    mod = _ilu.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    if not hasattr(mod, "process_xlsx"):
        raise RuntimeError(f"finalize 脚本没有 process_xlsx(): {script}")
    return mod.process_xlsx(xlsx_path, None)  # 原地覆盖


# ──────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="定额 Markdown → 多 sheet xlsx（薄壳，自动判断省份并跑 6 步 finalize）"
    )
    ap.add_argument("md_path", nargs="?",
                    help="MD 文件绝对路径（含 HTML table）")
    ap.add_argument("--province", choices=["auto", *PROVINCE_KEYWORDS.keys()],
                    default="auto",
                    help="省份标识（默认 auto：路径 + MD 前段自动判断）")
    ap.add_argument("--list-province", action="store_true",
                    help="列当前可用省份（声明 + 子脚本就位），然后退出")

    # xlsx 输出
    ap.add_argument("--xlsx", default=None,
                    help="输出 xlsx 路径（默认 = <stem>_待审核.xlsx，与 MD 同目录）")
    ap.add_argument("--no-xlsx", action="store_true",
                    help="跳过 xlsx 生成（仅产 CSV，调试用）")

    # CSV 兜底（旧行为兼容）
    ap.add_argument("--keep-csv", action="store_true",
                    help="保留省子脚本产出的 CSV 与 issues.md；"
                         "默认用 tempfile 收 CSV 后清理")

    # finalize 流水线
    ap.add_argument("--no-finalize", action="store_true",
                    help="xlsx 写完后不跑 finalize 6 步（保留原始 raw xlsx）")
    ap.add_argument("--finalize-dir", default=None,
                    help="finalize 脚本所在目录（默认 = ../quota-csv-finalize）")
    args = ap.parse_args()

    # ── 调试入口：列省份 ──
    if args.list_province:
        existing = list_existing_provinces()
        if not existing:
            print("[WARN] extractors/ 下没有任何 extract_quota.py 子脚本")
        else:
            print(f"当前可用省份（{len(existing)} 个）:")
            for p in existing:
                name = PROVINCE_NAMES.get(p, "?")
                kws = PROVINCE_KEYWORDS.get(p, ())
                print(f"  {p} ({name})")
                print(f"    关键词: {', '.join(repr(k) for k in kws)}")
                print(f"    脚本:   extractors/{p}/extract_quota.py")
        sys.exit(0)

    # ── 必须有 md_path ──
    if not args.md_path:
        ap.error("需要 MD 文件路径，或加 --list-province")

    md_path = Path(args.md_path).resolve()
    if not md_path.exists():
        print(f"[ERROR] 文件不存在: {md_path}", file=sys.stderr)
        sys.exit(1)
    if not md_path.is_file():
        print(f"[ERROR] 不是文件: {md_path}", file=sys.stderr)
        sys.exit(1)

    # ── 决定省份 ──
    if args.province == "auto":
        prov_raw = detect_province(md_path)
        if prov_raw is None:
            # 列出所有 PROVINCE_KEYWORDS 关键词作为探测痕迹
            all_kws = [kw for kws in PROVINCE_KEYWORDS.values() for kw in kws]
            print_decision_hint(
                problem="无法自动判断省份（路径 / MD 前段都没命中现有省份关键词）",
                detected_keywords=all_kws,
            )
            sys.exit(2)
        if isinstance(prov_raw, str) and prov_raw.startswith("MISSING:"):
            # 格式: "MISSING:<prov>:<kw>"
            parts = prov_raw.split(":", 2)  # ["MISSING", "<prov>", "<kw>"]
            prov = parts[1] if len(parts) > 1 else "?"
            kw = parts[2] if len(parts) > 2 else "?"
            print_decision_hint(
                problem=f"自动匹配到省份 '{prov}'（关键词 '{kw}'）, 但子脚本未建",
                declared_but_missing=prov,
            )
            sys.exit(3)
        prov = prov_raw
        print(f"[OK] 自动判断省份: {prov} ({PROVINCE_NAMES[prov]})")
    else:
        prov = args.province
        print(f"[OK] 手动指定省份: {prov} ({PROVINCE_NAMES[prov]})")

    # ── 子脚本就位检查 ──
    script = EXTRACTORS_DIR / prov / "extract_quota.py"
    if not script.exists():
        print_decision_hint(
            problem=f"省份 '{prov}' 已声明在 PROVINCE_KEYWORDS, 但 extractors/{prov}/extract_quota.py 不存在",
            declared_but_missing=prov,
        )
        sys.exit(3)

    # ── 决定临时 / 持久 CSV 路径 ──
    #   --keep-csv → 写到 <stem>_待审核.csv（持久）
    #   默认 → 写到 NamedTemporaryFile（subprocess 完成后自动清理，除非失败）
    stem = md_path.stem
    persistent_csv = md_path.with_name(stem + "_待审核.csv")
    persistent_issues = md_path.with_name(stem + "_待审核_issues.md")

    if args.keep_csv:
        csv_target = persistent_csv
    else:
        # 用 NamedTemporaryFile 拿一个 tmp 路径；省子脚本会直接写这个绝对路径。
        # 用 suffix 保留 .csv，便于 xlsx_writer / process 识别。
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"_extract_quota_{stem}_", suffix=".csv",
            dir=str(md_path.parent), delete=False,
        )
        tmp.close()
        csv_target = Path(tmp.name)
        # 清理钩子：保证 finally 时删除；--keep-csv 模式不会到这里
        def _cleanup_tmp():
            try:
                if csv_target.exists():
                    csv_target.unlink()
            except OSError:
                pass
        import atexit
        atexit.register(_cleanup_tmp)

    # ── 分发到对应省份脚本（subprocess） ──
    cmd = [sys.executable, str(script), str(md_path), str(csv_target)]
    print(f"[OK] 转发到 {script.name}  → csv={csv_target.name}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(r.returncode)

    # 检查省子脚本的 issues.md（仅 --keep-csv 才有意义,但拿到路径不亏）
    if args.keep_csv and not persistent_issues.exists():
        persistent_issues = None

    # ── 多 sheet xlsx 合并输出 ──
    if args.no_xlsx:
        if args.keep_csv:
            print(f"[OK] CSV 保留: {persistent_csv}")
        else:
            print(f"[SKIP] --no-xlsx: 不生成 xlsx, csv 已写临时文件即将清理")
        # csv_target 已落临时位置,如需 --keep-csv 已在 persistent_csv
        # 但这里 csv_target 写到 tmp,没有 persistent_csv 路径 →
        # --no-xlsx 必与 --keep-csv 共存,否则 csv 也没持久副本。给警告。
        if not args.keep_csv:
            print(
                "[WARN] --no-xlsx 不带 --keep-csv 时 csv 也会被清理,"
                "若需持久 csv 请加 --keep-csv",
                file=sys.stderr,
            )
        sys.exit(r.returncode)

    try:
        # 决定 xlsx 落点
        if args.xlsx:
            xlsx_path = Path(args.xlsx).resolve()
        else:
            xlsx_path = md_path.with_name(stem + "_待审核.xlsx").resolve()

        md_text = md_path.read_text(encoding="utf-8", errors="replace")
        narrative = narrative_parser.parse_narrative(md_text)

        # issues.md 仅 --keep-csv 模式时附加;默认模式下省子脚本的 issues.md
        # 也只写到 tempfile 同目录（不是 persistent）,最终会被清理。
        issues_md_path = None
        if args.keep_csv and persistent_issues and persistent_issues.exists():
            issues_md_path = persistent_issues

        summary = xlsx_writer.write_quota_xlsx(
            csv_path=csv_target,
            xlsx_path=xlsx_path,
            narrative=narrative,
            issues_md_path=issues_md_path,
        )
        print(f"[OK] xlsx 多 sheet: {xlsx_path}")
        print(f"[OK]   册说明: {summary['preface_chars']} 字符")
        print(f"[OK]   定额条目: {summary['n_rows_quota']} 行")
        print(
            f"[OK]   章节 sheet: {summary['n_chapters']} 个"
            f"（{', '.join(summary['sheet_names'][2:])}）"
        )
        if narrative.get("warnings"):
            for w in narrative["warnings"]:
                print(f"[WARN] narrative: {w}", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] xlsx 生成失败: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(5)

    # ── finalize 6 步流水线 ──
    if not args.no_finalize:
        # finalize 脚本与 extract_quota 平级（同属 d:/.claude/skills/
        # quota-md-to-csv-v2/../quota-csv-finalize/）
        # 默认 ../quota-csv-finalize (相对本脚本)
        if args.finalize_dir:
            finalize_dir = Path(args.finalize_dir).resolve()
        else:
            finalize_dir = (HERE.parent / "quota_csv_finalize").resolve()
        print(f"[PIPELINE] 跑 finalize 6 步（{finalize_dir}）")
        rc = run_finalize_pipeline(xlsx_path, finalize_dir)
        if rc != 0:
            sys.exit(6)  # finalize 失败
        print(f"[DONE] 待人工审核: {xlsx_path}")
    else:
        print(f"[OK] 跳过 finalize（--no-finalize）")

    # ── --keep-csv 模式:把临时 csv 拷到 persistent 位置 ──
    if args.keep_csv and csv_target != persistent_csv:
        try:
            shutil.copy2(csv_target, persistent_csv)
            print(f"[OK] CSV 保留: {persistent_csv}")
        except Exception as e:
            print(f"[WARN] 复制 csv 到 {persistent_csv} 失败: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
