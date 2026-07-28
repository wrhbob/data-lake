# quota-parser 脚本实现 SPEC（v0.2）

> 目标：把 `D:\工程造价学习\定额解析\` 改造为一个 **可被后台 Worker 调用的 Python 包**，
> 保持本地 CLI 入口继续可用，**业务规则不破坏**。
>
> 本 SPEC 描述“脚本侧”的契约，不约束网站侧数据模型/API；网站侧的 SPEC 见其他文档。

## 0. 文档元信息

- **状态**：Implementation Locked (v0.2)
- **基础约定**：[quota/README.md](../README.md) v0.2
- **配套文件**：`quota/parser/`（包源码）、`quota/parser/CLAUDE.md`（开发约定）

## 1. 目标与范围

### 1.1 目标

1. 将已有的离线流水线改造成可被 FastAPI Worker **常驻进程**调用的 Python 包。
2. 提供一个统一的同步入口 `run_quota_pipeline`。
3. 保留四川（`sichuan`）和重庆（`chongqing`）解析规则。
4. 提供清晰、可被 Worker 捕获的异常体系。
5. 保留本地 CLI 入口，方便开发与回归。

### 1.2 范围（v0.2 必做）

- PDF → OCR（MinerU）→ Markdown → 多 Sheet 候选 XLSX（`定额条目 / 册说明 / A/B/C/...`）。
- 候选 XLSX 一次成型时附带 **5 步 autofinalize**（已合并进 `extract_quota.py`，全部原地覆盖同一 xlsx）：
  1. `clean_empty_qty.py`
  2. `drop_toc_sections.py`
  3. `fill_work_content.py`
  4. `space_split_materials.py`
  5. `to_xlsx.py`
- 5 步只改 `定额条目` sheet；`册说明` 和各章 sheet 全程保留。
- CSV 中间产物默认不落盘（仅 `--keep-csv` 时落盘为 `<stem>_待审核.csv`）；worker 调度时不依赖 CSV。
- 人工审核：在 `定额条目` sheet 中直接编辑 Excel → 保存。
- Worker 上传 reviewed XLSX → 阶段 B 直接保存为 final XLSX，**不重跑 finalize**。
- Profile 注册与扩展机制（`sichuan` / `chongqing`）。
- Worker 常驻进程 + Windows 计划任务开机自启。
- 异常体系与自动 QA。

### 1.3 非范围

- 在线 XLSX/CSV 编辑器。
- 网站数据库写入、Archive/FileAsset 注册、MinIO 上传。
- 跨省通用解析器。
- 异步任务调度（消息队列、后台 worker 多实例、负载均衡）。
- 把解析结果导入 `quota_item` / `consumption` 等结构化数据库表。
- 换算规则 100% 自动填充。

## 2. 命名与术语

| 名词 | 含义 |
|---|---|
| Worker | 同一台 Windows 机器上的常驻 Python 进程 `quota_parser.worker` |
| OCR 服务 | MinerU 服务，默认 `http://172.16.20.23:8000` |
| Profile | 省级解析规则（`sichuan` / `chongqing`） |
| candidate | 阶段 A 输出：未经过人工审核的候选 XLSX |
| reviewed | 用户在本机审核后保存的 XLSX（上传时仅校验列数、Sheet 数与列名） |
| final | 最终交付 XLSX |
| Manifest | `task-result.json`：每次调用的结构化结果 |
| Workspace | 阶段 A 临时工作目录 `<work_root>/jobs/<parse_run_id>/` |

## 3. 业务流总览

```text
PDF
  ↓
[OCR]  MinerU 解析（自动分段、串行 1 任务）
  ↓
[Mark]  Markdown + result.json
  ↓
[Extract]  Profile.sichuan / Profile.chongqing
    ↓
    ①  CSV 临时（默认不落盘）
    ②  xlsx 多 Sheet 一次性写出：定额条目 / 册说明 / A / B / C / ...
    ③  autofinalize 5 步原地覆盖 xlsx：
        clean_empty_qty
        drop_toc_sections
        fill_work_content
        space_split_materials
        to_xlsx
  ↓
候选 XLSX（多 Sheet，Sheet1 = 定额条目）
  ↓
用户下载 → 打开 Excel → 在「定额条目」sheet 内核对/修改 → 保存为 reviewed XLSX
  ↓
上传 reviewed XLSX
  ↓
[Save]  阶段 B：直接落 final XLSX，不再重跑 finalize
  ↓
QA Report + Manifest
```

