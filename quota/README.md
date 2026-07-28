# 定额 PDF → XLSX 网站化接入计划（v0.3）

> 本文记录"已有定额 PDF 转换为 XLSX"接入数据湖网站的工作边界、流程和已落实的决策。
>
> 当前负责人范围：**定额解析板块**。目标是把已有 PDF 经过 OCR、Markdown 解析、自动格式化，最终生成可下载的 XLSX。本文不把定额解析直接扩展为完整的定额数据库入库项目。

## 0. 文档组织

| 路径 | 内容 | 状态 |
|---|---|---|
| `quota/README.md`（本文） | 工作计划与跨文档视角（已落地决策） | v0.3 |
| `quota/parser/SPEC.md` | 解析脚本契约（实现规范） | v0.2, Implementation Locked |
| `quota/parser/tests/PARITY_REPORT.md` | v0.2 ↔ v0.1 baseline 行为对齐验证报告 | v0.2 完成，143,940 单元格 0 不匹配 |
| `quota/web-frontend/SPEC.md` | 网站前端契约（UI + HTTP API + 数据契约） | v0.2, Draft |

最新决策以两份 SPEC 为准；本文档在 SPEC 未覆盖的层面提供工作计划视角。SPEC 之间的字段对齐见 `quota/web-frontend/SPEC.md` §11。

## 1. 当前目标

将本地项目 `D:\工程造价学习\定额解析` 的离线流水线接入网站，使用户能够：

1. 在网站中选择已经登记的定额 PDF；
2. 创建解析任务；
3. 调用局域网中已经部署的 OCR/MinerU 服务；
4. 将 OCR 结果 Markdown 解析为 candidate XLSX（含多 Sheet：定额条目 / 册说明 / A/B/C...）；
5. 上传审核后的 reviewed XLSX；
6. reviewed XLSX 允许做任意修改：网站只校验 Sheet1「定额条目」结构（Sheet 名、列数、列序、首列 sheet 位置均固定）和行数差距是否过大；行数差距大只提示，不阻止提交，主要以人工结果为准；
7. 不再重跑 autofinalize（5 步：clean_empty_qty → drop_toc_sections → fill_work_content → space_split_materials → finalize_last_step 已包含在 candidate 中）；
8. 直接落盘为 final XLSX；
9. 在网站中下载 XLSX 和必要的质检报告。

第一阶段不要求开发在线 Excel 编辑器，但网站需要支持 reviewed XLSX 的下载、上传和轻量结构校验。解析完成后，结果需要写回网站并在定额页面展示；同时保留删除解析产物/解析结果的接口。

## 2. 计划中的业务流程

```text
已有定额 PDF
  ↓
网站档案中选择 PDF
  ↓
创建定额解析任务
  ↓
解析 Worker 获取 PDF
  ↓
调用局域网 OCR/MinerU 服务
  ↓
生成 Markdown / result.json
  ↓
省级 Profile 解析 Markdown
  ↓
生成 candidate XLSX（多 Sheet：定额条目 / 册说明 / A/B/C...）+ 异常报告
  ↓
下载 candidate XLSX → 本地 Excel 编辑 → 上传 reviewed XLSX
  ↓
直接落盘为 final XLSX（不再跑 autofinalize，按 parser SPEC §10 选项 B）
  ↓
自动 QA
  ↓
网站保存并提供 final XLSX 下载
```

### 2.1 暂时取消独立的格式人工审核

格式由脚本确定性生成，暂不设置单独的格式人工审核阶段。脚本仍必须生成自动 QA 报告，检查例如：

- XLSX 是否成功生成；
- Sheet 和列结构是否正确（Sheet1「定额条目」必须存在、首列 sheet、10 列无 header）；
- 输出行数是否异常；
- 定额编码和定额行是否存在；
- autofinalize 删除或修改的行数；
- 处理过程中产生的 warnings/errors。

"取消格式人工审核"不等于取消格式质量检查。

## 3. 两段式解析接口

