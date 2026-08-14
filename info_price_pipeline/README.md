# 信息价提取 v3 (per-city 独立架构)

PDF 信息价期刊 → xlsx（市场信息价 + 新型材料 + PC 构件 + 元数据）
支持城市：成都 / 重庆 / 北京（per-city 独立目录，各自一套脚本）

---

## 一、项目结构（per-city 独立）

```
信息价提取/
├── 成都/                        ← 成都独立目录（完整一套）
│   ├── 1_脚本/                  ← 成都专属 step3_extract_cd.py
│   ├── 2_输入/                  ← 放 PDF
│   ├── 3_中间产物/cd_06_ocr/    ← OCR 缓存（result.json + .md + .html）
│   ├── 4_输出/                  ← 最终 xlsx
│   ├── 5_日志/                  ← step5 process.log
│   ├── 6_配置/                  ← 成都市.yaml + section_titles.json
│   └── run.py                   ← 成都主入口
├── 重庆/                        ← 重庆独立目录
│   ├── 1_脚本/step3_extract_cq.py
│   ├── 6_配置/重庆市.yaml
│   └── run.py
├── 北京/                        ← 北京独立目录
│   ├── 1_脚本/step3_extract_bj.py
│   ├── 6_配置/北京市.yaml
│   └── run.py
├── 6_配置/                      ← 跨城通用配置（根目录）
│   ├── section_titles.json      ← 章节关键词（白名单，新增章节要加这里）
│   ├── skip_keywords.json       ← 9 类整章 SKIP
│   ├── city_codes.json          ← 城市 → 2字母代码
│   └── 城市模板/                ← 各城市 yaml 模板
├── .venv/Scripts/python.exe     ← 项目统一 venv（per-city 目录无 venv）
```
（2026-07-31 注：根目录旧入口 `run.py` + `1_脚本/` + `4_输出/` 已删，所有城市必须 `cd {city}/` 跑 per-city run.py）

**关键设计**：
- 每个城市**独立目录 + 独立脚本**，互不 import
- **共享 venv**（根目录 .venv），per-city 目录无 venv
- **共享 6_配置**（根目录优先，per-city 可扩展）

---

## 二、跑前检查（5 个判断坑自动处理）

`run.py` 启动时**自动处理 5 个常见坑**，无需人工判断：

| # | 坑 | 现象 | 自动处理 |
|---|---|---|---|
| 1 | **venv 路径** | 成都/重庆/北京独立目录无 .venv | 优先 `../.venv/` → `./.venv/` → sys.executable |
| 2 | **--offline 缓存位置** | 缓存可能在根目录或 per-city 目录 | 两路径都查，都不存在报错列出候选路径 |
| 3 | **pycache 旧代码** | 改脚本后旧 .pyc 加载 | 启动时 `rmtree` 本目录 `__pycache__/` |
| 4 | **toc_pages 硬编码** | 成都 02 目录在 p4，硬编码 p0-3 漏识别 | `is_toc_page()` 特征识别替代硬编码 |
| 5 | **yaml schema 错** | nm_end 类型错运行时才报 | `validate_city_yaml()` 启动即校验 |

---

## 三、跑数据

### 3.1 拿到 PDF 后怎么跑

```bash
# Step 1: 把 PDF 放进目标城市的 2_输入/（或任意路径都行）

# Step 2: 进入城市目录跑 run.py
cd D:/AI学习/vs code/信息价提取/成都
../.venv/Scripts/python.exe run.py "D:/.../2026年成都市信息价06期.pdf" \
  --city 成都 --period 06 --year 2026 --data-month 5 --cycle-type 月刊
```

**判断套哪个城市脚本**：
- 看 PDF 标题（含"成都市/重庆市/北京市"字样）
- 看出版单位（"成都市住建局/重庆市住建委/北京市住建委"）
- `--city` 参数人工指定

### 3.2 离线模式（复用 OCR 缓存）

