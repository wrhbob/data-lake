---
name: mineru-pdf-parse
description: >-
  使用本地 MinerU（OpenDataLab）服务把 PDF 解析为 JSON + Markdown + HTML 三件套。
  输入：单个 PDF 文件路径（必填）+ 可选输出目录。
  输出：源 PDF 同位置同名文件夹下的 result.json / <stem>.md / <stem>.html。
  自动做 MinerU API 健康检查（不通直接报错），自动规避 multipart filename 中文双编码坑。
  Trigger: 用户给出 PDF 路径要求解析 / OCR / 版面还原 / 转 markdown / 抽表，
  或提到 MinerU / OpenDataLab / 矿大 / mineru-api / /file_parse 时。
allowed-tools: Bash(python *), Bash(curl *), Bash(ls *), Bash(mkdir *), Bash(cp *), Bash(rm *), Bash(docker *), Read, Write
---

# MinerU PDF 解析 Skill

把任意 PDF 交给**本地 MinerU 服务**（默认 `http://172.16.20.23:8000`）解析，
输出 `result.json` / `<stem>.md` / `<stem>.html` 到源 PDF 同位置的同名文件夹。

---


**硬性约束（违反必炸）：**
1. **禁止并发**：永远一次只跑 1 个 PDF，绝不同时开 2 个请求
2. **禁止 MinerU 服务的异步并发利用**：服务默认 `max_concurrent_requests=3`，**单人都不要触发** —— 调 `/file_parse` 时前端用同步阻塞即可，不要用异步任务队列堆积
3. **批处理必须串行**：本 skill 的 `parse_all.py` 已强制串行（一次处理完一个 PDF 再处理下一个）
4. **不要启动多个 mineru-api 进程**：单进程单服务



---


**对策：**
1. **大 PDF（>100 页）必须用 `parse_chunked.py`** —— 拆成 ~100 页/段，串行调用 `/tasks` 异步端点（HTTP 短连接，uvicorn 不会积累 buffer）。每段独立 result.json，部分失败不丢全部。
2. **同步端点 `/file_parse` 适合 ≤100 页的小 PDF**（实测 78 页 103.4s）。
3. **异步端点 `/tasks` + `/tasks/{id}/result` 适合大 PDF** —— 提交即返回 `task_id`，HTTP 立即断开，服务端继续在后台跑，结果保存 24h。

**快速选择：**

| PDF 页数 | 用什么 |
|---|---|
| ≤ 100 页 | `parse_pdf.py`（同步 `/file_parse`） |
| > 100 页 | `parse_chunked.py --chunk-size 100`（异步 `/tasks`） |

---

## 触发条件

满足任一即触发：
- 用户提供 PDF 路径，要求**解析 / 抽取 / OCR / 版面还原 / 转 markdown**
- 提到 **MinerU / OpenDataLab / 矿大 / mineru-api / /file_parse**
- 提到「PDF 识别」「版面分析」「表格识别」「公式识别」且本地有 MinerU 服务

## 输入

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `<pdf_path>` | ✅ | — | **单个** PDF 文件的**绝对路径** |
| `[output_dir]` | ❌ | `<pdf_dir>/<pdf_stem>/`（同名文件夹） | 输出目录 |

环境变量（可选）：
- `MINERU_API_URL`：MinerU API 地址，默认 `http://172.16.20.23:8000`
- `PYTHONIOENCODING=utf-8`：避免 Windows bash 打印中文路径乱码

## 输出（每个 PDF 一个同名文件夹）

| 文件 | 大小量级 | 说明 |
|---|---|---|
| `result.json` | ~25KB/页 | MinerU 完整响应（`md_content` + `middle_json` + `content_list`） |
| `<stem>.md` | ~2KB/页 | md_content 原文，HTML `<table>` 嵌入 |
| `<stem>.html` | ~2.5KB/页 | 浏览器可看的预览页（91 个表格 ≈ 180KB） |

`<stem>` 是源 PDF 的文件名去后缀（如 `重庆2026-06`），**保留中文**。

## 工作流（严格按顺序，不可跳步）

### Step 0 · 健康检查（必做，不通直接报错退出）

调 `python scripts/health_check.py`（或手动 `curl http://172.16.20.23:8000/health`）：

- ✅ `{"status":"healthy","version":"3.4.4",...}` → 继续
- ❌ 任何其他情况 → **直接报错并给出排查步骤**（见下方脚本）
  - 容器是否在跑：`docker ps | grep mineru`
  - 端口映射：`docker port PDF2Markdown`
  - API 进程：`docker exec PDF2Markdown ps -ef | grep mineru-api`
  - 重启命令：`docker exec -d PDF2Markdown bash -c 'nohup mineru-api --host 0.0.0.0 --port 8000 > /tmp/mineru-api.log 2>&1 &'`

### Step 1 · 准备输出目录

```python
pdf_path = Path(<pdf_path>).resolve()
out_dir  = Path(<output_dir>).resolve() if <output_dir> else pdf_path.parent / pdf_path.stem
out_dir.mkdir(parents=True, exist_ok=True)
```

### Step 2 · 上传 PDF（关键！避免双编码）