> 权威细节见 `quota/parser/SPEC.md`（v0.2，Implementation Locked）。本节给出与 SPEC 对齐的概要。

### 阶段 A：PDF → candidate XLSX

输入：

- 已登记的 PDF；
- 省份、版本年、专业、分册等档案元数据；
- 解析 Profile（`sichuan` / `chongqing`）；
- OCR 服务地址；
- 本次任务工作目录。

处理：OCR → Markdown → 省级 Profile 抽取 → 5 步 autofinalize → 多 Sheet XLSX。

输出：

- OCR Markdown；
- OCR `result.json`；
- **candidate XLSX（多 Sheet：定额条目 / 册说明 / A/B/C...）**；
- 任务结果 Manifest（task-result.json）；
- 自动 QA 报告（`qa_report.json` + `qa_report.md`）。

阶段结束状态：`candidate_ready`（可下载、可进入人工审核）。

### 阶段 B：reviewed XLSX → final XLSX（选项 B，不再二次处理）

输入：

- candidate XLSX（阶段 A 产出）；
- 用户上传的 reviewed XLSX。

reviewed XLSX 的约束（v0.2 选项 B）：

- **唯一硬校验**：Sheet1 名必须为「定额条目」、列数必须为 10、列序固定、位于首列 sheet；其余 Sheet（册说明 + A/B/C...）完全信任人工结果；
- 除此之外，用户可以自由修改任意单元格内容（包括数值、文本、类型字段、字体格式）；
- 用户可以增、删、调整行顺序；
- 行数与 candidate XLSX 差异较大时只产生 warning，不阻断；
- 不重跑 autofinalize，直接落盘。

处理：按 parser SPEC §10 选项 B——以人工结果为主，不再二次处理任何 sheet。

输出：

- **final XLSX**（结构与 reviewed XLSX 一致；candidate 中存在但 reviewed 中缺失的 Sheet 用 candidate 原内容保底）；
- 自动 QA 报告；
- 解析产物 Manifest。

阶段结束状态：`qa_passed` / `failed_user`。

## 4. 网站与解析项目的职责边界

### 网站负责

- 档案登记和 PDF 选择；
- 创建、查询、重试、取消解析任务；
- 任务状态、阶段和错误展示；
- 从 MinIO 获取 PDF 并提供给 Worker；
- 保存 OCR、CSV、XLSX 和报告等产物；
- 保存原 PDF 与派生产物的关系；
- 提供 CSV 下载、审核后 CSV 上传和 XLSX 下载；
- 记录用户审核动作和审计信息。

### 解析项目负责

- OCR 服务调用；
- 大 PDF 分段和断点续跑；
- Markdown → CSV；
- 四川、重庆等省级 Profile 规则；
- 数值审核后的 CSV 清洗；
- XLSX 生成；
- 自动 QA；
- 返回结构化的任务结果和产物清单。

解析项目不直接操作网站数据库、Archive、FileAsset 或 MinIO。

## 5. 推荐运行方式

解析 Worker 最好与网站服务部署在同一台服务器/工作节点上，但当前代码库**没有定义一个独立的中央网站服务器地址**。现有部署文档描述的是：在某台运行 `file_asset_service/serve.py` 的电脑上启动本地 FastAPI 控制台，网站监听 `127.0.0.1:8010`，数据库和 MinIO 使用共享 NAS 服务。也就是说，当前实际形态更接近"运行控制台的工作站/服务器 + 共享 PostgreSQL/MinIO"，而不是已经明确的一台集中式 Web 服务器。

因此需要先确认部署拓扑：

```text
方案 A（当前最容易落地）：
运行 FastAPI 的工作站
  ├── FastAPI
  ├── quota-parser-worker
  └── 访问共享 PostgreSQL / MinIO / 局域网 OCR

方案 B（后续生产化）：
独立网站服务器
  ├── FastAPI
  └── 访问共享 PostgreSQL / MinIO

独立解析节点
  ├── quota-parser-worker
  └── 访问共享 MinIO / 局域网 OCR
```

