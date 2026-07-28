# 清单定额筛选修复 · Smoke 测试 (v0.3.2)

> **状态**：代码已落地，等用户重启网站 + 浏览器硬刷
> **范围**：后端 [quota_api.py](file_asset_service/app/quota_api.py#L308-L317) primary 筛选 + 前端 [quota-ui.js](file_asset_service/app/ui/quota-ui.js) Plan C
> **不在本范围**：`/facets` 接口的 primary 过滤也走旧逻辑（仍读 pubset），chip 上的数字可能与列表行数对不上。下一批处理。

---

## 0. 重启前自检

- [ ] 后端改动在 [quota_api.py:308-317](file_asset_service/app/quota_api.py#L308-L317)（主查询）和 [L352-360](file_asset_service/app/quota_api.py#L352-L360)（count 查询），均改读 `Archive.metadata_payload["category"]["value"].astext`
- [ ] 前端 `loadQuotaArchivesGeneric` 函数已删除
- [ ] 前端 `load()` 与 `reloadArchives()` 改走 `state.api.getArchives(currentArchiveFilters())`
- [ ] `node --check file_asset_service/app/ui/quota-ui.js` exit 0
- [ ] DB 当前数据：3 条 quota 档案（`metadata.category='construction_quota'`，四川 / 2026）

---

## 1. 重启 + 浏览器硬刷

```bash
# 重启后端（用户手动）
# Ctrl+F5 / Cmd+Shift+R 硬刷浏览器，清掉旧 JS
```

打开 DevTools → Network 面板，确认下列请求都走 `/api/data-lake/quota/archives?...` 而不是 `/api/archives?domain_type=quota&limit=500`。

---

## 2. Smoke 场景（按顺序点）

### 场景 A · 一类筛选

| 操作 | 预期结果 |
|---|---|
| 进入定额档案台，默认显示全部 3 条 | ✓ |
| 点「清单规范」chip | 列表**清空**（无任何 boq_standard 档案） |
| 点「建筑工程定额」chip | 列表显示 3 条 |
| 点「专业工程定额」chip | 列表**清空**（无任何 industry_quota 档案） |
| 点「全部」chip | 列表恢复 3 条 |

**判据**：每切一类，列表**真的会变化**（不是一直显示同 3 条）。

### 场景 B · 省份筛选

| 操作 | 预期结果 |
|---|---|
| 在「建筑工程定额」下点「四川」chip | 列表显示 3 条（都在四川） |
| 点「江苏」chip | 列表**清空**（无江苏档案） |
| 回到「四川」chip | 列表恢复 3 条 |
| 点「全部」（省份） | 仍 3 条（默认全四川） |

### 场景 C · 年份筛选

| 操作 | 预期结果 |
|---|---|
| 在「建筑工程定额」下点「2026」chip | 列表显示 3 条 |
| 点「2025」chip | 列表**清空** |
| 点「2024」chip | 列表**清空** |

### 场景 D · 组合筛选

| 操作 | 预期结果 |
|---|---|
| 「建筑工程定额」+「四川」+「2026」 | 列表 3 条 |
| 「建筑工程定额」+「江苏」+「2026」 | 列表**清空** |
| 「建筑工程定额」+「四川」+「2025」 | 列表**清空** |
| 「建筑工程定额」+「全部」+「全部」 | 列表 3 条（清除二级筛选） |

### 场景 E · 大类切换时的状态联动

| 操作 | 预期结果 |
|---|---|
| 「建筑工程定额」+「四川」 → 切到「清单规范」 | 地区 chip 应该**自动恢复"全部"**（清单规范的二级是 scope，不是 jurisdiction） |
| 「清单规范」chip 显示「适用范围维度待接入」 | （备用 fallback，与本次修复无关） |

---

## 3. 上传链路冒烟（验证你后端 + 前端上传 modal 的修复）

| 操作 | 预期结果 |
|---|---|
| 点「新增档案」按钮 | 弹出上传 modal |
| 不选资料分类，点「上传并保存」 | 按钮 disabled（canSubmit=false） |
| 选 PDF + 选「建筑工程定额」+ 不选省份/年份 | 按钮 disabled |
| 选 PDF + 选「建筑工程定额」+ 选「四川」+ 输 2026 | 按钮 enabled |
| 点「上传并保存」 | 成功 toast「已创建 1 份档案」 |
| 新档案**只出现在「建筑工程定额」+「四川」+「2026」组合**下 | ✓（验证 metadata.category='construction_quota' 被正确写入） |
| 切到「清单规范」chip | 新档案**不出现**（验证 metadata.category 过滤生效） |

---

## 4. 不在本次 smoke 范围（已知差异）

| 项 | 现状 | 何时处理 |
|---|---|---|
| **chip 上的数字（计数）** | `/facets` 接口仍按 pubset 字段算，与列表筛选结果可能对不上（例如专业工程定额 chip 显示 0 但筛选列表也是 0，巧合一致；切到实际有 industry 档案的省份时会有偏差） | 下一批（建议同步把 `/facets` 也改读 `Archive.metadata_payload["category"]["value"]`） |
| **省份 chip 列表** | 当前用前端静态 `PROVINCE_REGIONS`（31 省 + 深圳）兜底，与后端 `/facets` 真值并存；后端接好后自动让位 | 待批 1 收尾确认 |
| **「清单规范」/「专业工程定额」的二级筛选** | 适用范围 / 行业分类目前是「待接入」状态 | 字典接口就绪 |

---

## 5. 回滚预案（如果出意外）

```bash
# 后端回滚
git checkout HEAD~1 -- file_asset_service/app/quota_api.py

# 前端回滚（如果 Plan C 有问题）
git checkout HEAD~1 -- file_asset_service/app/ui/quota-ui.js
```

回滚后行为：列表回到「选省份/年份/版次不影响」的旧状态（前端 Plan C 前的形态）。