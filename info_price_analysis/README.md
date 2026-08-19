# 信息价分析工具（独立项目）

> 输入任意 xlsx → 输出 1 份 md 总报告
> 不与 `信息价提取/` 对接

---

## 一、一句话说明

把信息价期刊 xlsx（成都/重庆/北京/广州/武汉/湖北等多城多期）丢进 `1_输入/`，跑 `python 6_脚本/run.py`，看 `4_输出/reports/` 里的 md 总报告。

---

## 二、跑数据

### 2.1 准备

```bash
# 1. 把 xlsx 放进 1_输入/
cp "我的信息价04期.xlsx" "<项目根>/1_输入/"

# 2. 激活 venv（如果还没建）
cd "<项目根>"
python -m venv .venv
.venv/Scripts/python.exe -m pip install openpyxl pandas pyyaml jinja2
```

### 2.2 跑全流程

```bash
cd "<项目根>"
.venv/Scripts/python.exe 6_脚本/run.py --input "1_输入/我的信息价04期.xlsx"
```

### 2.3 跑单个 Phase

```bash
# Phase 0: 数据盘点
.venv/Scripts/python.exe 6_脚本/phase0_scan.py --input "1_输入/我的信息价04期.xlsx"

# Phase 0.5: 生成 manifest.csv
.venv/Scripts/python.exe 6_脚本/phase0_5_manifest.py --input "1_输入/*.xlsx"

# Phase 0.5（hash 文件首次）
.venv/Scripts/python.exe 6_脚本/phase0_5_manifest.py --input "1_输入/*.xlsx" --interactive
# 或
.venv/Scripts/python.exe 6_脚本/phase0_5_manifest.py --input "1_输入/*.xlsx" \
    --map "4bc506f3...xlsx=05,f0681342...xlsx=06"

# Phase 1: 智能列识别
.venv/Scripts/python.exe 6_脚本/phase1_columns.py --manifest "3_中间产物/manifest.csv"

# Phase 2: 多期合并 + 同义词库
.venv/Scripts/python.exe 6_脚本/phase2_match.py --manifest "3_中间产物/manifest.csv"

# Phase 2.5: pending_pool.csv
.venv/Scripts/python.exe 6_脚本/phase2_5_pending.py --manifest "3_中间产物/manifest.csv"

# Phase 3: 4 类查询 + IDW
.venv/Scripts/python.exe 6_脚本/phase3_query.py --manifest "3_中间产物/manifest.csv"

# Phase 4: md 总报告
.venv/Scripts/python.exe 6_脚本/phase4_report.py --manifest "3_中间产物/manifest.csv" --output "4_输出/reports/"
```

---

## 三、输入要求

### 3.1 文件格式

- **必须**：`.xlsx`（不支持 `.xls`、`.csv`、`.pdf`）
- **编码**：UTF-8（含中文）
- **结构**：任意结构（不挑列、不挑 sheet）

### 3.2 期数来源（3 级降级）

工具按以下优先级识别期数：

| 优先级 | 来源 | 示例 |
|---|---|---|
| L1 | 文件名 | `成都_2026年04期.xlsx` → year=2026, period=04 |
| L2 | xlsx 内部列 | 列名含"期数"/"期号"/"出版日期" |
| L3 | manifest.csv 人工映射 | `period_source=L3` |

### 3.3 多文件

```bash
# 批量跑多个 xlsx
.venv/Scripts/python.exe 6_脚本/run.py --input "1_输入/*.xlsx"
```

工具自动按期数、城市分组。

---

## 四、输出

### 4.1 md 总报告（`4_输出/reports/`）

报告文件名：`{city}_{period_range}_report_{timestamp}.md`

报告结构：

| 节 | 内容 | 受众 |
|---|---|---|
| §0 摘要 | 材料分桶 + 同物率 + 价格异常 + 区县覆盖 | 造价员**只看这一节** |
| §1 数据盘点 | 文件清单 + 总行数 + 空列 | 数据校对 |
| §2 同物匹配结果 | 同物组数 + pending 摘要 + 黑名单 | 算法审 |
| §3 价格分布 | 各材料 4 数（min/p25/median/p75/max）| 预算员 |
| §4 跨期趋势 | 同物跨期价格变化 | 趋势分析 |
| §5 区县空间插值（IDW）| 已知价 + 估算价 | 投标员 |
| §6 待人工复核项 | pending 摘要 + 异常价格 + 缺失数据 | 造价员审 |

### 4.2 manifest.csv（`3_中间产物/`）

文件元数据清单（**12 列**）：

| 列 | 来源 | 说明 |
|---|---|---|
| file_path | 扫描 | xlsx 全路径 |
| file_sha256 | 扫描 | sha256(file_content) |
| filename_period_source | L1/L2/L3 | **决定期号来源** |
| year | 识别 | 年份 |
| period | 识别 | 期数（**唯一可信源**）|
| city | 元数据 | 城市 |
| sheet_name | 配置 | 主 sheet 名 |
| row_count | 扫描 | 主 sheet 行数 |
| excel_year_ref | Excel 元数据 | 仅供参考 |
| excel_period_ref | Excel 元数据 | 仅供参考 |
| excel_month_ref | Excel 元数据 | 仅供参考 |
| notes | 用户/工具 | 备注 |

**Excel 元数据仅展示不参与判断**（Phase 0 验证 02-06 期错 2/5 次）。

### 4.3 pending_pool.csv（`5_日志/`）

待人工审核的同物匹配（13 列），详见 `项目.md §9`。

---

## 五、配置（4 个 yaml + 1 个坐标）

### 5.1 `2_库/unit_synonyms.yaml`