在确认网站服务器位置之前，不能假定 `127.0.0.1:8010` 就是其他人员都能访问的地址；它目前只表示启动服务的那台电脑本机地址。Worker 如果部署在网站服务器上，必须确认该机器能够访问 OCR 服务、共享 MinIO 和共享 PostgreSQL，并且有足够的本地临时磁盘空间。

不在 FastAPI 请求线程中直接运行整本 PDF 解析。建议单独运行 `quota-parser-worker`：

```text
FastAPI 网站
  → 数据库创建 pending 解析任务
  → quota-parser-worker 领取任务
  → Worker 调用本地解析包和局域网 OCR 服务
  → Worker 更新任务状态并上传产物
```

OCR/MinerU 并发配置以本地项目 `.claude/skills/mineru-pdf-parse` 中的既有约束为准：同一 OCR 服务并发数固定为 1。CSV 清洗和 XLSX 生成可在后续单独优化。

## 6. 中间文件存储规划

### 长期保存：MinIO

按 `quota/web-frontend/SPEC.md` §6.1（Archive 字段 `candidate_xlsx_key` / `final_xlsx_key`）决定 MinIO 路径，约定按 `archive_id` 隔离（每个档案只保留最新一次解析结果，不存历史）：

```text
raw/
  quota/{archive_id}/source.pdf

extract/
  quota/{archive_id}/candidate/candidate.xlsx
  quota/{archive_id}/final/final.xlsx

report/
  quota/{archive_id}/manifest/manifest.json
  quota/{archive_id}/qa/qa_report.json
  quota/{archive_id}/qa/qa_report.md
```

> v0.1 不强制保留 OCR Markdown / result.json 作为长期产物（按 parser SPEC §16.2 临时目录清理规则 7 天）。需要审计时可由 parser 维护者调整。

### 短期计算：Worker 临时目录

Worker 只在任务执行期间使用本地临时目录，例如：

```text
<worker-root>/jobs/{parse_run_id}/
  input/
  ocr/
  candidate/
  reviewed/
  output/
```

任务产物上传并完成数据库登记后，应清理临时目录。

## 7. 定额页面展示和删除接口

解析完成后，最终 XLSX 及其相关产物不能只停留在 Worker 本地目录，必须注册回网站并在定额档案详情页展示：

- 原始 PDF；
- 解析状态（由 `archive.status` 驱动：`registered` / `parsing` / `parsed` / `qa_passed` / `usable` / `cancelled`）；
- candidate XLSX（下载按钮，仅 `parsed` 及之后状态可下载）；
- reviewed XLSX 上传入口（仅 `parsed` 状态可用）；
- final XLSX（下载按钮，仅 `qa_passed` 及之后状态可下载）；
- Manifest 摘要 + 完整查看链接（头部展示 Profile、`parser_version`、Task ID——`parser_version` 必须展示，用于排错）；
- QA 报告摘要 + 完整查看链接；
- 解析器 Profile 与版本；
- 异常 banner（`transient` / `failed_permanent` / `failed_user` 三类，按 parser SPEC §11 映射）。

详细 UI 行为与状态映射见 `quota/web-frontend/SPEC.md` §6.1 / §7。

网站需要提供删除接口，但删除应区分两类：

1. **删除某次解析结果**：删除对应 parse run 的派生产物及数据库关联，不删除原始 PDF 和档案；
2. **删除原始档案/PDF**：属于更高风险的档案操作，不能由普通"删除解析结果"接口隐式执行，需要独立权限和确认。

删除操作应保留审计记录，并处理数据库记录与 MinIO 对象之间的一致性。建议默认保留原始 PDF、候选 CSV 和审核后 CSV，只有明确的清理操作才删除中间产物。

## 8. 任务状态

复用 `Archive.status` 状态机（**不**新增独立 `parse_run` 表——每个 PDF 只保留一份最新 XLSX，无历史需求）：

```text
registered  →  parsing  →  parsed  →  qa_passed  →  usable
   │              │           │           │
   └──── 用户点   └──── Worker  └──── 用户    └──── 可在定额页面
        「开始解析」  跑阶段 A   下载 candidate   套用，作为源
                     + autofinalize  上传 reviewed
                                  │
                              cancelled（v0.2 终态；用户取消解析）
```

