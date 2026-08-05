# 新增省份 Extractor 落地指南

> **目的**：5 步接入新省份（31 省 + 深圳市），worker 自动识别可解析省份。
> **范围**：本文档只描述新增落地，不讨论 sc/cq 既有实现。

---

## 1. 双胞胎目录陷阱（先看这条）

仓库里有两份**独立维护**的 extractor 框架，**不是 symlink、不是同一文件**：

| 目录 | 实际被谁加载 | 内容 |
|---|---|---|
| `quota/parser/external/quota_md_to_csv_v2/` (underscore) | **pipeline 真实 import** — `config.py:103` `QUOTA_MD_TO_CSV_DIR`；`pipeline.py:118` `importlib.util` 动态加载；worker `_guard_has_parser:693` 扫这个目录 | ~719 行,库版 + CLI 双模式 |
| `quota/parser/external/quota-md-to-csv-v2/` (hyphen) | **CLI / 技能入口**，`subprocess.run` 转发（`extract_quota.py:379`） | ~471 行,技能版独立维护 |

**两边内容有差异**（行数差 ~250 行），是有意识维护的两套入口，不是 git/symlink 同步。每次新增省必须**手动 cp 双份**。建议加 pre-commit hook 自动 diff 提示（待办）。

**加载方向固定**：underscore = 唯一会被 worker / pipeline 路径触达的目录。
- 缺 underscore → worker 守卫 `_guard_has_parser` 返 False → job 走 `skipped_no_parser` 终态（archive.parse_status 标 `'skipped_no_parser'`，见 `quota_parser_worker.py:735`），不影响入库。
- 缺 hyphen → CLI 直接调 `quota-md-to-csv-v2/extract_quota.py` 会 FileNotFoundError，但**不影响 worker 跑批**（worker 不走 hyphen 路径）。

---

## 2. 5 步流程

### Step 1 — 选 short code

沿用车牌简称（豫=yu / 鄂=hu / 湘=xi / 陕=snx 区别晋=sx）。**避开 sc/cq**。必须在 32 项 `_UPLOAD_PROVINCE_MAP` 里（与上传入口、worker 守卫、API schema 三方对齐）。

> ⚠️ **关键词不要用短名**：PROVINCE_KEYWORDS 走 `_resolve_province`（`pipeline.py:42-91`）的 stage 3 反查，`dict.items()` 按声明顺序遍历、`s in keywords` 命中即返回。短名（川 / 渝 / 豫 / 鄂 等）会被 PDF 内正文撞车，导致误判省份。如 x 省的 keyword 含"川"，会被先注册 sc 抢走。**至少 1 个全名（"湖北"），可加 1 个全名简称（"鄂"），但短名禁止做 keyword**。

### Step 2 — 改 4 处注册表

| # | 文件 | 改什么 |
|---|---|---|
| 2.1 | `quota/parser/quota_parser/config.py` L68-83 | `PROVINCE_KEYWORDS` (L68-74) + `PROVINCE_NAMES` (L76-79) + `PROVINCE_DEFAULT_KEY` (L83) |
| 2.2 | `quota/parser/external/quota_md_to_csv_v2/extract_quota.py` L92-100 | 同 2.1（无 `default` sentinel） |
| 2.3 | `quota/parser/external/quota-md-to-csv-v2/extract_quota.py` L68-76 | 同 2.2 |
| 2.4 | `file_asset_service/app/quota_api.py` L1576-1581 | `{"sichuan", "chongqing"}` 硬编码 profile 白名单 → 改为 `_VALID_PROFILES`（从 `_UPLOAD_PROVINCE_MAP` 派生，32 省含新省）。**漏改此条**:即使 2.1-2.3 全改,`POST /parse` 带 `profile=guangdong` 仍被 422 INVALID_PROFILE 拒绝（worker 触发解析路径会卡在此处）。 |
| 2.5 | `file_asset_service/app/quota_parser/service.py` L33 / L121 / L522 | `PROFILES = ("sichuan", "chongqing")` 硬编码白名单（L121/L522 两处 check 用同一变量）→ 改为从 `quota_api._VALID_PROFILES` 派生（与 2.4 共用单一真源，32 省）。**漏改此条**：即使 2.4 通过，仍在 `_trigger(archive, profile=...)` 内抛 `ValueError: profile 'x' 不在注册表 ['sichuan', 'chongqing']`，web 端转 422；错误消息"不在注册表"区别于 2.4 的"必须为 ... 或省略"。 |

```python
# config.py (顶层 — 含 default sentinel)
PROVINCE_KEYWORDS = {
    "sc": ("四川", "川建"),
    "cq": ("重庆",),
    "default": (),                      # v0.4 sentinel
    "x":  ("湖北", "鄂"),                 # 新增
}
PROVINCE_NAMES = {
    "sc": "四川",
    "cq": "重庆",
    "x":  "湖北",                        # 新增
}
PROVINCE_DEFAULT_KEY = "default"
```

