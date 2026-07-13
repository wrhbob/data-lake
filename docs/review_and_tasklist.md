# 数据湖交付 · 代码评审与任务单

- **评审角色**: 审查专家(只读评审,不改代码)
- **被审对象**: 左侧 CC(Claude Code)在 `data_lake_handoff` 的工作成果
- **评审日期**: 2026-07-10
- **评审方式**: 静态读代码/文档 + 本地测试套件运行;运行时事实由被审方(CC)补正并采纳
- **综合定级**: **B+(代码就绪,交付未完全落地)**

---

## 1. 结论摘要

CC 的成果**工程完成度高、核心机制成熟可信**:调度 → worker → 适配器 → 下载 → 建档 → 去重的主链路在代码与测试层面自洽。短板集中在**一个仍存活的导入 bug(P0 根因)** 与**部分交付/可移植性收尾**,而非核心逻辑。

| 维度 | 评价 | 依据 |
|---|---|---|
| 架构设计 | 优 | NAS 权威 / 无状态爬虫 / 只读同事,边界清晰 |
| 并发正确性 | 优 | `FOR UPDATE SKIP LOCKED` + 租约/心跳/过期回收 |
| 去重可靠性 | 优 | 应用层早停 + DB 双唯一约束兜底 |
| 容错/重试 | 良 | 指数退避、死信、源自动转人工 |
| 测试覆盖 | 良 | 513 通过;含平台耦合(见 §4) |
| 可移植性 | 中 | 硬编码 mac 路径;Windows 下测试假失败 |
| 交付落地 | 中 | MinIO+同事访问已通;目录 PG 迁移待决策(见 §5 修正) |

---

## 2. 亮点(值得肯定)

- **并发模型正确**: `file_asset_service/app/cost_info_worker.py:82-176` 用 PostgreSQL `SKIP LOCKED` 领任务,`include_expired_running` 回收过期租约,多机不会重复领取或死锁。
- **去重是双层的**: 应用层连续重复早停(`cost_info_worker.py:199-213`);DB 层唯一约束兜底 `uq_archive_business_version(tenant_code, domain_type, business_key, version)` 与 `uq_file_asset_tenant_sha256`(`app/models.py:302-308,183`)。即使应用逻辑失效也不会重复入库。
- **容错成熟**: 指数退避重试、超限进死信(spec 017)、连续 5 次失败自动把源置 `manual_recovery`(`cost_info_worker.py:337-385`)。
- **爬虫礼貌性好**: HTTP 客户端有节流+抖动、读超时 deadline、Content-Length 完整性校验、TLS 控制、重试(`app/source_adapter.py:158-244`)。
- **适配器可扩展**: 中央 `ADAPTERS` 注册表 + 每站完整配置工厂;新增桥接后已覆盖 46 个可爬站点(`app/cost_info_config_factories.py`)。

---

## 3. 问题与风险

### P0 — 导入 bug【已修 · 2026-07-10 审查已验证 ✅】

原缺陷:`build_registry_config` 无条件用 `parser_config_for(row)` 写 `list_url = entry_url`(站点根 URL)的残缺 parser,覆盖适配器正确列表页——即"全爬到 0"的根因。

**修复(CC 交付,审查通过)**:

- `info_price_site_import.py` 新增 `_factory_parser_for(site_id)`(惰性 import 工厂,未注册 `ValueError→None`)。
- `build_registry_config:171` 改为 `"parser": _factory_parser_for(site_id) or parser_config_for(row)`。46 个工厂站点导入即产出完整 parser(带 `file_list_endpoint`/`years_endpoint`),与 `restore` 一致;~290 pending 站仍走 CSV 摘要,行为零变化。
- 新增正/反 2 条回归测试锁定"导入不得覆盖已恢复的完整 parser"。

**审查核验**: 落点在写 `source.config` 唯一汇聚点、关注点分离干净、回退安全、无别名风险;`test_cost_info_config_factories / test_info_price_site_import / test_cost_info_registry_runner` 共 12/12 绿。`restore-all` 现为稳态,已放行。