阶段 A：PDF → candidate（一次成型，已包含 autofinalize 5 步 + 自动 QA）。
阶段 B：reviewed → final（仅落盘 + QA，不再跑 finalize）。

## 4. 包结构与文件

```text
quota/parser/
├── SPEC.md                              ★ 本文档
├── CLAUDE.md                            开发与运行约定
├── pyproject.toml                       依赖与控制台脚本声明
├── src/quota_parser/
│   ├── __init__.py
│   ├── config.py                        路径/OCR URL/Work Root/Profile 注册
│   ├── result.py                        Result 数据类（StageAResult/StageBResult/Manifest）
│   ├── exceptions.py                    异常体系
│   ├── pipeline.py                      run_quota_pipeline 入口（含阶段 A 与阶段 B）
│   ├── ocr/
│   │   ├── client.py                    复用现有 skill 的同步客户端
│   │   └── chunk.py                     >100 页分段、断点续跑
│   ├── extract/
│   │   ├── base.py                      Profile 抽象基类
│   │   ├── _common/
│   │   │   ├── xlsx_writer.py          多 Sheet 写出（继承自现 extractors/_common）
│   │   │   └── narrative_parser.py     叙述段解析（继承自现 extractors/_common）
│   │   ├── sichuan.py                   四川抽取（继承自现有 v3 sc/）
│   │   └── chongqing.py                 重庆抽取（继承自现有 v3 cq/）
│   ├── finalize/
│   │   ├── clean_empty_qty.py           autofinalize 第 1 步（仅改定额条目 sheet）
│   │   ├── drop_toc_sections.py         autofinalize 第 2 步（仅改定额条目 sheet）
│   │   ├── fill_work_content.py         autofinalize 第 3 步（仅改定额条目 sheet）
│   │   ├── space_split_materials.py     autofinalize 第 4 步（仅改定额条目 sheet）
│   │   └── to_xlsx.py                   autofinalize 第 5 步（4 级分组 + 段行 C-D 合并 + 加粗）
│   ├── save/
│   │   └── reviewed_to_final.py         人工审核后直接保存（阶段 B）
│   ├── qa/
│   │   ├── qa.py                        自动 QA
│   │   └── report.py                    qa_report.json / qa_report.md
│   └── worker/
│       └── main.py                      quota_parser.worker 主入口
├── profiles/
│   ├── __init__.py                      注册表
│   ├── sichuan.py                       Profile 注册
│   ├── chongqing.py                     Profile 注册
│   └── _template.py                     Agent 适配模板
└── tests/
    ├── fixtures/                        测试样本 PDF/Markdown/已有 xlsx
    ├── test_extract_sichuan.py
    ├── test_extract_chongqing.py
    ├── test_finalize_pipeline.py
    └── test_worker_save.py
```

> **目录约定**：`quota/parser/` 改为正式可发布的 Python 包；
> 现有 `D:\工程造价学习\定额解析\.claude\skills\` 继续保留为本地 CLI 入口与开发工具，
> 但网站 Worker 只调用 `quota_parser.*` 的 Python API。
>
> **继承策略**：`extract/sichuan.py` 与 `extract/chongqing.py` 不重写现有 v3 业务逻辑，
> 只把当前 `extract_quota.py` 顶层主流程（含 autofinalize）拆成函数；subprocess 调
> 用将改为 import 调用。`xlsx_writer.py` 与 `narrative_parser.py` 直接迁入
> `extract/_common/`，行为零变更。

## 5. 公开接口

### 5.1 `run_quota_pipeline`（阶段 A）

```python
def run_quota_pipeline(
    *,
    pdf_path: str,
    work_dir: str,
    province: str,
    ocr_api_url: str = "http://172.16.20.23:8000",
    profile: str | None = None,
    enable_chunking: bool = True,
) -> StageAResult:
    """阶段 A：PDF → candidate XLSX（含 autofinalize 5 步 + 自动 QA）

    - pdf_path: PDF 本地路径（Worker 由 MinIO 临时下载得到）
    - work_dir: 任务独占目录（不含 parse_run_id 段，函数内部追加）
    - province: 'sichuan' | 'chongqing'
    - ocr_api_url: 局域网 OCR 服务
    - profile: 显式 Profile 名；None 时按 province 取默认
    """