**绝对不要把中文 PDF 文件名直接传给 multipart 的 `filename=` 字段！**
否则 MinerU 服务端会做 double-encode（CP1252 → UTF-8），返回的 JSON `file_names`
字段会变成 `×°ÅäÊ½¨Öþ¹¤³Ì_³éÒ³_...` 乱码，导致输出 .md/.html 文件名也是乱码。

正确做法：
```python
shutil.copy2(pdf_path, out_dir / "upload.pdf")  # 永远用 ASCII 文件名
files = {"files": ("upload.pdf", f, "application/pdf")}  # multipart filename 也用 upload.pdf
```

### Step 3 · 调 `/file_parse`

默认参数（推荐 `hybrid-engine high` 模式，精度 95.39）：

```python
data = {
    "backend": "hybrid-engine",   # 必填精度最高的后端；pipeline 精度只有 86.47
    "effort": "high",             # 开图像分析，最高质量（medium 关闭）
    "parse_method": "auto",
    "return_md": "true",
    "return_middle_json": "true", # 每页所有 block 的 bbox+type
    "return_content_list": "true",# 按阅读顺序的 block 列表
    "formula_enable": "true",
    "table_enable": "true",
    "image_analysis": "true",
    "start_page_id": "0",
    "end_page_id": "99999",
}
r = requests.post(f"{api_url}/file_parse", files=files, data=data, timeout=1800)
```

⚠️ **用 Python `requests`，不要用 curl**：curl 在 Windows + Git Bash 下处理
中文路径的 multipart 编码不稳定，会间歇返回 HTTP 000（连接失败）。

### Step 4 · 写 result.json

```python
result_path = out_dir / "result.json"
result_path.write_bytes(r.content)
```

### Step 5 · 渲染 .md + .html

调 `scripts/render.py <result.json> <pdf_path>`：
- 输出文件名用源 PDF 的 `stem`（**不是** JSON 里的 `file_names` 字段，后者可能双编码）
- .md：直接写 `md_content`
- .html：把 `<table>` 抽出来嵌入预览页（含简单 CSS：表格蓝色标题、空 cell 灰色、首行浅蓝）

### Step 6 · 清理中间文件

```python
(out_dir / "upload.pdf").unlink()  # 除非 --keep-upload
```

## 已知问题（必读，已踩过的坑）

### 问题 1：JSON `file_names` 双编码乱码
- **症状**：render 出来的 .md/.html 文件名变成 `×°ÅäÊ½¨Öþ¹¤³Ì_³éÒ³_...`
- **根因**：multipart `filename="中文.pdf"` header 的字节流被服务端用 CP1252 解码又按 UTF-8 重编码
- **解决**：永远用 ASCII 文件名 `upload.pdf` 上传，输出文件再用源 PDF 的 stem 重命名（已在 `render.py` 实现）

### 问题 2：curl 在 Windows 下 multipart 失败（HTTP 000）
- **症状**：`curl -F "files=@中文.pdf" ...` 间歇返回 HTTP 000、0 bytes
- **根因**：Git Bash on Windows + curl 8.x 的 msys path 处理边界问题
- **解决**：用 Python `requests.post(url, files={"files": ("upload.pdf", f, "application/pdf")}, data=...)`

### 问题 3：Windows bash 打印中文路径变乱码
- **症状**：控制台看到 `×°ÅäÊ½¨Öþ¹¤³Ì_...`
- **根因**：Windows cmd 默认 GBK 编码
- **解决**：设 `PYTHONIOENCODING=utf-8`（`parse_pdf.py` 内部已设）

### 问题 4：首次 /file_parse 慢（~5 分钟）
- **症状**：第一次调用要 3-5 分钟
- **根因**：vLLM 模型加载（~7-8GB）
- **之后**：~1.3 秒/页（warm）
- **应对**：脚本默认 `timeout=1800`（30 分钟），足够大本 PDF

### 问题 5：`extract.py` 兼容性（如果你后续接 Step 4 抽表）
- MinerU 输出的章节标题是 markdown `## B.1 钢网架`，**不是** `<div style="text-align: center;">...</div>`
- 你现有的 `Markdown2CSV/extract.py` 只认 `<div>` 标题
- 解决：写新 `extract_mineru.py`，用 `##` / `###` 作为 region 切分点
- 或者在 render 阶段把 `##` 改成 `<div style="text-align: center;">...</div>` 注入回 md_content


---

## 调用方式

### 阶段 0：OCR 中间目录落点规范（2026-07-24 起）

> MinerU 解析产物 `<stem>.md` / `<stem>.html` / `result.json` 应统一落**流程根 OCR 中间目录**，
> 而不是 PDF 同目录（避免污染原始 PDF 目录）。详见 [CLAUDE.md §8.10](../../CLAUDE.md)。