每个状态的 UI 可用操作见 `quota/web-frontend/SPEC.md` §6.1 / §7.1。

不能仅凭文件名变化推断审核状态；上传 reviewed XLSX 必须显式调 `POST /reviewed`（见 web-frontend SPEC §5.4）。

## 9. 网站侧初步改造点

预计涉及：

- `quota/web-frontend/SPEC.md`：网站前端契约（v0.1 已完成，待 parser 维护者核对）；
- `file_asset_service/app/processors.py`：增加定额 PDF 解析处理器；
- `file_asset_service/app/runner.py`：增加调用 Worker/解析引擎的分支；
- `file_asset_service/app/models.py`：在 `Archive` 表上增加 `parse_*` 字段（见 web-frontend SPEC §6.1），**不新增独立 `parse_run` 表**；
- `file_asset_service/app/assets.py`：将 candidate / final XLSX、QA 报告、Manifest 注册为派生 FileAsset（key 见 §6）；
- `file_asset_service/app/quota_api.py`：增加 8 个端点（`POST /parse` / `GET /candidate.xlsx` / `POST /reviewed` / `GET /final.xlsx` / `GET /manifest` / `GET /qa-report` / `POST /parse/cancel`（v0.1 占位 501），并扩展现有 `GET /archives/{id}` 返回 `parse` 子对象）；
- `file_asset_service/app/mock_parse_runner.py`：mock Worker（`QUOTA_PARSE_MOCK=1` 启用）；
- `file_asset_service/app/ui/quota-parse.js`、`quota-parse-api.js`、`quota-parse-mock.js`：新增 3 个前端模块（解析 tab + 档案详情解析区块）；
- `file_asset_service/app/ui/quota-ui.js`：新增「解析任务」tab，档案详情内增加解析区块；
- `file_asset_service/app/ui/index.html`：注册三个新脚本；
- `file_asset_service/app/storage.py`：按实际需要补充流式读取、临时下载或签名下载能力。

现有裸上传入口不应作为生产解析入口。解析应从已经登记的定额 Archive 和已关联的 PDF 开始。

## 10. 解析项目侧初步改造点

> **v0.2 状态**：已按 `quota/parser/SPEC.md` 完成 1–8 项；9–10 项部分完成（autofinalize 已封装为函数；CSV/XLSX 中间产物保留按"默认不落盘 + --keep-csv 开关"实现）。详见 §13「v0.2 实施成果」。

在 `D:\工程造价学习\定额解析` 中，建议逐步完成：

1. ✅ 增加统一的 Python 调用入口（`quota_parser.pipeline.run_quota_pipeline()`），避免网站依赖多个脚本的命令行细节；
2. ⏳ 将 `D:\工程造价学习\数值审核流程` 等硬编码路径改为参数或环境变量；
3. ✅ 保留 CLI 入口，同时提供可被 Worker 调用的函数接口；
4. ✅ 将业务错误从 `sys.exit` 改为可捕获的异常（`quota_parser.exceptions` 子类）；
5. ⏳ 输出结构化 `task-result.json` / Manifest（dataclass 已就位，序列化在 P1 阶段）；
6. ✅ 保留四川、重庆独立 Profile，不强行合并规则；
7. ✅ 将"文件改名表示审核完成"改为显式的阶段函数（`finalize_reviewed_xlsx()`）；
8. ✅ 保留超过 100 页分段和 OCR 单并发限制（`parse_chunked()` + `_OCR_LOCK` 占位）；
9. ✅ 将自动清洗和 XLSX 生成封装为可重复执行的阶段（5 步 autofinalize 函数化）；
10. ✅ 为候选 CSV、审核后 CSV、最终 XLSX 分别保留文件（`--keep-csv` 开关 + 不覆盖原始结果）。

## 11. 第一版不做的内容

以下内容暂不纳入第一阶段：