> **关键词至少 1 个全名**（"湖北"），可加 1 个全名简称（"鄂"），**短名/单字禁止**。

### Step 3 — 写省份主脚本（双胞胎一次性建）

```bash
# 一次性建双胞胎目录 + 复制
mkdir -p quota/parser/external/quota_md_to_csv_v2/extractors/x
mkdir -p quota/parser/external/quota-md-to-csv-v2/extractors/x
cp quota/parser/external/quota_md_to_csv_v2/extractors/sc/extract_quota.py \
   quota/parser/external/quota_md_to_csv_v2/extractors/x/extract_quota.py
cp quota/parser/external/quota-md-to-csv-v2/extractors/sc/extract_quota.py \
   quota/parser/external/quota-md-to-csv-v2/extractors/x/extract_quota.py
# 改 SECTION_RE / LATEX_MAP / 数字格式 — 两份都要改
```

**两个目录必须都有 extract_quota.py** —— 任一缺：

- 缺 underscore → worker 守卫返 False → `skipped_no_parser`（不影响入库，但 parse_status 永远卡"未解析"）
- 缺 hyphen → CLI 调 `quota-md-to-csv-v2/extract_quota.py` 报 FileNotFoundError（不影响 worker）

**契约**（pipeline 唯一调到的接口，`pipeline.py` 走 importlib 动态加载）：

```python
def process_md_file(md_path: Path) -> tuple[list[list[str]], list[dict]]:
    """
    Returns:
        rows:   list[list[str]]，10 列 (类型|项目编码|名称|项目特征|计量单位|消耗量|基价|验证|标准换算|来源)
        issues: list[dict]，解析告警，每项含字段:
            - section_id  (str)  章节定位
            - project_id  (str)  项目编码
            - reason      (str)  失败原因
            - prefix      (str)  原始表前文本（"工作内容:... / 单位:..."）
            - html        (str)  原始 HTML 摘要（≤800 字）

    副作用: 无（薄壳负责写 CSV / issues.md 到 md_path 同目录）
    失败:   抛 QuotaParserError 子类（UnsupportedProvinceError / ProfileExecutionError 等）
            → worker 标 job='failed' + archive.parse_status='failed_user' / 'failed_permanent'
    """
```

> ⚠️ **类型列取值约束**：`段 / 定 / 工 / 料 / 配 / 机 / 综 / 主材`（8 个，详见 `quota_md_to_csv_v2/SKILL.md §5` L128-142）。写错会被下游 finalize 5 步固化到 xlsx，无法回退。
>
> ⚠️ **本契约不调 finalize**：5 步 finalize（`clean_empty_qty / drop_toc_sections / fill_work_content / space_split_materials / finalize_last_step`，见 `config.py:90-96`）由薄壳 `extract_quota.py` 在 `process_md_file` 顶部默认自动跑（`run_finalize=True`），新省不必关心。

### Step 4 — 双胞胎同步（如 Step 3 已 cp 过则跳过）

```bash
# 仅当 Step 3 只在一边建了 extractor 才需要；推荐 Step 3 一并建好
cp quota/parser/external/quota_md_to_csv_v2/extractors/x/extract_quota.py \
   quota/parser/external/quota-md-to-csv-v2/extractors/x/extract_quota.py
```

### Step 5 — 验证

**5.1 离线 CLI**（cwd 必须在 `quota/parser/external/quota_md_to_csv_v2/` 或 `quota-md-to-csv-v2/`，否则 `from extractors._common import ...` 会 ModuleNotFoundError）：

```bash
PY=/d/miniconda3/envs/file-asset/python.exe
cd quota/parser/external/quota_md_to_csv_v2
$PY extract_quota.py --list-province  # 必须有 x
$PY extract_quota.py <md> --province x --keep-csv --no-finalize
# 检查 <md>同目录/<stem>_待审核.csv（UTF-8 BOM，Excel 双击不乱码）
```

> `--no-finalize` 显式跳过 5 步 finalize 流水线，只看 extractor 裸输出，便于隔离问题。**默认是开 finalize**，验证完记得去掉。

**5.2 在线 worker**：

```bash
bash file_asset_service/scripts/run_quota_parser_worker.sh restart
# 上传 x 省 PDF（province=x）→ worker claim → _guard_has_parser('x') 返 True → 跑 pipeline
# 验证 DB（SQLAlchemy 2.0 风格）:
$PY -c "
from app.database import get_engine
from sqlalchemy import text
with get_engine().connect() as c:
    print(c.execute(text('''
        SELECT parse_status, parse_phase, candidate_xlsx_key
        FROM archive WHERE title='<x PDF title>'
    ''')).first())
"
# 期望: parse_status ∈ {'candidate_ready', 'qa_warning'}（StageAResult.status 直写）
#       parse_phase='stage_a', candidate_xlsx_key NOT NULL
#
# ⚠️ worker.py:456 写的是 result.status 直传:
#   parse_status = result.status if result.status != "failed" else "parsed"
# StageAResult.status 取值见 quota_parser/result.py:12:
#   "candidate_ready" | "qa_warning" | "failed"
# 正常成功路径下 archive 看到的是 "candidate_ready"，"parsed" 只在 fail override 时出现。
```