```bash
PY=/d/miniconda3/envs/DLSE/python.exe
PYTHONIOENCODING=utf-8
PDF="/d/工程造价学习/定额解析/<某目录>/<stem>.pdf"
OCR_DIR="D:/工程造价学习/数值审核流程/OCR中间/<stem>_OCR中间"

# ≤100 页：用 parse_pdf.py 同步端点
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_pdf.py "$PDF" \
    -o "$OCR_DIR"

# >100 页：用 parse_chunked.py 分段（推荐）
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_chunked.py "$PDF" \
    --output-dir "$OCR_DIR"
# → $OCR_DIR/<stem>.md（合并后的完整 MD）
# → $OCR_DIR/<stem>.html + result.json
# → $OCR_DIR/<stem>_chunks/（段级中间产物，可选保留）
```

### A. 通过 Skill（用户说「/mineru-pdf-parse <pdf>」）
按本 SKILL.md 工作流执行：先健康检查 → 创建目录 → 调用 `scripts/parse_pdf.py <pdf> [output_dir]`。

> **自动判断**：若 PDF 页数 > 100，应主动改用 `parse_chunked.py`（见下方「大 PDF 分段」）。

### B. 直接命令行
```bash
# 用 DLSE 环境（项目里其他脚本都用的）
PY=/d/miniconda3/envs/DLSE/python.exe

# 健康检查
$PY ~/.claude/skills/mineru-pdf-parse/scripts/health_check.py

# 解析单个 PDF（≤100 页用同步端点）
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_pdf.py \
    "/d/工程造价学习/定额解析/装配式建筑工程/装配式建筑工程_抽页_22-26_35-73_81-98_104-113_120-125.pdf"

# 自定义输出目录
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_pdf.py \
    "/d/.../某PDF.pdf" "/d/.../输出目录"

# 只解析不渲染（只想要 result.json）
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_pdf.py \
    "/d/.../某PDF.pdf" --no-render

# 重新渲染（已经有 result.json，只想换 .md/.html 样式）
$PY ~/.claude/skills/mineru-pdf-parse/scripts/render.py \
    "/d/.../某PDF/result.json" "/d/.../某PDF.pdf"

# 串行批处理整个目录（严禁并行！）
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_all.py \
    "/d/工程造价学习/定额解析/装配式建筑工程/"

# 批处理 + 集中输出
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_all.py \
    "/d/.../目录1/" "/d/.../目录2/" \
    --output-base "/d/.../汇总输出/"
```

### C. 大 PDF 分段（>100 页）

```bash
# 拆成 100 页/段，串行异步处理（避免容器 RAM OOM）
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_chunked.py \
    "/d/工程造价学习/定额解析/《四川省建设工程工程量清单计价定额——房屋建筑更新改造工程》/《四川省建设工程工程量清单计价定额——房屋建筑更新改造工程》.pdf"

# 自定义每段页数（50/100/200）
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_chunked.py \
    "/d/.../某大PDF.pdf" --chunk-size 80

# 只产分块结果，不合并（部分失败场景下保留中间产物）
$PY ~/.claude/skills/mineru-pdf-parse/scripts/parse_chunked.py \
    "/d/.../某大PDF.pdf" --no-merge
```

**输出结构：**
```
<pdf_dir>/<pdf_stem>/
├── chunks/
│   ├── chunk_001_pages_001-100/
│   │   ├── chunk_001_pages_001-100.pdf   ← 拆出来的子 PDF
│   │   ├── result.json                    ← MinerU 响应
│   │   ├── chunk_001_pages_001-100.md     ← 单段 markdown
│   │   └── chunk_001_pages_001-100.html   ← 单段 html
│   ├── chunk_002_pages_101-200/
│   │   └── ...
│   └── chunk_005_pages_401-436/
│       └── ...
├── <pdf_stem>.md         ← 合并后的总 markdown（所有 chunk 的 md_content 拼接）
├── <pdf_stem>.html       ← 合并后的总 html
└── result.json            ← 合并后的 result.json（md_content + 拼接的 content_list / middle_json）
```

**中间产物保留**：`chunks/` 子目录里每段都有完整的 PDF + result.json + .md + .html。某一段失败不影响其他段，也不影响之前的合并产物。

## 脚本清单（全部在 `scripts/`）

| 脚本 | 作用 |
|---|---|
| `health_check.py` | 独立检查 MinerU API 健康，失败给排查步骤 |
| `parse_pdf.py` | 单 PDF 主入口（**≤100 页**，同步 `/file_parse` 端点） |
| `parse_async.py` | 单 PDF 异步端点入口（`/tasks`，避免长连接 uvicorn OOM） |
| `parse_chunked.py` | **大 PDF 分段串行解析**（>100 页，用 `pdf-page-extract` 拆 + `/tasks` 异步） |
| `parse_all.py` | **串行**批处理多个 PDF（严禁并行，12GB 显卡 OOM 会死机） |
| `render.py` | 独立工具：result.json → .md + .html（可复用） |

## 性能参考（实测）

| PDF | 页数 | 用时 | 模型状态 |
|---|---|---|---|
| `__p035-p073.pdf` | 3 | 5.7s | warm |
| `装配式建筑工程_抽页_22-26_35-73_81-98_104-113_120-125.pdf` | 78 | **103.4s** | warm |
| 首次任意 PDF | 任意 | ~3-5min | cold（vLLM 模型加载） |

GPU 显存峰值约 **7.8 GB**（hybrid-engine high，单 PDF，无并发）。