```

#### `StageAResult`（dataclass）

```text
task_id: str
status: Literal["candidate_ready", "qa_warning", "failed"]
parser_version: str
profile: str
source_sha256: str
source_pdf_path: str
ocr_markdown_path: str | None
ocr_result_json_path: str | None
candidate_xlsx_path: str         ★ 多 Sheet：定额条目 / 册说明 / A / B / C / ...
candidate_xlsx_sha256: str
issues_md_path: str | None       # 当且仅当 province 子脚本在解析过程中
                                 # 产生至少一个 issue（表格解析失败 /
                                 # 关键列缺失 / 跨页表格拼接异常）时存在；
                                 # 其余情况为 None
qa_report_json_path: str
qa_report_md_path: str
warnings: list[str]
metrics: dict[str, int | str]
```

### 5.2 `finalize_reviewed_xlsx`（阶段 B）

```python
def finalize_reviewed_xlsx(
    *,
    reviewed_xlsx_path: str,
    output_xlsx_path: str,
    source_candidate_xlsx_path: str | None = None,
) -> StageBResult:
    """阶段 B：reviewed XLSX → final XLSX（直接保存，不再跑 finalize）"""
```

#### `StageBResult`（dataclass）

```text
task_id: str
status: Literal["final_ready", "qa_warning", "failed"]
parser_version: str
final_xlsx_path: str
final_xlsx_sha256: str
qa_report_json_path: str
qa_report_md_path: str
warnings: list[str]
metrics: dict[str, int | str]
```

### 5.3 `serve_worker`（常驻 Worker）

```python
def serve_worker(
    *,
    database_url: str,
    poll_interval_seconds: float = 3.0,
    ocr_api_url: str = "http://172.16.20.23:8000",
    work_root: str,
) -> None:
    """常驻 Worker：轮询数据库中状态为 pending/ready 的 parse_run，
    调用 run_quota_pipeline 或 finalize_reviewed_xlsx，
    写回产物与状态。

    实现要点：
      - 进程内全局互斥锁保证 OCR 并发 = 1。
      - 单进程模型：单个 Worker 实例顺序处理任务。
      - 任务级 workspace: <work_root>/jobs/<parse_run_id>/
      - 异常状态：
          - OcrUnavailableError/Transient → 自动重试 N 次（默认 2）
          - InvalidXlsxStructure → 标记 failed_user，需要人工干预
    """
```

> 数据库与网站侧表 schema 在网站侧 SPEC 中定义。本 SPEC 只规定 Worker 会调用
> `run_quota_pipeline` 与 `finalize_reviewed_xlsx`，不直接定义数据库 IO 细节。

#### `serve_worker` 当前实现状态（v0.2）

| 能力 | 状态 |
|---|---|
| 函数签名 + env 解析 + 健康检查打印 | ✅ 已实现（占位） |
| 数据库轮询（`SELECT ... FOR UPDATE SKIP LOCKED`） | ❌ 未实现 |
| 任务领取 + 状态机推进 | ❌ 未实现 |
| 产物上传 MinIO / 写 `parse_run` 表 | ❌ 未实现 |
| 重试 + 状态映射（transient/failed_permanent/failed_user） | ❌ 未实现 |
| Windows 计划任务注册 | ❌ 未实现 |

> **v0.2 占位说明**：当前 `serve_worker()` 仅读 env + 打印参数即返回，
> 不轮询、不调度、不上传。真实 Worker 主循环（轮询 → 任务领取 →
> 调用 `run_quota_pipeline`/`finalize_reviewed_xlsx` → 上传 MinIO → 更新 DB）
> 在 v0.2 P1 阶段补齐。开发接口契约在 §5.3，但接口调用方不应假定 v0.2
> 即可独立启动 Worker 完成完整端到端任务；联调前需要等待 P1。

### 5.4 入口控制台脚本

```text
quota-parser              # pyproject.toml [project.scripts]
quota-parser-worker       # pyproject.toml [project.scripts]
```

## 6. 数据契约

### 6.1 XLSX Schema（候选与最终 XLSX 共用）

sheet 名称和列结构来自现有 `extractors/_common/xlsx_writer.py` 行为，零变更继承：

```text
文件: candidate.xlsx / final.xlsx

Sheet1 名: 定额条目
  列数: 10（无 header；与现有 CSV schema 完全一致）
  列序: 类型 | 项目编码 | 名称 | 项目特征 | 计量单位 | 消耗量 | 基价/单价 | 验证 | 标准换算 | 标准换算来源
  行类型 enum: 段 | 定 | 工 | 料 | 配 | 机 | 综 | 主材

