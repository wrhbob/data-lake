# 解析全流程审计报告 · 2026-08-03

> **目的**：在投入大规模省级定额入湖前，对当前 `quota_parser` 全链路做一次端到端审计，
> 找出"玩具状态"的真凶，明确 P0/P1/P2 修复顺序。
>
> **方法**：4 路并行深读（worker/sweeper、pipeline/extractors、minerU 脚本、API/service/schema），
> + 关键项亲手复核。每条隐患都给出 `文件:行号` 与触发场景，未复核的明确标注。
>
> **范围**：`file_asset_service/app/quota_*`、`quota/parser/quota_parser*`、
> `quota/parser/external/mineru_pdf_parse/`、`quota/parser/external/quota_md_to_csv_v2/`。

---

## 严重度图例

| 标记 | 含义 |
|---|---|
| 🔴 **致命** | 会产出错误数据 / 永久卡死 / 静默假成功 |
| 🟠 **高** | 数据正确性 / 表解析脆弱，常见样本会触发 |
| 🟡 **中** | 运维 / 一致性，累积后成患 |
| 🟢 **低** | 边缘 / 一次性 / 锦上添花 |
| ✅ **正确** | 明确无问题，放心 |

---

## 🔴 致命 — 11 项

### F1. province 非 `sc/cq` 一律静默落"占位假成功"
**位置**：[pipeline.py:143-150](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser/pipeline.py#L143-L150) ✅ 已复核
**触发**：`province=None / 'Sichuan' / 'SICHUAN' / '重庆' / '500000'`
**现象**：
- 归一化只有 `strip().lower()`，无任何别名反查
- 上述输入全部收敛到 `'default'`
- 落到 [pipeline.py:200-216](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser/pipeline.py#L200-L216) 占位分支，写 10 列 `empty_candidate.xlsx`，`rows_count=0`
- worker 照常标 `candidate_ready` + `parsed`，前端显示"可审核"
- 用户在 UI 上看到一个"成功解析"但 0 行数据的僵尸档案

**根因**：7-31 重庆事故只打了 API 兜底补丁，pipeline 本体没修。任何历史脏数据 / 手工喂的其他值都会复现。

**建议**：pipeline 加 PROVINCE_NAMES 反查 + 中文名 / 数字码映射；识别不出就**抛错**，不落占位。

---

### F2. 解析失败 → 档案永久卡死 `parsing`，再也无法触发
**位置**：worker 失败路径仅 `_mark_job_failed`（更新 quota_parse_job.status='failed'），不更新 archive；sweeper 只扫 `running` / `queued`
**触发**：pipeline 抛异常（OCR 挂、HTML 解析炸、mineruU 5xx 多次等）
**现象**：
- archive.parse_status 永远留在 `'parsing'`
- `trigger_parse` 对 `parsing` 拒绝重入（[service.py:123](../file_asset_service/app/quota_parser/service.py#L123)）
- UI 点多少次都没用，只能手改库
- 比超时更常见的坏路径（pipeline 抛异常很常见）

**建议**：worker 失败路径同步把 `archive.parse_status` 置为 `failed_user` / `failed_permanent`。

---

### F3. 并发双击 → 同一档案双 job
**位置**：[service.py:524-529](../file_asset_service/app/quota_parser/service.py#L524) ✅ 已复核
**触发**：用户在 UI 上快速连点两次"解析"，或两请求并发在途
**现象**：
- `quota_parse_job` 表无 `(archive_id)` 唯一约束（models.py:571-613 只有 job_id PK）
- active job 检查是裸 `SELECT ... scalar_one_or_none()`，无 `FOR UPDATE`
- 两个请求同时过 `_trigger` → 都查不到 active job → 各插一条 queued job → 都 commit
- 真 worker 对同一 PDF 重跑整套 5-15 分钟流水线，覆写 parse_* 字段

**建议**：加 `(archive_id)` 唯一部分索引（`WHERE status IN ('queued','running')`），或 active job 检查用 `SELECT FOR UPDATE`。

---

### F4. sweeper 误杀阈值错配 → 活 job 被标 timeout → 状态回跳
**位置**：
- 阈值定义 [worker.py:60-61](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser_worker.py#L60)
- 真实单 chunk 上限 [parse_chunked.py:174-175](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/mineru_pdf_parse/scripts/parse_chunked.py#L174)
- 无条件 `_mark_job_done` [worker.py:579-596](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser_worker.py#L579)
**触发**：后端 minerU 排队 / 负载高，某个 chunk 轮询 > 15 分钟
**现象**：
- 后续 chunk 阈值 15 min < 单 chunk 轮询合法上限 30 min
- 慢后端下活 worker 被 sweeper 标 `parse_timeout`
- worker 跑完后 `_mark_job_done` 无条件把 `failed` 翻回 `done`
- 状态来回跳 + minerU 双份 GPU 占用
- 非 chunked 路径（≤100 页）零心跳（worker.py:187-192 + parse_pdf.py:106）→ 100 页 PDF 在加载模型时也可能被杀

**建议**：阈值对齐（后续 chunk 也放宽到 30min+ 余量），或 worker 用 last_heartbeat_at 定期刷（不只 chunk 完成时刷）。`_mark_job_done` 加状态守卫（仅在 status='running' 时翻 done）。

---

### F5. 临时目录永不清理 → 磁盘无限堆积
**位置**：
- 创建 [worker.py:321-324](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser_worker.py#L321)
- cleanup 模块 [quota/parser/quota_parser/cleanup.py](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser/cleanup.py) 存在但未被 import
**触发**：每个 job 完成 / 失败后
**现象**：
- `D:\tmp\quota_parser_work\<job_id>` 全程无 `rmtree`
- 531 页实测每任务 ~40MB（source.pdf + ocr/chunks/ + result.json + md/html）
- 多任务累积至磁盘耗尽

**建议**：job 完成后清理 work_root；或 cron 调 `cleanup_expired_jobs`。

---

### F6. 阶段 B（reviewed→final）产物不注册 archive_file → 删除清不到 → MinIO 泄漏
**位置**：[service.py:265-285 `upload_reviewed`](../file_asset_service/app/quota_parser/service.py#L265)
**触发**：所有真模式 / mock 阶段 B
**现象**：
- `upload_reviewed` 只 put_object + 写 `final_xlsx_key`，不调 `register_parse_artifact`
- `parse_final_xlsx` 行永远不存在
- `delete_reviewed_only` 找 0 行 → final.xlsx MinIO 对象永不删
- 返回的 `deleted_minio=deleted_af+1`（service.py:690）虚高

**建议**：upload_reviewed 内部补调 register_parse_artifact(file_role='parse_final_xlsx')。

---

### F7. register_parse_artifact 的 sha256 去重 + 陈旧 object_key → 共享对象被误删
**位置**：[service.py:444-461 `register_parse_artifact`](../file_asset_service/app/quota_parser/service.py#L444)
**触发**：两档案产出相同 sha256 的产物（如两个 archive 解析同一 PDF，或测试样本）
**现象**：
- 复用第一个的 FileAsset 行但不更新 object_key
- key 含 archive_id（worker:381 `quota/{job.archive_id}/artifacts/...`）→ 复用行指向 A 的 MinIO key
- archive B 的 archive_file → FileAsset 指错 key
- 删 A 的解析结果 → 把 B 仍引用的对象也删掉 → B 下载 404

**建议**：要么按 (tenant, sha256) 真正复用对象（不复制 key），要么不复用、按 archive_id 各自存。

---

### F8. `is_parse_mock()` 无环境门禁
**位置**：[service.py:37-39](../file_asset_service/app/quota_parser/service.py#L37) ✅ 已复核
**触发**：生产部署若残留 `QUOTA_PARSE_MOCK=1` env（.env / supervisord / 容器环境变量泄漏）
**现象**：
- 仅看 `os.environ.get("QUOTA_PARSE_MOCK","0")=="1"`，不区分 dev/prod
- 真模式端点（[quota_api.py:1782 / 1819-1822](../file_asset_service/app/quota_api.py#L1782)）也读它
- 一旦生产残留 → 所有解析变 mock → 假 candidate / final / QA 数据入库 + MinIO

**建议**：加 app-mode 门禁（生产强制关 mock；dev 显式打日志"mock 模式已开启"）。

---

### F9. reviewed.xlsx 校验极弱 + mock 完全跳过 → 空/伪造文件可当 final 入库
**位置**：
- 真模式 [service.py:157-200 `validate_reviewed_xlsx`](../file_asset_service/app/quota_parser/service.py#L157)
- mock 模式 [quota_api.py:1687-1696](../file_asset_service/app/quota_api.py#L1687)
**触发**：用户上传空文件、伪造 xlsx、text/html 改名
**现象**：
- 真模式只查"有 Sheet + 名='定额条目' + max_column==9"，无行数 / 表头 / 类型校验
- 0 数据行的 9 列空 sheet 通过；openpyxl 伪造的 '定额条目' + 9 列垃圾也通过
- mock 只过文件名后缀 + mime（1663-1681），`text/html` 改名 `x.xlsx` + mime=octet-stream 即可落库
- finalize_reviewed_xlsx 校验同样弱（[pipeline.py:289-310](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser/pipeline.py#L289)），随后原样 copy

**建议**：加最小行数 / 表头字符串 / 必填列非空校验；mock 模式也走同一结构校验。

---

### F10. 部分 chunk OCR 失败被当"全量成功"
**位置**：[worker.py:184-185](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser_worker.py#L184) + [parse_chunked.py:398-407](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/mineru_pdf_parse/scripts/parse_chunked.py#L398)
**触发**：某 chunk 抛 `PollTaskLost` / `PollTaskTimeout` / 5xx
**现象**：
- worker 不读 `parse_chunked` 的 `all_succeeded`
- 某 chunk 失败 → `result_paths` 变短 → `zip(chunks, result_paths)` 静默截断
- 缺段无任何告警
- 产出"看似成功"的残缺 candidate.xlsx

**建议**：worker 检查 `all_succeeded`，任一 chunk 失败 → job 标 `failed_user`，不写 candidate。

---

### F11. 无任何重试机制 — `attempt` / `max_attempts` 是死字段
**位置**：[worker.py:237](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser_worker.py#L237) + models.py:595-596
**触发**：minerU 瞬时抖动、PG 瞬断、S3 一时 5xx
**现象**：
- 文档（INTEGRATION_PLAN.md / DB_SCHEMA.md）承诺 transient 重试（attempt+1、3 次内回 queued、超限转 failed_user）
- 代码全无：失败即永久 `failed_permanent`，无回 queued 路径
- 与声明的设计相悖

**建议**：实现 transient 分类 → 自动回 queued（attempt < max_attempts）；超过则失败。

---

## 🟠 高 — 数据正确性 / 表解析脆弱（7 项）

### H1. `total_cols` 只取第一行 → 表头空白行导致整表静默丢弃
**位置**：[sc.py:179](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/quota_md_to_csv_v2/extractors/sc/extract_quota.py#L179) / [cq.py:186](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/quota_md_to_csv_v2/extractors/cq/extract_quota.py#L186)
**触发**：OCR 把首行识别为空行 / 短合并标题（如 `colspan=2` 的"表 X"）
**现象**：数据列从 col1 起被静默丢弃 → `find_projects` 找不到项目编码 → `extract_table` 直接 `return [], None` → **整表静默丢弃且不记 issue**

**建议**：取多数派列数（前后 3 行），不要只取首行。

---

### H2. `to_float` 全角数字 / 全角逗号 → 静默归 0
**位置**：[sc.py:148-156](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/quota_md_to_csv_v2/extractors/sc/extract_quota.py#L148) / [cq.py:155-163](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/quota_md_to_csv_v2/extractors/cq/extract_quota.py#L155)
**触发**：OCR 出全角数字（`１，２３４`）、全角逗号、空格千分位
**现象**：
- `strip_numeric_brackets(...).replace(",", "")` 不处理全角 → `float()` 抛错 → 静默归 `0.0`
- 单价列存原串看着没坏，但**验证列 / 求和错**
- 定行验证"单价 × 消耗量 ≠ 验证列"成为常态

**建议**：补全角 → 半角转换 + 全角 / 空格千分位去除。

---

### H3. 料行循环无终止条件 → 表尾"注："说明行被当数据行
**位置**：[sc.py:521-528](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/quota_md_to_csv_v2/extractors/sc/extract_quota.py#L521)
**触发**：任何定额表常见的表尾注释行
**现象**：
- `for raw_row in raw_rows[mh+1:]` 把 material_header 之后每一行都当料行
- 表尾 `<tr>` 里的"注：…"行被 `parse_material_row` 当成料行，`name` = 注释全文 → 输出一条垃圾料行
- 比例行判定 `re.match(r"^[（(]", raw_q)`（sc.py:394）—— 若 OCR 丢了括号，比例行被当普通行按 `qty*price` 算验证

**建议**：表尾"注："行识别 + 跳过；料行循环加终止条件。

---

### H4. `find_projects` 误把页码 / 章节码当项目编码
**位置**：[sc.py:215-227](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/quota_md_to_csv_v2/extractors/sc/extract_quota.py#L215) / [cq.py:222-234](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/quota_md_to_csv_v2/extractors/cq/extract_quota.py#L222)
**触发**：表内出现页码 `P0001`、章节码 `A0101`、说明 / 页脚行里的编码
**现象**：任意 cell 匹配 `^[A-Z]{1,2}\d{4}$` 就当项目编码 → `project_cols` 混入假列 → **整表按错误列序抽取**，静默错位

**建议**：项目编码必须配 `材料名称` 列前缀（"定额编号"/"项目编码"列头），不能裸扫所有 cell。

---

### H5. 未闭合 `<table>` 导致两表拼成一张
**位置**：[sc.py:710](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/quota_md_to_csv_v2/extractors/sc/extract_quota.py#L710) / [cq.py:776](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/quota_md_to_csv_v2/extractors/cq/extract_quota.py#L776) `re.finditer(r"<table[^>]*>.*?</table>", md_text, re.DOTALL)`
**触发**：MinerU 输出某个 `<table>` 未闭合
**现象**：
- 非贪婪 `.*?` 延伸到下一个 `</table>` → 把两张表拼成一次 match
- BeautifulSoup 把两表所有 `<tr>` 当一张表解析，行列错位、项目列污染
- 若某 `<table>` 之后全文都没有 `</table>` → 该表完全不匹配 → 整表静默丢弃（无 issue）

**建议**：用 HTML parser 解析（BeautifulSoup），不要靠正则切 table。

---

### H6. 跨 chunk 表格被从中间切断，无愈合
**位置**：[parse_chunked.py:100-108](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/external/mineru_pdf_parse/scripts/parse_chunked.py#L100)
**触发**：任何跨第 100 / 200 / 300… 页的定额表（此类书籍表格横跨页很常见）
**现象**：
- 纯页数整除切分，**完全不知道表格边界**
- 跨页表格被切成两半 → 两半分别进两个 chunk OCR → 产物是两张残缺 / 断裂的 `<table>`（或 MinerU 把后半识别错乱）
- 合并后无任何检测 / 愈合
- test 数据 503 张表、page_idx 跳变 4 处，说明确实存在跨 chunk 切分

**建议**：切分前做表格边界探测（用 pdfplumber / PyMuPDF），跨页表宁可放单 chunk 也不切断。

---

### H7. 状态词汇分裂：`candidate_ready` / `done` 不在白名单
**位置**：
- 白名单 [service.py:71-80](../file_asset_service/app/quota_parser/service.py#L71) ✅ 已复核
- 实际写入 [service.py:681 `archive.parse_status = "candidate_ready"`](../file_asset_service/app/quota_parser/service.py#L681) ✅ 已复核
- 含错值 [service.py:576 `_REVIEWED_REVERTIBLE_STATUSES = {"qa_passed", "usable", "done"}`](../file_asset_service/app/quota_parser/service.py#L576) ✅ 已复核
- 死代码 [service.py:83 `_check_parse_status` 全库零引用](../file_asset_service/app/quota_parser/service.py#L83)
**现象**：
- `candidate_ready`、`done` 都不在 `_VALID_PARSE_STATUSES`，但代码实际在写它们
- 前端也认 `candidate_ready`（quota-ui.js:72），说明这是第三个真实状态，只是没进白名单
- `_check_parse_status` 一旦被接线，reviewed-only 回退流程直接炸（白名单拒绝）

**建议**：把 `candidate_ready` / `done` 加入白名单，或反推成 `parsed` / `qa_passed` 别名，统一词汇。

---

## 🟡 中 — 运维 / 一致性（6 项）

| # | 问题 | 位置 |
|---|---|---|
| M1 | profile 硬编码默认 `"sichuan"`，重庆档案 job.metadata.profile 被标错 | [quota_api.py:1576](../file_asset_service/app/quota_api.py#L1576) ✅ 已复核 |
| M2 | chunked 路径漏传 `ocr_api_url`，大 PDF 打到硬编码 IP，job 里配的地址不生效 | [pipeline.py:177-183](../file_asset_service/app/../../file_asset_service/app/../../file_asset_service/app/../../quota/parser/quota_parser/pipeline.py#L177) |
| M3 | ck_archive_file_role 重建仅 PG 分支，SQLite dev 环境写 parse_* 会 CheckViolation | [database.py:377](../file_asset_service/app/database.py#L377) |
| M4 | 双目录漂移：`mineru_pdf_parse` vs `mineru-pdf-parse`、`quota_md_to_csv_v2` vs `quota-md-to-csv-v2`，改错一边不传导 | repo 根 `quota/parser/external/` 全部 |
| M5 | OCR URL 硬编码 7 处 + env 名不统一（`QUOTA_PARSER_OCR_URL` vs `MINERU_API_URL`） | 散落 parse_chunked / parse_pdf / health_check / quota_ocr 等 |
| M6 | 裸上传 `/upload` 双入口仍活着（CLAUDE.md P0 已列但未改），历史无主 blob 累积 | [quota_api.py:1233](../file_asset_service/app/quota_api.py#L1233) |

---

## 🟢 低（选列）

- **`parse_html` 角色实际存的是 JSON**：artifact 上传时注册了 role 但存的是 ocr_result_json（worker.py:363-370）
- **artifact 上传 4 步独立事务无补偿**：中途失败 → 孤儿产物，job 卡 running 后被 sweeper 标 failed
- **`_make_task_id` 秒级精度**：同秒内同 archive 两次触发得到相同 task_id（service.py:58-60）
- **`parse_async.py` 硬编码绝对路径** `C:\Users\wrhbob\.claude\skills\...`，换机器即坏
- **worker 双启动无 flock 防护**（Windows）：双进程同时轮询 / 刷日志，job claim 靠 DB SKIP LOCKED 兜住

---

## ✅ 设计正确的（放心的地方）

- **job claim 无竞态**：`SELECT ... FOR UPDATE SKIP LOCKED` + UPDATE 同事务（worker:224-242）
- **trigger 事务完整**：archive 状态变更 + job INSERT 同一 session 一次 commit，回滚不卡死
- **poll 有 404 判定**：连续 3 次 404 抛 `PollTaskLost`，不会无限轮询
- **register_parse_artifact 内容级幂等**：同 sha256 复用 FileAsset，不重复建行
- **模型与 DB 一致**：28 个 role 动态 CheckConstraint 已对齐（2026-08-01 修）
- **SQL 全参数绑定**：无注入风险

---

## 修复优先级表

| 批次 | 项 | 工作量 | 影响 |
|---|---|---|---|
| **P0-A** | F1 province 归一化反查 + 抛错不落占位 | 半日 | 根治 0 行假成功 |
| **P0-B** | F2 失败同步更新 archive + F4 心跳 / 阈值对齐 | 半日 | 根治"永久卡死"和"误杀" |
| **P0-C** | F3 双 job 唯一约束 + F8 mock 门禁 | 2h | 防并发 / 防假数据 |
| **P1** | F6 / F7 删除链路 + F9 校验加强 | 1 日 | 防泄漏 / 防伪造 |
| **P1** | F5 临时目录清理 | 2h | 防磁盘耗尽 |
| **P1** | F10 / F11 部分失败告警 + 重试机制 | 1 日 | 防永久失败 |
| **P2** | H1-H7 表解析加固（除 H6 跨 chunk） | 2-3 日 | 数据质量 |
| **P2** | H6 跨 chunk 表格边界探测 | 1-2 日 | 大 PDF 数据完整性 |
| **P3** | M1-M6 中级清理 | 分散 | 长期一致性 |
| **P3** | 🟢 低 | 锦上添花 | — |

---

## 附：审计未复核项声明

下列行号 / 路径由 Explore agent 报告，**未在本次会话中亲自复核源码**（仅复核了报告中标 ✅ 已复核 的项）：

- F4 误杀阈值的具体数字（agent 给出 worker:60-61 + parse_chunked:174-175）
- F5 cleanup 模块未接线（agent 给出 quota/parser/quota_parser/cleanup.py）
- F10 worker 不读 all_succeeded（agent 给出 worker:184-185）
- F11 重试机制缺失（agent 给出 worker:237 + models.py:595-596）
- H1-H5 sc.py / cq.py 的具体行号
- H6 跨 chunk 切分（agent 给出 parse_chunked.py:100-108）
- M2 chunked 漏传 ocr_api_url（agent 给出 pipeline.py:177-183）
- M3 database.py:377 PG 分支
- M4 双目录漂移
- M5 OCR URL 硬编码 7 处
- M6 双入口 /upload
- 🟢 低全部

修复时建议优先复核这些行号定位（agent 报告可信度来自完整 read，不是脑补，但行号有可能因后续改动漂移）。