### P1 — 目录 PostgreSQL 迁移待决策【已按运行时事实修正,见 §5】

原评审"系统仍绑 127.0.0.1:15432、同事无法访问"仅对**目录 PG**成立(目录库仍在本机 Docker,因原 NAS 目录已丢、选择本地重建);**MinIO(172.16.20.26,29GB 原始文件)与同事访问已打通**。故 P1 真正剩余项是**决策项**:是否把目录 PG 也迁到 NAS,而非"全未落地"。

### P1 — 平台可移植性

- 【已修 · 2026-07-10 审查已验证 ✅】重庆 cookie 引导已重写为 `sync_playwright`(`app/chongqing_cost_info.py:43-96`):删 `os/subprocess`、惰性导入 playwright、`playwright_starter` 可注入,彻底去掉 `.sh` 依赖,Win/mac/linux 通用。模块无 playwright 也可导入、重庆套件 13/13 绿。**残余**:真 chromium 过 412 的 e2e 未跑(见 §6 验证项)。
- 测试 **18/534 失败**,经核实**全部是 Windows 下子进程 stdout 用 gbk 解码中文导致**(`completed.stdout` 变 None),**非逻辑回归**;posix/UTF-8 下应全绿。

### P1 — 安全:交付目录含真实 `.env`【被审方提级同意】

交付目录内存在真实 `.env`(含真实 MinIO 密码)。仓库当前**非 git**,`.gitignore` 暂不生效。**外发交付包前必须剔除/换占位**,并提供仅含占位符的 `.env.example`。

### P2 — 性能隐患(当前规模可接受)

- `_task_period_already_covered`(`cost_info_worker.py:269-284`)每任务把该源所有 current archive 全量捞出在 Python 比期号,O(n) per task。
- `_select_pending_tasks` 的 site_id/adapter_kind/due 过滤在查询后用 Python 完成,取行多于所需。
- 建议随数据量增长把上述判断下推到 SQL 并加索引。

### P2 — 导入与 restore 的 stable 字段漂移【P0 修复后新观察】

导入现只替换 `parser` 块,`stable`/`price_coordinates`/`source_shape` 仍取 CSV 行值。对已 `restore` 的工厂站再跑 ledger 导入,会得到"工厂 parser + CSV stable"的混合配置,`region_code`/`tax_type`/`publisher` 等可能与工厂权威值漂移(parser 正确,不影响抓取)。建议后续统一:工厂站导入也优先工厂 `stable`,或对工厂站跳过 ledger 覆盖。

### P2 — 覆盖范围

- ~290 个 region 无适配器(长期工程);10 个声明-only 源无 `adapter_kind` 不可爬(设计如此)。

### P2 — manifest 时效

- 随包 manifest 生成于 2026-07-01,来自工作站库;正式交付应从 NAS 权威库重生成并发布。

---

## 4. 测试结论

- **本地(Windows)**: `513 passed, 18 failed, 3 skipped`。
- **18 项失败根因**: Windows 默认 gbk 编码解码子进程中文 stdout 失败(`UnicodeDecodeError` → `stdout=None`),属**环境/编码问题,非逻辑缺陷**。
- **处置**: CI 走 posix + `PYTHONIOENCODING=utf-8`,消除假失败;文档标注"开发/测试面向 mac/linux(UTF-8)"。

---

## 5. 被审方(CC)运行时补正(已采纳)

评审为静态读代码/文档所得;以下运行时事实由 CC 提供并采纳,任务单据此修订,**避免重复功**:

1. **NAS MinIO 已接入**: `172.16.20.26`,29GB 原始文件在用(非"仍绑本地")。
2. **目录已从 manifest 重建**: 877 期信息价档案恢复,可浏览/下载。
3. **同事入口已就绪**: 内网 URL + Basic 登录 + onlogon 常驻。
4. **唯一遗留**: 目录 PG 仍在本机 Docker(原 NAS 目录已丢、选本地重建);是否迁 NAS 为**决策项**。