Sheet2 名: 册说明
  列数: 1
  内容: 单格 A1，preface 全文（含 ## 总说明 / ## 册说明 等合并）

Sheet3+ 名: <章字母>  （如 A、B、C、...；重复时按 _2/_3 后缀；31 字符截断）
  列数: 1
  内容: 章 A1 = 描述, A2 = 工程量计算规则
```

5 步 autofinalize 只改 "定额条目" sheet；"册说明" 和各章 sheet 全程不动。

CSV 中间产物默认不落盘（`tempfile + atexit`），仅在命令行携带 `--keep-csv` 时
落到 `<stem>_待审核.csv`。Worker 调度不依赖 CSV；如需 issue 列表，看 issues_md
而非 csv。

### 6.2 reviewed XLSX 校验（v0.2 选项 B）

上传 reviewed XLSX 时，仅校验 Sheet1 "定额条目"，其他 sheet 完全信任人工结果。

校验：

```text
- 文件能用 openpyxl 打开
- 含一个名为"定额条目"的 sheet（必须位于第 1 位）
- "定额条目" sheet 列数为 10
```

不允许：

```text
- 改 Sheet1 "定额条目" 名
- 改 Sheet1 列数或列顺序
- 在 Sheet1 列中加入新列
```

允许（完全以人工为准）：

```text
- 修改 Sheet1 任意单元格内容（数值、文本、类型字段、字体格式）
- 在 Sheet1 内增、删、重排行
- 修改"册说明"和任何章 sheet（A/B/C/...）的全部内容
- 在 final.xlsx 中按 reviewed.xlsx 的 sheet 顺序与命名原样保留
```

校验失败 → 阶段 B 直接抛 `InvalidXlsxStructureError`，Worker 标 failed_user。

`save/reviewed_to_final.py`：

```text
1. 校验 reviewed.xlsx Sheet1 结构
2. 用 reviewed.xlsx 的所有 sheet（含"册说明"、各章）原样覆盖 candidate.xlsx 的对应 sheet
3. candidate.xlsx 中存在但 reviewed.xlsx 中不存在的 sheet：
   - 若为"定额条目"，已用 reviewed 替换，无需处理
   - 若为"册说明"或各章，沿用 candidate 的原内容（保底，不阻塞）
4. 写入 final.xlsx
5. 算 SHA-256 + QA 报告
```

> 这是 v0.2 决策：以人工结果为主，stage B 不再二次处理任何 sheet。

### 6.2 Manifest（`task-result.json`）

```json
{
  "schema": "quota-parser-result/v1",
  "task_id": "qp_20260727_<run>",
  "phase": "stage_a",
  "status": "candidate_ready",
  "parser_version": "0.2.0",
  "profile": "chongqing",
  "province": "chongqing",
  "ocr_api_url": "http://172.16.20.23:8000",
  "source_pdf_sha256": "...",
  "candidate_xlsx_sha256": "...",
  "artifacts": [
    {"kind": "ocr_markdown",       "path": "ocr/source.md"},
    {"kind": "ocr_result_json",    "path": "ocr/result.json"},
    {"kind": "candidate_xlsx",     "path": "candidate/candidate.xlsx"},
    {"kind": "issues_md",          "path": "candidate/issues.md"},
    {"kind": "qa_report_json",     "path": "qa/qa_report.json"},
    {"kind": "qa_report_md",       "path": "qa/qa_report.md"}
  ],
  "metrics": {
    "pages": 436,
    "ocr_seconds": 920,
    "candidate_rows": 12580,
    "warnings": 3
  },
  "warnings": ["OCR detected low-confidence page 87"]
}
```

阶段 B 返回的 Manifest 形式对称，field `phase` = `stage_b`。

### 6.3 SHA-256 计算范围

- `source_pdf_sha256`：原 PDF
- `candidate_xlsx_sha256`：候选 XLSX
- `final_xlsx_sha256`：最终 XLSX

Worker 上传 MinIO 时将 SHA-256 写入 FileAsset；网站侧负责查重。

## 7. Province Profile

### 7.1 注册机制

```python
# src/quota_parser/profiles/__init__.py
from . import sichuan, chongqing

