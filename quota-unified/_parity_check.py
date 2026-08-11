#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时回归脚本：5 省 fixture 对比 orig extractor vs baseline。

对比对象：
  orig  = extractors/<prov>/extract_quota.py 的 process_md_file(md_path)
  base  = baseline.process_md_file(md_path, profile, ctx)

对比维度：
  - rows 逐行逐列 diff（前 N 条展示）
  - issues 数量

用法：python quota-unified/_parity_check.py
"""
import importlib.util
import sys
from pathlib import Path

QUOTA_UNIFIED = Path(__file__).resolve().parent
REPO_ROOT = QUOTA_UNIFIED.parent

sys.path.insert(0, str(QUOTA_UNIFIED))

import baseline  # noqa: E402
from profile_schema import get_preset_profile  # noqa: E402

FIXTURES = {
    "sc": "quota/parser/tests/fixtures/sichuan/sample.md",
    "gd": "quota/parser/tests/guangdong/ocr/广东省_2018_房屋建筑与装饰工程定额_上册.md",
    "hu": "quota/parser/tests/hubei/ocr/湖北省建设工程公共专业消耗量定额及全费用基价表（2024）.md",
    "bj": "quota/parser/tests/beijing/ocr/北京市2021预算消耗量标准-仿古建筑工程.md",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_orig(prov: str, md: Path):
    mod = load_module(
        QUOTA_UNIFIED / "extractors" / prov / "extract_quota.py",
        f"orig_{prov}",
    )
    rows, issues = mod.process_md_file(md)
    return rows, issues


def run_base(prov: str, md: Path):
    pname = "bj-2021" if prov == "bj" else f"{prov}-2018"
    profile = get_preset_profile(pname)
    ctx = baseline.FeatureContext(profile=profile)
    rows, issues = baseline.process_md_file(md, profile, ctx)
    return rows, issues


def diff_rows(orig, base):
    diffs = []
    n = max(len(orig), len(base))
    for i in range(n):
        a = orig[i] if i < len(orig) else None
        b = base[i] if i < len(base) else None
        if a != b:
            diffs.append((i, a, b))
    return diffs


def main() -> int:
    print(f"{'省':<4} {'orig行':>8} {'base行':>8} {'diff':>6} {'origIssues':>10} {'baseIssues':>10}")
    overall_fail = False
    for prov, rel in FIXTURES.items():
        md = (REPO_ROOT / rel).resolve()
        if not md.exists():
            print(f"{prov:<4} fixture 缺失: {md}")
            overall_fail = True
            continue
        orig_rows, orig_issues = run_orig(prov, md)
        base_rows, base_issues = run_base(prov, md)
        diffs = diff_rows(orig_rows, base_rows)
        ok = len(diffs) == 0
        overall_fail = overall_fail or not ok
        print(f"{prov:<4} {len(orig_rows):>8} {len(base_rows):>8} {len(diffs):>6} "
              f"{len(orig_issues):>10} {len(base_issues):>10}  {'✅' if ok else '❌'}")
        if diffs:
            for i, a, b in diffs[:5]:
                print(f"    row {i}:")
                print(f"      orig: {a}")
                print(f"      base: {b}")
            if len(diffs) > 5:
                print(f"    ... 共 {len(diffs)} 处 diff")
    print("---")
    print("PASS ✅" if not overall_fail else "FAIL ❌")
    return 0 if not overall_fail else 1


if __name__ == "__main__":
    sys.exit(main())