- 在线 Excel/CSV 编辑器；
- 取消数值人工审核；
- 自动把所有解析结果导入 Layer 1 定额数据库；
- 清单—工序—定额桥接；
- 多 GPU 并行 OCR；
- 复杂实时进度推送；
- 所有省份的通用解析器；
- 自动换算规则 100% 填充；
- 让网站直接依赖解析项目的本地固定目录。

## 12. 待落实项（仅未决项）

### A. OCR 服务

- ✅ OCR 地址与端口：`http://172.16.20.23:8000`（`quota_parser.config.DEFAULT_OCR_API`）。
- ✅ 单并发约束：worker 用 `_OCR_LOCK` 占位。
- ✅ Markdown 转换：`render_result(result_json, source_pdf)` 完成。
- ⏳ OCR 服务是否支持任务查询、取消和断点续跑？

### B. 运行节点

- ⏳ Worker 部署位置（与 FastAPI 同机 vs 独立 GPU 节点）。
- ⏳ Worker 与 FastAPI 是否能访问同一 MinIO / PostgreSQL。
- ⏳ 第一版是否接受数据库轮询任务（vs 消息队列）。

### D. 输出和质量门禁

- ⏳ 最终 XLSX 的标准文件名与 Sheet 名是否沿用现有规则？
- ⏳ QA 未通过时是否禁止下载 final XLSX？
- ⏳ QA 仅告警时是否允许标记为完成？
- ⏳ 是否把 PDF 页码、章节、定额编码等来源信息写入 XLSX？

### E. 解析 Profile

- ⏳ 第一版正式支持哪些省份 / 版本？
- ⏳ 省份和版本是否完全来自档案元数据（不再根据文件名自动判断）？
- ⏳ 没有对应 Profile 时，是阻止任务创建，还是允许进入人工处理队列？

### F. 网站数据模型（已决策项摘要）

- ✅ 不新增 `quota_parse_run` 表，复用 `Archive` 加列（见 web-frontend SPEC §6.1）。
- ✅ candidate / final XLSX 存 MinIO（路径见 §6），`Archive` 上只存 MinIO key；不注册为派生 FileAsset。
- ✅ 每个档案只保留最新一次解析结果，不存历史。
- ⏳ 第一阶段是否只做"PDF→XLSX 下载"，暂不导入 `quota_item` / `consumption` 等 Layer 1 表？

## 13. v0.2 实施成果（已完成）

> 详细验证报告见 `quota/parser/tests/PARITY_REPORT.md`（143,940 单元格，0 不匹配）。

### 13.1 包结构

```
quota/parser/
├── SPEC.md                              ★ 解析脚本契约（v0.2 Implementation Locked）
├── CLAUDE.md                            开发与运行约定
├── README.md
├── external/                            复用层（动态 import，不复制业务代码）
│   ├── mineru_pdf_parse/scripts/
│   │   ├── parse_pdf.py             →  parse_pdf(*, pdf_path, output_dir, api_url, ...)
│   │   ├── parse_chunked.py         →  parse_chunked(*, pdf_path, output_dir, chunk_size, ...)
│   │   ├── render.py                →  render_result(*, result_json, source_pdf) -> dict
│   │   └── health_check.py          →  health_check(api_url, timeout) -> dict
│   ├── quota_md_to_csv_v2/
│   │   └── extract_quota.py         →  process_md_file(*, md_path, work_dir, province, ...)
│   └── quota_csv_finalize/              autofinalize 5 步（按顺序）
│       ├── clean_empty_qty.py       →  process_xlsx(input, output=None) -> dict
│       ├── drop_toc_sections.py
│       ├── fill_work_content.py
│       ├── space_split_materials.py
│       └── finalize_last_step.py    （原 to_xlsx.py；v0.2 起重命名）
├── quota_parser/                          ★ Worker 调用入口（v0.2 新增）
│   ├── __init__.py                       暴露 run_quota_pipeline / finalize_reviewed_xlsx / serve_worker
│   ├── config.py                         DEFAULT_OCR_API / PROVINCE_KEYWORDS / 路径常量
│   ├── exceptions.py                     QuotaParserError + 7 个子类
│   ├── result.py                         StageAResult / StageBResult dataclass
│   └── pipeline.py                       阶段 A / B / Worker 主流程
└── tests/
    ├── fixtures/sichuan/                  四川样本 MD + PDF + expected.xlsx
    └── PARITY_REPORT.md                   ★ v0.2 ↔ v0.1 行为对齐验证报告
```