REGISTRY = {
    "sichuan":   sichuan.profile,
    "chongqing": chongqing.profile,
}

def get_profile(name: str) -> Profile:
    if name not in REGISTRY:
        raise UnsupportedProvinceError(name)
    return REGISTRY[name]
```

### 7.2 抽象基类

```python
# src/quota_parser/extract/base.py
class Profile(Protocol):
    name: str
    extract_to_candidate(markdown_path: str, work_dir: str) -> CandidateArtifacts:
        ...
```

### 7.3 模板

`profiles/_template.py` 提供给 Claude Agent 的适配模板（含输入输出契约、
关键章节正则、Sheet 分配方式）。Agent 修改规则时只动这个文件或新增
`profiles/<省>.py`。

### 7.4 扩展流程

```text
新增省份：
  1. 复制 _template.py → profiles/<省>.py
  2. 在 REGISTRY 注册
  3. 运行 profiles/<省>.py 提供的 fixture 回归
  4. 发布新版本
```

## 8. OCR 调用

### 8.1 默认地址

```python
# src/quota_parser/ocr/client.py
DEFAULT_OCR_API = "http://172.16.20.23:8000"
```

可通过环境变量覆盖：

```python
env override: QUOTA_PARSER_OCR_URL
```

### 8.2 复用现有 skill

`ocr/client.py` 通过 `importlib` 动态加载：

```text
D:/工程造价学习/定额解析/.claude/skills/mineru-pdf-parse/scripts/parse_pdf.py
D:/工程造价学习/定额解析/.claude/skills/mineru-pdf-parse/scripts/parse_chunked.py
D:/工程造价学习/定额解析/.claude/skills/mineru-pdf-parse/scripts/health_check.py
```

> Worker 进程启动时要求这些 skill 文件存在，不复制其源代码。
> 若未来需要更轻量打包，可以把这些 skill 的核心函数 refactor 入 `ocr/` 包中，
> 但 v0.2 阶段沿用 import 路径。

### 8.3 健康检查

Worker 启动时调用 `health_check.py`：
- 不健康 → 启动失败，异常 `OcrUnavailableError`
- 健康 → 进入轮询

### 8.4 大 PDF 分段

```text
page_count <= 100   : 走 parse_pdf.py 同步路径
page_count >  100   : 走 parse_chunked.py，分段 100 页/段
```

分段采用现有 skill 的实现，Worker 内部互斥锁保证 OCR 并发 = 1。

### 8.5 断点续跑

`ocr/chunk.py` 维护 `chunks.json`：
```json
{
  "chunks": [
    {"index": 1, "pages": [1, 100],  "status": "succeeded", "result": "..."},
    {"index": 2, "pages": [101, 200], "status": "running"},
    {"index": 3, "pages": [201, 300], "status": "pending"}
  ]
}
```

Worker 启动时检测到 `chunks.json` 存在 → 自动从失败段恢复。

## 9. autofinalize 5 步顺序（人工审核前的全处理）

`finalize/` 内部按下列**5 步固定顺序**执行（v2.1 以来约定，2026-07-27 新增
`drop_toc_sections.py`）。所有省份一致。

每一步**只改 `<stem>_待审核.xlsx` 中的"定额条目" sheet**；其他 sheet
（册说明、各章 A/B/C/...）全程保留。

```text
clean_empty_qty        # 删空消耗量行 + 完全空行
  ↓
drop_toc_sections      # 删"目录行误捕"段行（无下属子项）
  ↓
fill_work_content      # "定"行项目特征前向传播
  ↓
space_split_materials  # 料/配 行名称空格规整（5 条 han↔alnum 规则）
  ↓
to_xlsx                # 4 级 outline + 段行 C-D 合并 + 段行加粗
```

每一步单独提供函数入口（输入 `<xlsx> [output_xlsx]`，默认原地覆盖）。

`run_quota_pipeline` 内部按序调用这 5 步，所有省份一致，写入 candidate.xlsx。
`finalize_reviewed_xlsx` **不再调用**这 5 步，直接落 final.xlsx。

> 行为与现有 `extract_quota.py` autofinalize 完全一致；本 SPEC 不修改业务规则。

## 10. 人工审核保存模式

v0.2 决策：人工审核后不再重跑 finalize，**只保存**（选项 B）。

```text
candidate.xlsx（已含 autofinalize 5 步结果 + 多 Sheet）
  → 用户下载
  → 打开 Excel，可修改任意 sheet 的任意内容
  → 保存为 reviewed.xlsx（用户本机操作，不在本项目脚本控制下）
  → 上传
  → save.reviewed_to_final.reviewed_to_final_xlsx()  (选项 B)
      - 校验 Sheet1 "定额条目" 结构（10 列无 header、固定名、固定位）
      - 用 reviewed.xlsx 的全部 sheet 顺序与命名原样写入 final.xlsx
      - reviewed.xlsx 比 candidate.xlsx 少的 sheet（除"定额条目"）用 candidate 原内容保底
      - 计算 SHA-256
      - 写 QA 报告
