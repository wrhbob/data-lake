# quota-compare · 跨省定额对比

造价师跨省查同义定额的小工具。从 4 省 9 册广联达导出 xlsx 里抽取指定主题，生成单 sheet 跨省对比 xlsx。

## 文件清单

```
quota-compare/
├── README.md                 # 本文件
├── extract.py                # 通用抽取脚本（硬匹配 + CLI 兜底）
├── 原始样本/                  # 4 省 9 册广联达 xlsx（输入数据源）
│   ├── 四川上.xlsx / 四川下.xlsx
│   ├── 广东上.xlsx / 广东中.xlsx / 广东下.xlsx
│   ├── 河南上.xlsx / 河南下.xlsx
│   └── 重庆上.xlsx / 重庆下.xlsx
├── 人工挖土方_跨省对比.xlsx    # 74 条命中（四川 12 / 广东 22 / 河南 27 / 重庆 13）
└── 踢脚线_跨省对比.xlsx       # 42 条命中（四川 6 / 广东 20 / 重庆 16）
```

> 两个 xlsx 都未被 Git 跟踪（`quota-compare/` 整体 untracked），改动靠人工 .xlsx 备份。

## 调用方法

### 单关键词查询

```bash
cd quota-compare
PYTHONIOENCODING=utf-8 /d/miniconda3/envs/file-asset/python.exe extract.py "水磨石"
```

### 加扩展词（OR 关系）

```bash
python extract.py "踢脚" --any "踢脚线 踢脚板"
```

### 加排除词（防误伤）

```bash
python extract.py "挖" --any "土 淤泥 冻土 沟槽 基坑 槽坑" \
                    --exclude "机械 挖掘机 挖孔 钻"
```

### 批量跑内置主题

```bash
python extract.py --all
```

主题表定义在 [extract.py](extract.py) 顶部的 `TOPICS` 常量里，加新主题就追加一行：

```python
TOPICS = [
    ("踢脚", "踢脚线对比",     "踢脚线_跨省对比",     "踢脚线 踢脚板",                  ""),
    ("挖",   "人工挖土方对比",  "人工挖土方_跨省对比",   "土 淤泥 冻土 沟槽 基坑 槽坑",    "机械 挖掘机 挖孔 钻"),
    ("水磨石", "水磨石对比",    "水磨石_跨省对比",      "",                              ""),  # 新增
]
```

## 匹配规则（短路求值）

1. `keyword` 必须在定额「名称」里出现
2. 任一 `exclude` 词出现 → 排除
3. 若给了 `--any`，至少一个 `any` 词必须出现

## 加新主题的工作流

1. 追加 `extract.py` 的 `TOPICS` 一行
2. `python extract.py --all` 重新生成所有主题的 xlsx
3. 看新 xlsx 命中清单（脚本会按省列出来）

或者走"快速单次"模式（不入 TOPICS 表）：

```bash
python extract.py "防水卷材" --any "SBS APP 自粘" --title "防水卷材对比" --stem "防水_跨省对比"
```

## 设计原则

- **零配置文件**：避免 JSON/YAML 配置带来的额外复杂度
- **硬匹配 > DSL**：每个主题的关键词逻辑由调用方传入，脚本只做最薄一层
- **共用一份 PROVINCES**：所有主题都跑同一份 9 册样本，省×册映射不参数化
- **保留定额结构**：段祖先 + 定 + 工料机子行的层级原样透传，附加省份列
- **省份去重**：同一省份多册间重复编码的定额只写一次
- **Office 兼容**：以 `=` 开头的字符串强制 `data_type='s'` 避免 Excel 弹「需要修复」

## 已知限制

- 仅四川/广东/河南/重庆 4 省样本，其他省份需要先解析 PDF 入湖
- 河南样本（房屋建筑更新改造工程）未含装饰章节，所以踢脚线类 0 命中；要补装饰专册
- 样本 PDF 可能存在章节覆盖差异（不同省份的"房屋建筑与装饰工程"分册边界不一致）