---

## 3. 撤销

```bash
rm -rf quota/parser/external/quota_md_to_csv_v2/extractors/x/
rm -rf quota/parser/external/quota-md-to-csv-v2/extractors/x/
# 3 处注册表删 entry，重启 worker
```

`archive.parse_profile` 字段保留（存的是 **profile 长名**如 `sichuan / chongqing / beijing`，不是 short code——详见 `quota_api._VALID_PROFILES`）。撤销后守卫重新走 `skipped_no_parser`（`_guard_has_parser` 检查 `extractors/<prov>/extract_quota.py` 不存在 → False），不报错。

---

## 4. 常见误区

| 现象 | 原因 |
|---|---|
| 改注册表但 worker 还是 skipped_no_parser | 99% 双胞胎只改 1 份。守卫扫 underscore。 |
| `UnsupportedProvinceError: 未知 province='x'. 接受: ...` | `_resolve_province`（`pipeline.py:89`）3 阶段都未命中。检查 `config.py:PROVINCE_KEYWORDS` 漏改或 keyword 太偏 |
| `POST /parse` 带新省 profile 被 422 INVALID_PROFILE | `quota_api.py:1577` 硬编码 `{"sichuan", "chongqing"}` 白名单未升级（Step 2.4）。改为 `_VALID_PROFILES`（line 1079，32 省已派生）。**漏改此条**会让 worker 触发解析路径返回 422，archive 落 `failed_user` |
| `trigger_parse` 抛 `ValueError: profile 'x' 不在注册表 ['sichuan', 'chongqing']` | `file_asset_service/app/quota_parser/service.py:33` 还有第二处硬编码白名单 `PROFILES = ("sichuan", "chongqing")`（Step 2.5）。L121/L522 两处 check 用同一变量。改为从 `quota_api._VALID_PROFILES` 派生（与 L1577 共用单一真源）。**漏改此条**即使 L1577 通过，仍在 `_trigger(archive, profile=...)` 内抛 422，错误消息是"不在注册表"（区别于 L1577 的"必须为 ... 或省略"） |
| worker claim 显示 `profile=sichuan` 但 archive 实际是 gd/x/yu | `quota_api.py:1599` 历史硬编码 `profile=... or "sichuan"` fallback（body.profile + archive.parse_profile 都空时）。gd/x/yu 等非 sc/cq 省份会被错误 fallback 成 sichuan → worker 跑错省 extractor → 必然失败。改为「profile 缺省时按 province 推 default profile」(`_UPLOAD_PROVINCE_MAP[province][2]`)，province 推不出时 422 PROVINCE_NO_PROFILE。 |
| pipeline 跑通但 CSV 空 | `extractors/x/extract_quota.py` 的 `SECTION_RE` 没匹配上 x 省章节格式。看 `--keep-csv` 产出的 `issues.md` |
| 类型列下游识别错 | 写错"类型"列取值（必须 8 选 1：`段 / 定 / 工 / 料 / 配 / 机 / 综 / 主材`，见 `quota_md_to_csv_v2/SKILL.md §5`） |

> **错误类名备忘**：pipeline 抛的 `QuotaParserError` 子类（`exceptions.py`）——`UnsupportedProvinceError` / `InvalidXlsxStructureError` → worker 标 `failed_user`；`ProfileExecutionError` / `InvalidPageRangeError` / `WorkdirNotWritableError` → worker 标 `failed_permanent`。**不是直接抛 `ValueError`**。

---

## 5. 关联

- `quota_md_to_csv_v2/SKILL.md §5` (L128-142) — 10 列字段语义（类型列取值约束）
- `quota_md_to_csv_v2/SPEC.md §3.2` — 类型列枚举（**注意：只列 7 个，缺"配"——以 SKILL.md 为准**）
- `quota_parser/config.py:68-83` — 顶层 PROVINCE_KEYWORDS / NAMES / DEFAULT_KEY
- `quota_parser/pipeline.py:42` — `_resolve_province`（3 阶段省份识别）
- `quota_parser/exceptions.py` — QuotaParserError 异常体系
- `quota_parser/pipeline.py:116` — `_import_md_extract_module`（动态加载薄壳）
- `quota_parser/quota_parser_worker.py:678` — `_guard_has_parser`（与 `_import_md_extract_module` 走同目录）