```

> **关键约束（选项 B）**：
> - Sheet1 "定额条目" 名固定为 "定额条目"，列数为 10，列序固定。
> - 其他 sheet（册说明 + 各章 A/B/C/...）完全信任人工，可任意修改、增删。
> - 不重跑 autofinalize。

## 11. 异常体系

```text
QuotaParserError
├── OcrUnavailableError          # OCR 不健康/超时/服务不可达，Worker 重试
├── OcrTransientError            # 单段失败，Worker 按段重试
├── UnsupportedProvinceError     # profile 不存在，Worker 标 failed_user
├── InvalidPageRangeError        # PDF 页数异常，Worker 标 failed_permanent
├── InvalidXlsxStructureError    # reviewed.xlsx 列结构错，Worker 标 failed_user
├── ProfileExecutionError        # Profile 抽取失败，Worker 标 failed_permanent
└── WorkdirNotWritableError      # workspace 创建失败，Worker 标 failed_permanent
```

Worker 状态映射：

```text
transient(可自动重试)        : OcrUnavailableError / OcrTransientError
failed_permanent(自动重试无效): ProfileExecutionError / InvalidPageRangeError / WorkdirNotWritableError
failed_user(需要用户介入)    : UnsupportedProvinceError / InvalidXlsxStructureError
```

## 12. 自动 QA

`qa/qa.py` 在生成 final.xlsx 后执行：

### 12.1 检查项（选项 B）

```text
- 文件存在且能 openpyxl 打开
- 必含 sheet = "定额条目"（且必须在第 1 位），含 10 列无 header
- "定额条目" 行数与 candidate.xlsx 差异 > 15% → warning
- "定额条目" type 列只能出现 段 / 定 / 工 / 料 / 配 / 机 / 综 / 主材
- 段/定 行至少存在，若为 0 行 → warning
- 空 code 的行数 / 总行数 > 5% → warning
- final.xlsx SHA-256 与 manifest 一致
- 其他 sheet（册说明 + 各章 A/B/C/...）按 reviewed.xlsx 顺序保留；缺失内容保底
```

### 12.2 输出

`qa_report.json`：

```json
{
  "schema": "quota-parser-qa/v1",
  "task_id": "...",
  "summary": "ok | warning | failed",
  "checks": [
    {"name": "sheet_names", "status": "ok"},
    {"name": "row_type_enum", "status": "ok"},
    {"name": "row_drop_ratio", "status": "warning", "detail": "row_drop_ratio=0.18"}
  ]
}
```

`qa_report.md`：人类可读的对应版本。

## 13. Worker 进程与 Windows 计划任务部署

### 13.1 启动方式

```text
python -m quota_parser.worker
```

`worker/main.py` 入口读取环境变量：

```text
QUOTA_PARSER_DATABASE_URL   # 网站侧数据库 URL
QUOTA_PARSER_WORK_ROOT      # 例如 D:\quota-parser-jobs\
QUOTA_PARSER_OCR_URL        # 默认 http://172.16.20.23:8000
QUOTA_PARSER_POLL_INTERVAL  # 默认 3
```

### 13.2 开机自启

提供 `quota_parser.worker.install_scheduler()` 函数：
- Windows 计划任务名 `QuotaParserWorker`
- 触发器：用户登录时
- 操作：`python -m quota_parser.worker`
- 工作目录：项目根目录

调用：

```text
python -m quota_parser.worker --install-task      # 注册
python -m quota_parser.worker --uninstall-task    # 移除
```

### 13.3 进程内 OCR 互斥

```python
# quota_parser/worker/main.py
import threading

_OCR_LOCK = threading.Lock()

def _safe_run_pipeline(...):
    with _OCR_LOCK:
        return run_quota_pipeline(...)