```bash
cd D:/AI学习/vs code/信息价提取/成都
../.venv/Scripts/python.exe run.py "<pdf>" \
  --city 成都 --period 06 --year 2026 --data-month 5 --cycle-type 月刊 --offline
```

跳过 step1（OCR），从 `3_中间产物/cd_06_ocr/result.json` 读缓存。

### 3.3 全流程 6 步

| 步骤 | 输出 | 耗时（成都 06 180MB）|
|---|---|---|
| step1 OCR | `cd_06_ocr/result.json` + .md + .html | ~3 分钟 |
| step2 章节分类 | `classified.json` | <10s |
| step3 字段提取 | `extract.json` | <30s |
| step4 区县识别 | `clean.json` | <10s |
| step5 残缺统计 | `5_日志/成都/06_process.log` | <5s |
| step6 写 xlsx | `成都_2026年06期_市场信息价.xlsx` | <5s |

---

## 四、章节分类原理

### 4.1 is_toc_page 算法（替代 toc_pages=4 硬编码）

**老问题**：成都 02 目录在 p4，硬编码扫描 page 0-3 漏识别 → xlsx 0 行。

**新算法**（`step2_classify.is_toc_page()`）：
- 特征 1：≥30% 行尾 "... (数字)" 格式
- 特征 2：含 "目录" 章节标题
- 特征 3：行数 > 8 且全是短行

### 4.2 nm_end 自动 fallback（替代 yaml 强配）

**老问题**：02 期 yaml 没配 nm_end → NotImplementedError 卡住。

**新逻辑**（`step2_classify.compute_nm_end()`）：
- 优先级 1：yaml `nm_end.{period}` 配了 → 用该值
- 优先级 2：未配 → 自动算 NM 章节内最大 table page_idx
- 兜底：toc_start + 6（NM 章节典型 6-10 页）

### 4.3 compute_page_offset 自动算

**问题**：成都 02 自动算 -1（错的），成都 06 自动算 +3（对的）。

**当前状态**：依赖 PC/MARKET 章节首张表反推。**已知不稳定**，待修（P2）。

---

## 五、支持城市

| 城市 | 代码 | 状态 | 06 期行数 | Sheet 数 | 入口 |
|---|---|---|---|---|---|
| 成都 | cd | ✅ 已修复 | 3907+462+66 | 3 | `成都/run.py` |
| 成都 02 | cd | ✅ 已修复 | 2939+1449+144 | 3 | 同上 |
| 重庆 | cq | ✅ 跑通 | 1488+184+130 | 3 | `重庆/run.py` |
| 北京 | bj | ✅ 跑通 | 3457 | 5 | `北京/run.py` |

---

## 六、扩展新城市（3 步）

1. **配 `{city}市.yaml`**：`districts` + `table_schemas`
2. **建 `step3_extract_{code}.py`**：复制 `step3_extract_cd.py` 改城市名
3. **注册 CITY_STEP3**：在 `{city}/run.py` 加 `"{city}": "step3_extract_{code}"`

---

## 七、已知限制

| 问题 | 影响 | 状态 |
|---|---|---|
| 成都 02 page_offset = -1 | 章节定位略偏 | 待修 P2 |
| PC schema 列切分对 8 列错位 | PC 表少数行错位 | 待修 |
| compute_page_offset 不稳定 | 不同期自动算可能 ±1 偏差 | 待修 P2 |
| 厂商报价 ≠ 新型材料 | 易混淆 | 已配 SKIP |

---

## 八、设计目标

1. **per-city 独立**：每个城市独立 `step3_extract_xx.py`，互不 import
2. **配置驱动**：表结构全部写进 `{city}市.yaml.table_schemas`
3. **跨城共享**：根 `6_配置/` 优先读，per-city 可扩展
4. **自动 fallback**：nm_end / page_offset 未配自动算（不静默错）
5. **错误可定位**：每行带 `_debug_origin` 字段
6. **人工可复核**：异常全进 `5_日志/{city}/{period}_validation_errors.csv`