### 13.2 Worker 可直接调用的函数

```python
from quota_parser import (
    run_quota_pipeline,    # 阶段 A: PDF → candidate xlsx
    finalize_reviewed_xlsx, # 阶段 B: reviewed → final xlsx
    serve_worker,           # 常驻 Worker（轮询数据库；v0.2 占位）
    # 异常
    QuotaParserError, OcrUnavailableError, OcrTransientError,
    UnsupportedProvinceError, InvalidPageRangeError,
    InvalidXlsxStructureError, ProfileExecutionError,
    WorkdirNotWritableError,
)
```

### 13.3 端到端测试结果（四川样本）

| 维度 | v0.2 生成 | v0.1 baseline | 一致 |
|------|---------|--------------|------|
| Sheet 数 | 15 | 15 | ✓ |
| Sheet 顺序 | 定额条目 / 册说明 / A..N | 定额条目 / 册说明 / A..N | ✓ |
| Sheet1 行数 | 14394 | 14394 | ✓ |
| Sheet1 列数 | 10 | 10 | ✓ |
| 总行数 | 20761 | 20761 | ✓ |
| 段数（"段"行） | 505 | 505 | ✓ |
| 定额数（"定"行） | 1668 | 1668 | ✓ |
| **单元格逐 cell 对比** | 143,940 | 143,940 | **0 不匹配（0.0000%）** |

## 14. 推荐的实施顺序

```text
第一步：✅ 完成——parser 包封装（quota_parser） + end-to-end 对齐验证
        见 quota/parser/tests/PARITY_REPORT.md

第二步：确认 OCR 地址 / Worker 节点 / 人工审核规则
        （§12.A / §12.B 的开放问题）

第三步：网站 Archive 表加 parse_* 列 + 7 个新端点（含 mock 分支）

第四步：mock_parse_runner 跑通一份已有 PDF（无需 Worker）

第五步：前端「档案详情解析区块」+「解析任务 tab」

第六步：联调真实 Worker（quota_parser.serve_worker），替换 mock 分支

第七步：注册派生产物并在档案详情中下载 candidate / final XLSX

第八步：补充 QA、重试、审计和「取消解析」动作
```

---

**当前结论：** 第一版采用"复用 Archive 状态机 + 独立 Worker 执行 + MinIO 保存产物 + 用户下载/上传 XLSX + 不重跑 autofinalize"。格式人工审核取消，数值审核保留为明确的人工门禁——v0.2 起以人工结果为主要依据，网站只对 reviewed XLSX 做最轻量的 Sheet1 结构校验和行数差异告警。

**v0.2 进展：** 解析脚本已封装为可被 Worker 调用的 Python 包（`quota_parser`），行为与 v0.1 baseline 完全一致（143,940 单元格 0 不匹配）。Worker 与 FastAPI 后端的接口契约见 web-frontend SPEC；`serve_worker()` 真实轮询逻辑在 P1 阶段实现。

**v0.3 进展：** 网站前端 SPEC 升到 v0.2（UI 状态表增加 `cancelled`、Mock 异步硬约束、422 模态框状态明确、新增 `POST /parse/cancel` 占位端点）；README 同步去掉 `issues.md` 引用与早期方案对比段。

**配套 SPEC 文档：**
- `quota/parser/SPEC.md` v0.2（解析脚本契约，已锁版）
- `quota/parser/tests/PARITY_REPORT.md` v0.2（行为对齐验证报告）
- `quota/web-frontend/SPEC.md` v0.2（网站前端契约，§11 字段对齐表）
- `quota/README.md`（本文，工作计划视角）