```

> **单进程假设**：`_OCR_LOCK` 是 `threading.Lock`，仅在**单 Worker 进程内**
> 保证 OCR 并发 = 1（同一进程内多线程不会并发调用 MinerU）。
>
> **多进程场景警告**：如果未来扩到多 Worker 进程（即使单机），`threading.Lock`
> 跨进程失效，多个进程可能同时打 MinerU API，导致：
>   - 单进程显存争抢（OOM）；
>   - MinerU 服务端队列超出 `max_concurrent_requests` 配置；
>   - 任务处理顺序错乱（同一 PDF 可能被两个 Worker 抢到）。
>
> **多进程演进路径**（v0.2 不实施，记入 P2 候选）：
>   - 选项 A：DB advisory lock（`pg_try_advisory_lock(hash(worker_id))`），
>     简单可靠，与现有共享 PostgreSQL 共用；
>   - 选项 B：Redis `SET NX EX` 分布式锁；
>   - 选项 C：每 Worker 独占一个 OCR 服务实例（端口隔离），最重但最稳。
>
> v0.2 阶段默认部署 1 个 Worker 进程；多进程方案待实际扩缩容时再选型。

## 14. CLI 与回退开发

保留本地开发 CLI：

```text
# 阶段 A：本地手跑
quota-parser stage-a <pdf> --province chongqing --work-dir ./jobs/run1

# 阶段 B：本地手跑
quota-parser stage-b <reviewed.xlsx> --output ./jobs/run1/final.xlsx