> 可静态佐证:`README_START_HERE.md:149-150` 明确"不要用 127.0.0.1:15432、以 NAS 真实地址为准";`deploy/nas/` 与 `crawler-node.env.example` 脚手架支持 MinIO 上 NAS 的可行性。

---

## 6. 任务单

图例 · 责任方:**CC**=被审方(代码/运行)· **审查**=评审专家 · **决策**=需人拍板

### P0 立即(根治)【已完成 ✅】

- **[CC] ✅ 修复 ledger 导入污染**: 新增 `_factory_parser_for`,`build_registry_config` 优先用工厂 parser。重庆等站 `file_list_endpoint`/`years_endpoint` 不丢失(测试锁定)。
- **[CC] ✅ 回归防护**: 正/反 2 条测试锁定"导入不得覆盖已恢复的完整 parser"。
- **[审查] ✅ 评审 B 的 diff**: 根治到位、12/12 绿、稳态成立,**已放行 `restore-all`**。

### P1 本周

- **[决策] 目录 PG 是否迁 NAS**: 拍板"迁/不迁";若迁,给出迁移窗口与回滚方案。
- **[CC] ✅ 平台解耦(重庆 playwright)**: 已重写为 `sync_playwright` 惰性导入、去 `.sh`,审查已放行。
- **[CC] CI 固定编码**: 设 `PYTHONIOENCODING=utf-8` 并在 posix runner 跑,消除 18 项假失败。验收:CI 全绿。
- **[CC/决策] 安全收尾**: 交付包外发前剔除/占位真实 `.env`,提供 `.env.example`;确认仓库纳入 git 后 `.gitignore` 生效。

### P1 数据交付

- **[CC] 从 NAS 权威库重生成并发布 manifest**: 发布到 `s3://cost-report/manifests/latest/` + 日期快照。验收:同事按固定路径读到与 NAS 库一致的清单。

### P2 计划内

- **[CC] 性能下推**: period-covered 判断与 worker 任务过滤下推 SQL/加索引。
- **[CC] 站点逐个激活验证**: 用 `restore --site-id ... --activate` 逐站恢复后,按优先级(重庆+四川6市先行)做真实抓取验证(触网)。
- **[决策] 覆盖扩展路线图**: ~290 个无适配器 region 按省排优先级立项。

---

## 6b. 待验证项(外部动作,需点头)

- **[决策/CC] 重庆真机过 412 e2e**: 起真 chromium 打活站,确认 `sync_playwright` 真能过 412、出档案。**这是重庆放量的前置闸门**。
- **[已证实] 道 1 HTTP 链路**: `restore-all --activate`(46 工厂配置落库/49 源激活)→ 真机爬四川6市+湖北+武汉+南京,cost_info 档案 877→928,南京 PDF 落 `cost-raw` 可下载。全链路已证。

## 7. 下一步(均不触网、不动生产)

- **A(本文档)**: 审查专家已落地并持续更新 = `docs/review_and_tasklist.md`。
- **B(P0 根治)**: ✅ 完成并放行;**`restore-all --activate` 已执行**,道 1 HTTP 全链路已真机证明(877→928)。
- **Track 2(重庆跨平台)**: ✅ 完成并放行。
- **剩余(外部/写库,需点头,可并行)**: ① 重庆真机过 412 e2e(重庆放量前置闸门);② HTTP 站分波次放量(已证链路,dedup 兜底)。

## 8. 一句话给管理者

> 代码质量与核心机制**可信**;P0 导入 bug 已根治并通过审查,MinIO 与同事访问已通。离"完全交付"还差 **目录 PG 迁移决策 + 跨平台/安全收尾**,以及 `restore-all` 写库后的逐站真机验证。建议先拍 P1 决策,再逐站放量。