单位同义（含注释）：

```yaml
# 单位换算规则（重要！）
# 例：千匹 → 块 * 1000（包装单位）
# 例：T → t（同义不换算）
# 例：m³ → m³（同义不换算但写法不同）
m³:
  - 立方米
  - M3
  - 立米
kg:
  - 千克
  - KG
```

### 5.2 `2_库/material_synonyms.yaml`

材料同义（如 砼 ↔ 混凝土）。

### 5.3 `2_库/origin_synonyms.yaml`

产地同义（仅展示，不参与匹配）。

### 5.4 `2_库/known_different.yaml`

黑名单（已知不同物，如 商品混凝土 ↔ 沥青混凝土）。

### 5.5 `coords/chengdu_districts.yaml`

成都 17 区县坐标（V1 必填，V2 扩 6 城）：

```yaml
districts:
  锦江区: {lat: 30.6586, lon: 104.0812}
  青羊区: {lat: 30.6746, lon: 104.0617}
  金牛区: {lat: 30.6938, lon: 104.0525}
  武侯区: {lat: 30.6424, lon: 104.0648}
  成华区: {lat: 30.6603, lon: 104.1011}
  龙泉驿区: {lat: 30.5564, lon: 104.2745}
  青白江区: {lat: 30.8836, lon: 104.2549}
  新都区: {lat: 30.8241, lon: 104.1586}
  温江区: {lat: 30.6796, lon: 103.8367}
  双流区: {lat: 30.5736, lon: 103.9235}
  郫都区: {lat: 30.8088, lon: 103.9019}
  都江堰市: {lat: 30.9884, lon: 103.6469}
  彭州市: {lat: 30.9901, lon: 103.9417}
  邛崃市: {lat: 30.4133, lon: 103.4644}
  崇州市: {lat: 30.6301, lon: 103.6713}
  简阳市: {lat: 30.4012, lon: 104.5494}
  金堂县: {lat: 30.8580, lon: 104.4120}
  新津区: {lat: 30.4158, lon: 103.8117}
  大邑县: {lat: 30.5850, lon: 103.5218}
  蒲江县: {lat: 30.1980, lon: 103.5158}
  天府新区成都直管区: {lat: 30.4500, lon: 104.0500}
  东部新区: {lat: 30.2400, lon: 104.4500}
```

---

## 六、故障排查

### 6.1 期数识别不到

**现象**：manifest.csv `filename_period_source=L2`（hash 命名未标）或 `period` 为空。

**新逻辑（L1/L2/L3 三级）**：

| 级别 | 触发 | 输出 |
|---|---|---|
| L1 | 文件名匹配 `[一-龥]*第?\d{1,2}期` | `period_source=L1`（自动） |
| L2 | 文件名不匹配（即 sha256 命名）| 提示 `--interactive` 或 `--map` 标注 |
| L3 | 任何时候用户编辑 manifest.csv | `period_source=L3`（escape hatch）|

**Excel 元数据（出版期/数据月份）**：不参与判断，仅写入 `excel_*_ref` 列展示。

**修复 hash 文件标注**：
```bash
# 方式 A: 交互模式
.venv/Scripts/python.exe 6_脚本/phase0_5_manifest.py --input "1_输入/*.xlsx" --interactive

# 方式 B: 命令行批量
.venv/Scripts/python.exe 6_脚本/phase0_5_manifest.py --input "1_输入/*.xlsx" \
    --map "4bc506f3...xlsx=05,f0681342...xlsx=06"
```

标注缓存：`3_中间产物/period_mapping.yaml`（按 sha256 索引，永久记）。

### 6.2 同物匹配率低（< 70%）

**现象**：pending_pool.csv 累积太多。

**排查**：
1. 检查 `2_库/material_synonyms.yaml` 是否覆盖常用变体（砼、混凝土、商品混凝土）
2. 检查 spec 列是否规范（NFKC 归一化后是否还有大小写/空格差异）
3. 临时调高 Jaccard 阈值（不推荐，应优先审 pending）

### 6.3 IDW 估算缺失

**现象**：报告 §5 标 "IDW 不可用"。

**原因**：
- 坐标文件缺失 → 补 `coords/{city}_districts.yaml`
- 已知区县 < 3 个 → 标"低置信度"，不出估算

### 6.4 价格区间被误判

**现象**：银杏 3200-5150 解析成 0。

**原因**：未走区间拆分逻辑（坑 10）。

**修复**：升级到支持区间拆分（v1.1+）。

### 6.5 GBK 编码报错

**Windows 终端**：
```bash
set PYTHONIOENCODING=utf-8
.venv/Scripts/python.exe 6_脚本/run.py --input "1_输入/我的信息价04期.xlsx"
```

或在脚本头部加：
```python
import sys
sys.stdout.reconfigure(encoding='utf-8')
```

---

## 七、扩展（v2 计划）

| 模块 | 触发条件 |
|---|---|
| 跨城对比（6 城）| V1 成都验证通过 |
| 6 城坐标录入 | 跨城对比启动 |
| 时间预测（ARIMA/趋势线）| 用户明确要求 |
| 图表输出（matplotlib）| 用户明确要求 |
| Web UI | 命令行不够用时 |
| 智能列识别 v2（学习用户修正）| pending_pool 决策 ≥ 50 条 |

详见 `项目.md §12`。

---

## 八、版本

v1.0（2026-08-14 16:28）— 初版
v1.1（2026-08-14 17:00）— manifest.csv 8→12 列 + §6 Excel 元数据脱钩 + §6.1 排错更新

---

**问题？查 `项目.md` 完整设计哲学 + 决策 + 风险边界。**