# Worker 启动（开发）
quota-parser-worker
```

> 这些 CLI 与现有 `.claude/skills/.../脚本` 并存。新脚本是正式实现，
> 老 skill 脚本作为兼容与回归样本。

## 15. 依赖

| 包 | 用途 | DLSE 环境 |
|---|---|---|
| openpyxl 3.1.5+ | XLSX 读写 | 已装 |
| requests | MinerU HTTP | 已装 |
| PyMuPDF | 分段 | 已装 |
| beautifulsoup4 + lxml | Markdown 解析 | 已装 |
| python-multipart | 上传下载 | 新装 |
| psycopg[binary] 3.x | Worker 数据库轮询（与网站共用） | 已装 |

> 第一版不引入新依赖家族（Celery/Redis 等）。

## 16. 幂等 / 可重入 / 临时目录清理

### 16.1 幂等

- 阶段 A：可重跑，产物可覆盖；`chunks.json` 决定是否续跑。
- 阶段 B：不可重跑；如 reviewed.xlsx 已上传，再次上传将被拒绝。

### 16.2 Workspace 清理

清理策略分两档，**解决"任务结束清理"与"7 天兜底"看似矛盾的问题**：

```text
┌─────────────────────────────┬──────────────────────────────────┐
│ 任务正常完成（成功 / 失败） │ 立即 shutil.rmtree(<workspace>) │
│ （产物均已上传 MinIO + DB）  │ — 产物路径已不可丢失             │
├─────────────────────────────┼──────────────────────────────────┤
│ 任务异常中断（Worker 崩溃   │ workspace 暂留，下次 Worker 启动 │
│ / OOM / kill -9 / 宿主机    │ 时扫 mtime > 7 天的目录清理；   │
│ 掉电）                      │ 也可由外部 cron 兜底清理         │
└─────────────────────────────┴──────────────────────────────────┘
```

详细规则：

1. **正常路径**：任务结束后（不论 `candidate_ready` / `qa_warning` / 业务层 `failed`），
   Worker 确认产物（XLSX / qa_report / Manifest）已上传 MinIO 且 `parse_run` 表已
   写入 MinIO key 后，立即 `shutil.rmtree(<work_root>/jobs/<parse_run_id>/)`。

2. **异常路径**：Worker 被 SIGKILL / OOM / 宿主机掉电时没有机会执行清理，
   workspace 残留。**7 天**不是"正常任务保留时长"，而是"兜底清理阈值"——
   下次 Worker 启动时（或外部 cron）扫描 `work_root/jobs/`，对 `mtime > 7 天`
   的目录直接 `rmtree`。

3. **不清理的产物**：已被上传 MinIO + DB 登记的 `candidate.xlsx` / `final.xlsx` /
   `qa_report.json` / `qa_report.md` / `manifest.json` / `issues.md` /
   `result.json` 不受 workspace 清理影响，它们在 MinIO 上有独立生命周期。

4. **配置项**：`QUOTA_PARSER_WORK_ROOT_RETENTION_DAYS`（默认 7）允许运维调整
   兜底阈值；调小（3）能更快释放磁盘，调大（14）适合长期排查。

## 17. 安全与审计

- Worker 对 MinIO 的访问凭据从环境变量读取。
- 数据库写操作仅限 parse_run 表，不直接修改网站业务表（Archive/FileAsset 由网站侧在 Worker 上传成功后统一维护）。
- 任何异常都写入 `parse_run.error_code/error_message`，便于审计。

## 18. 与现有本地 skill 的改造清单

| 现有 skill 文件 | 改造方式 |
|---|---|
| `mineru-pdf-parse/scripts/parse_pdf.py` | 保持原样；`ocr/client.py` 通过 import 调用其同名函数 |
| `mineru-pdf-parse/scripts/parse_chunked.py` | 同上 |
| `mineru-pdf-parse/scripts/health_check.py` | 同上 |
| `quota-md-to-csv-v2/extract_quota.py` | 整体保留；其 `process_md_file()` / autofinalize 顶层入口**搬入** `pipeline.py.run_quota_pipeline()`，subprocess 串联改为同步函数调用 |
| `quota-md-to-csv-v2/extractors/sc/extract_quota.py` | 整体迁移到 `extract/sichuan.py`（包括 SECTION_RE / SECTION_RE2 / PROJECT_ID_RE 等），不改业务 |
| `quota-md-to-csv-v2/extractors/cq/extract_quota.py` | 整体迁移到 `extract/chongqing.py` |
| `quota-md-to-csv-v2/extractors/_common/xlsx_writer.py` | 直接迁入 `extract/_common/xlsx_writer.py`；多 Sheet 写出逻辑零变更 |
| `quota-md-to-csv-v2/extractors/_common/narrative_parser.py` | 直接迁入 `extract/_common/narrative_parser.py` |
| `quota-csv-finalize/clean_empty_qty.py` | 迁入 `finalize/clean_empty_qty.py`，函数化（输入 xlsx 输出 xlsx，仍仅改定额条目 sheet） |
| `quota-csv-finalize/drop_toc_sections.py` | 迁入 `finalize/drop_toc_sections.py` |
| `quota-csv-finalize/fill_work_content.py` | 迁入 `finalize/fill_work_content.py` |
| `quota-csv-finalize/space_split_materials.py` | 迁入 `finalize/space_split_materials.py` |
| `quota-csv-finalize/to_xlsx.py` | 迁入 `finalize/to_xlsx.py`，行为不变 |
| `quota-csv-finalize/advance_stage.py` | **不迁移**：v2 起已废弃，文件保留为 deprecated 不调 |
| 历史 4 子目录流程根 `D:\工程造价学习\数值审核流程\` | 全部废弃；采样时手工清旧样本 |
| 阶段包装参数 `--stage 数值待审核` / `--stage 格式待审核` / `--src-pdf` / `--process-root` | 全部废弃（已在原 skill 内删除）；新版仅保留必要 flag（`--keep-csv` 等） |

> **行为零变更原则**：以上迁移只搬代码不改业务。`extract_quota.py` 主流程的
> 等价语义必须能复现一份 SC/CQ 实测 XLSX，与现有 v2.1 输出在结构上完全一致。

## 19. 不在本 SPEC 范围

- 网站数据库/Archive/FileAsset 的具体注册流程（详见网站侧 SPEC）。
- 删除接口的设计（详见网站侧 SPEC）。
- Vite+React+Ant Design 重构（与本 SPEC 无关）。
- 跨省通用解析器。
- 多 GPU 并行 OCR。

## 20. 验证、回归与发版

### 20.1 验证

- 每个 Profile 配套 fixture：
  - 四川 1 本 PDF（p005–p150）
  - 重庆 1 本 PDF（p005–p150）
- 验证表：
  - `tests/test_extract_sichuan.py`
  - `tests/test_extract_chongqing.py`
  - `tests/test_finalize_pipeline.py`
  - `tests/test_worker_save.py`

### 20.2 发版

- 每次修改 Profile 或 finalize 任一步，需更新 `parser_version`。
- Manifest 中 `parser_version` 为追溯关键字段。
- 不发版的情况下不应改 Profile 代码。

---

**锁版本**：v0.2。后续破坏性变更（增加强制 Sheet、调整 finalize 顺序等）必须走新 SPEC。
