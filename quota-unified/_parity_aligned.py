#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内容对齐对比：提取"定"行序列 (pid, name)，diff 对齐后看真实子目差异。

逐行 index 对比受行数差级联影响（gd base 多 32 行 → 2 万行 index 全偏）。
定行序列对齐能分离"表/子目结构差异" vs "明细行 index 偏移"。
"""
import importlib.util
import sys
from difflib import SequenceMatcher
from pathlib import Path

QUOTA_UNIFIED = Path(__file__).resolve().parent
REPO_ROOT = QUOTA_UNIFIED.parent
sys.path.insert(0, str(QUOTA_UNIFIED))

import baseline  # noqa: E402
from profile_schema import get_preset_profile  # noqa: E402

FIXTURES = {
    "gd": "quota/parser/tests/guangdong/ocr/广东省_2018_房屋建筑与装饰工程定额_上册.md",
    "bj": "quota/parser/tests/beijing/ocr/北京市2021预算消耗量标准-仿古建筑工程.md",
    "sc": "quota/parser/tests/fixtures/sichuan/sample.md",
    "hu": "quota/parser/tests/hubei/ocr/湖北省建设工程公共专业消耗量定额及全费用基价表（2024）.md",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_orig(prov, md):
    mod = load_module(QUOTA_UNIFIED / "extractors" / prov / "extract_quota.py", f"orig_{prov}")
    rows, _ = mod.process_md_file(md)
    return rows


def run_base(prov, md):
    pname = "bj-2021" if prov == "bj" else f"{prov}-2018"
    profile = get_preset_profile(pname)
    ctx = baseline.FeatureContext(profile=profile)
    rows, _ = baseline.process_md_file(md, profile, ctx)
    return rows


def row_key(row):
    """定行 signature：(pid, name)。段行 signature：('段', id)。"""
    if row[0] == "定":
        return ("定", row[1], row[2])
    if row[0] == "段":
        return ("段", row[1], row[2])
    return None  # 明细行跳过


def extract_keys(rows):
    return [(i, row_key(r)) for i, r in enumerate(rows) if row_key(r) is not None]


for prov, rel in FIXTURES.items():
    md = (REPO_ROOT / rel).resolve()
    orig = run_orig(prov, md)
    base = run_base(prov, md)
    ok = [row_key(r) for r in orig if row_key(r) is not None]
    bk = [row_key(r) for r in base if row_key(r) is not None]
    sm = SequenceMatcher(None, ok, bk, autojunk=False)
    ops = sm.get_opcodes()
    n_equal = sum(b - a for tag, a, b, c, d in ops if tag == "equal")
    n_replace = sum(b - a for tag, a, b, c, d in ops if tag == "replace")
    n_delete = sum(b - a for tag, a, b, c, d in ops if tag == "delete")
    n_insert = sum(d - c for tag, a, b, c, d in ops if tag == "insert")
    print(f"===== {prov} =====")
    print(f"定/段行: orig={len(ok)} base={len(bk)}")
    print(f"  对齐 equal={n_equal}  replace={n_replace}  delete(orig独有)={n_delete}  insert(base独有)={n_insert}")
    # 展示 replace/delete/insert 的前几个
    shown = 0
    for tag, a, b, c, d in ops:
        if tag == "equal":
            continue
        for j in range(b - a) if tag in ("replace", "delete") else range(d - c):
            shown += 1
            if shown > 6:
                break
            o = orig[a + j] if (tag in ("replace", "delete") and a + j < len(orig)) else None
            bb = base[c + j] if (tag in ("replace", "insert") and c + j < len(base)) else None
            print(f"  {tag}: orig={o[1] if o else None} '{o[2][:30] if o else None}'  |  base={bb[1] if bb else None} '{bb[2][:30] if bb else None}'")
        if shown > 6:
            break
    print()
