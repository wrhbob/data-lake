# quota v0.4 接入网站 — 审核 Brief

> 用途：把本轮（mock 模式接网站 + 3 个 Bug 修复）的改动和回归脚本交给另一台 AI，
> 让它能独立跑一遍冒烟测试、找出残余问题。
>
> 状态：**待审核**（未 git commit）
>
> 作者：Claude（MiniMax-M3），2026-07-30

---

## 0. 一句话总结

把 `quota_parser` v0.3 包（v0.3 已落地的 13 列 parse_*、7 个端点、mock runner、adapter service）通过
新增的 HTTP 客户端 + UI 接线，接入 file-asset web 服务，使用 QUOTA_PARSE_MOCK=1 让端到端流程
**无需真 worker 也能跑通**（阶段 A OCR + 抽取、阶段 B reviewed 落 final、删除解析结果）。

同轮修了 3 个 Bug：
- **Bug#1** 列表永远显示"未解析"（Pydantic 剥字段）
- **Bug#2** file_count 永远 = 1（没算 candidate/final 产物）
- **Bug#3** 上传 reviewed.xlsx 后下载 final.xlsx 仍是 mock 占位（mock runner 丢弃 reviewed_bytes）

---

## 1. 改动文件清单

### 1.1 新建（1 个）

| 绝对路径 | 行数 | 内容 |
|---|---|---|
| `D:\工程造价学习\data_lake0714\data_lake0714\file_asset_service\app\ui\quota-parse-api.js` | 228 | 7 个解析端点的 HTTP 客户端（trigger / candidate / reviewed / final / manifest / qa-report / parse-delete），五态契约（READY/UNAVAILABLE/UNAUTHORIZED/ERROR/UNKNOWN），blob 下载工具 `saveBlob` |

### 1.2 修改（6 个）

| 绝对路径 | 关键行 | 改动 |
|---|---|---|
| `file_asset_service/app/ui/quota-ui.js` | L78 | `resolveUiStatus` 改读 `row.parse_status \|\| row.status`（**Bug#1 关键修复**） |
| 同上 | L161/168/175 | 渲染 `parse-delete` 危险菜单项（4 个状态都加） |
| 同上 | L1084-1117 | `submitDeleteParse`：调 API → 成功 reloadArchives / 失败弹错 |
| 同上 | L2239-2250 | `parse-delete` 动作分支：开删除 modal |
| 同上 | L2267-2270 | `submit-delete-parse` 动作分支：modal 确认回调 |
| 同上 | L2658-2661, 2702-2748 | 注册 `quota:parse-action` 事件 + `handleParseAction`（4 个分支：candidate/final/manifest/qa） |
| 同上 | `ensureInit()` 早期 | 注入 `state.parseApi = createQuotaParseApi({})` |
| `file_asset_service/app/ui/index.html` | `<script>` 块 + version | 注册 `quota-parse-api.js?v=20260730-parse-wiring-v1` |
| `file_asset_service/app/quota_api.py` | L379, 454 | `list_quota_archives` / `get_quota_archive_detail` 加 8 个 parse 字段 + file_count 公式 |
| `file_asset_service/app/archive_service.py` | L227 | `_serialize_archive_base` 加 8 个 parse 字段（**Bug#1 第二层修复**） |
| 同上 | L465 | `_archive_summary_rows` 加 parse_artifact_count 到 file_count（**Bug#2 修复**） |
| `file_asset_service/app/schemas.py` | L363-377 | `ArchiveSummaryResponse` 加 8 个 parse 字段（**Bug#1 第三层修复**，缺这一层 Pydantic 剥字段） |
| `file_asset_service/app/mock_parse_runner.py` | L236-249 | `run_mock_pipeline_b` 优先用 `reviewed_bytes`，不再丢弃（**Bug#3 修复**） |

> 注：`@router.post("/archives/{archive_id}/parse/delete")` 后端端点（quota_api.py:1727）**本轮前已存在**，本次只接入前端。

---

## 2. 关键 Bug 三层修复示意图（重点给审核 AI 看）

Bug#1 涉及 3 个文件，不是单点：

```
ArchiveRow (DB)
    │ SQLAlchemy ORM 取出的 archive 有 parse_status
    ▼
archive_service._serialize_archive_base  ← 第二层修复：把 parse_* 加进 dict
    │
    ▼
quota_api.list_quota_archives items[...]  ← 第三层修复（冗余）：再打包一次
    │
    ▼
Pydantic ArchiveSummaryResponse (response_model=list[...])  ← 第一层修复：声明字段
    │
    ▼  不剥字段
前端 response.parse_status === 'parsed' → 渲染「已解析」色块
```

如果只修中间层、不修 schema 层，Pydantic 会把 parse 字段剥掉 → 前端拿不到 → Bug#1 现象不变。

---

## 3. 操作示范（端到端冒烟）

### 3.1 前置条件

```bash
# 服务已在 8010 端口运行（mock 模式已开）
# 验证：
curl -sS http://localhost:8010/api/archives?limit=1 | head -c 200
# 应该返回 JSON 列表，不报错
```

如果服务没跑：

```bash
PY=/d/miniconda3/envs/file-asset/python.exe
cd /d/工程造价学习/data_lake0714/data_lake0714
set -a; . ./.env; set +a
export QUOTA_PARSE_MOCK=1
"$PY" file_asset_service/serve.py > /tmp/serve.log 2>&1 &
# 等 5s
curl -sS http://localhost:8010/api/archives?limit=1 | head -c 200
```

### 3.2 准备一个测试 PDF

```bash
# 用任意小 PDF（10-50 页都行，没有就用以下任选）
# 这里用项目里的 quota 种子
ls /d/工程造价学习/data_lake0714/data_lake0714/quota/四川2025/*.pdf 2>/dev/null
# 找一个复制到 /tmp/test.pdf（别动原件）
cp "/d/工程造价学习/data_lake0714/data_lake0714/quota/四川2025/四川省建设工程工程量清单计价定额(2025).pdf" /tmp/test_review.pdf 2>/dev/null || \
  ls -la /d/工程造价学习/data_lake0714/data_lake0714/quota/四川2025/ | head
```

如果 `/tmp/test_review.pdf` 不存在，告诉用户拿一个 PDF 给你。

### 3.3 端到端 5 阶段脚本

把下面整段复制到 `C:/temp/review_smoke.sh`，然后 `bash C:/temp/review_smoke.sh` 跑：

```bash
#!/usr/bin/env bash
# review_smoke.sh — quota v0.4 mock 模式端到端冒烟
# 审核 AI 重点关注每个阶段的 EXPECTED 块

set -u
PY=/d/miniconda3/envs/file-asset/python.exe
HOST=http://localhost:8010
TEST_PDF="/tmp/test_review.pdf"
TEST_REVIEWED="C:/temp/reviewed.xlsx"
mkdir -p /c/temp

echo "════════════════════════════════════════════"
echo " Phase 1 — 上传测试 PDF + 建档案"
echo "════════════════════════════════════════════"

UPLOAD=$(curl -sS -X POST "$HOST/api/data-lake/quota/upload" \
  -F "files=@${TEST_PDF}" \
  -F "category=construction_quota" \
  -F "province=sc" \
  -F "year=2026")

echo "$UPLOAD" | head -c 800
echo ""

AID=$(echo "$UPLOAD" | python -c "import sys,json; d=json.load(sys.stdin); print(d['items'][0]['archive_id'])" 2>/dev/null)
if [ -z "$AID" ]; then
  echo "✗ 上传失败：无法取 archive_id"
  exit 1
fi
echo "✓ archive_id = $AID"

echo ""
echo "════════════════════════════════════════════"
echo " Phase 2 — 阶段 A：触发解析 + 验证状态"
echo "════════════════════════════════════════════"

curl -sS -X POST "$HOST/api/data-lake/quota/archives/$AID/parse" \
  -H "Content-Type: application/json" -d '{"profile":"sichuan"}' | head -c 500
echo ""

echo "等 mock_a 完成（5s sleep）..."
sleep 6

# EXPECTED: parse_status='parsed', file_count = 1 + 1 = 2 (PDF + candidate.xlsx)
DETAIL=$(curl -sS "$HOST/api/data-lake/quota/archives/$AID")
echo "phase2 detail = $DETAIL" | head -c 600
echo ""
STATUS=$(echo "$DETAIL" | python -c "import sys,json; print(json.load(sys.stdin).get('parse_status'))")
FC=$(echo "$DETAIL" | python -c "import sys,json; print(json.load(sys.stdin).get('file_count'))")
echo "→ parse_status=$STATUS (expect 'parsed'), file_count=$FC (expect 2)"
[ "$STATUS" = "parsed" ] && [ "$FC" = "2" ] && echo "✓ Phase 2 PASS" || echo "✗ Phase 2 FAIL"

echo ""
echo "════════════════════════════════════════════"
echo " Phase 3 — 下载 candidate.xlsx + 验证不是空"
echo "════════════════════════════════════════════"

curl -sS "$HOST/api/data-lake/quota/archives/$AID/candidate.xlsx" -o /tmp/cand.xlsx
file /tmp/cand.xlsx  # 期望: Microsoft Excel
[ -s /tmp/cand.xlsx ] && echo "✓ candidate.xlsx 非空" || echo "✗ candidate.xlsx 空"

echo ""
echo "════════════════════════════════════════════"
echo " Phase 4 — 阶段 B：上传 reviewed.xlsx + 验证 final 含真内容"
echo "════════════════════════════════════════════"

# 构造一个 reviewed.xlsx，sheet 名是 'review_sheet_42', 第一行 6 个有特征的 cell
"$PY" -c "
from openpyxl import Workbook
wb = Workbook(); ws = wb.active; ws.title = 'review_sheet_42'
ws.append(['已校对', 'M30', 'm3', '560.00', '630.00', '已修正'])
ws.append(['钢管', 'DN50', 'm', '32.50', '36.80', '薄壁'])
ws.append(['螺纹钢筋', '20号', 't', '3850', '4350', 'HRB400'])
wb.save(r'${TEST_REVIEWED}')
print('reviewed.xlsx written, size =', __import__('os').path.getsize(r'${TEST_REVIEWED}'))
"

curl -sS -X POST "$HOST/api/data-lake/quota/archives/$AID/reviewed" \
  -F "file=@${TEST_REVIEWED}" | head -c 300
echo ""

echo "等 mock_b 完成（2s sleep）..."
sleep 4

# 验证 final.xlsx 含真内容
curl -sS "$HOST/api/data-lake/quota/archives/$AID/final.xlsx" -o /tmp/final.xlsx
file /tmp/final.xlsx
"$PY" -c "
from openpyxl import load_workbook
wb = load_workbook('/tmp/final.xlsx')
ws = wb.active
print(f'sheet={ws.title!r} dims={ws.dimensions}')
for row in ws.iter_rows(min_row=1, max_row=3, values_only=True):
    print(' ', row)
# EXPECTED: sheet='review_sheet_42', 第一行首列='已校对'
assert ws.title == 'review_sheet_42', f'sheet name wrong: {ws.title!r}'
assert ws.cell(1,1).value == '已校对', f'first cell wrong: {ws.cell(1,1).value!r}'
print('✓ Phase 4 PASS — final.xlsx 含真 reviewed 内容')
"

DETAIL=$(curl -sS "$HOST/api/data-lake/quota/archives/$AID")
STATUS=$(echo "$DETAIL" | python -c "import sys,json; print(json.load(sys.stdin).get('parse_status'))")
FC=$(echo "$DETAIL" | python -c "import sys,json; print(json.load(sys.stdin).get('file_count'))")
echo "→ parse_status=$STATUS (expect 'qa_passed'), file_count=$FC (expect 3 = PDF + candidate + final)"
[ "$STATUS" = "qa_passed" ] && [ "$FC" = "3" ] && echo "✓ Phase 4 后状态 PASS" || echo "✗ Phase 4 后状态 FAIL"

echo ""
echo "════════════════════════════════════════════"
echo " Phase 5 — 删除解析结果 + 验证回到 None"
echo "════════════════════════════════════════════"

curl -sS -X POST "$HOST/api/data-lake/quota/archives/$AID/parse/delete" | head -c 300
echo ""

DETAIL=$(curl -sS "$HOST/api/data-lake/quota/archives/$AID")
STATUS=$(echo "$DETAIL" | python -c "import sys,json; print(json.load(sys.stdin).get('parse_status'))")
FC=$(echo "$DETAIL" | python -c "import sys,json; print(json.load(sys.stdin).get('file_count'))")
echo "→ parse_status=$STATUS (expect None), file_count=$FC (expect 1 = PDF only)"
[ "$STATUS" = "None" ] && [ "$FC" = "1" ] && echo "✓ Phase 5 PASS — 回到未解析" || echo "✗ Phase 5 FAIL"

echo ""
echo "════════════════════════════════════════════"
echo " Phase 6 — 反向：非法 profile 应该 422"
echo "════════════════════════════════════════════"

HTTP=$(curl -sS -o /dev/null -w "%{http_code}" -X POST "$HOST/api/data-lake/quota/archives/$AID/parse" \
  -H "Content-Type: application/json" -d '{"profile":"default"}')
echo "→ HTTP $HTTP (expect 422)"
[ "$HTTP" = "422" ] && echo "✓ Phase 6 PASS" || echo "✗ Phase 6 FAIL"

echo ""
echo "════════════════════════════════════════════"
echo " 全部完成。test archive_id = $AID"
echo "════════════════════════════════════════════"
```

### 3.4 期望输出（摘要）

```
✓ archive_id = 5e9f...
✓ Phase 2 PASS — parse_status=parsed, file_count=2
✓ candidate.xlsx 非空
✓ Phase 4 PASS — final.xlsx 含真 reviewed 内容
✓ Phase 4 后状态 PASS — qa_passed, file_count=3
✓ Phase 5 PASS — 回到未解析, file_count=1
✓ Phase 6 PASS — 非法 profile 422
```

任何一个 ✗ 都要标出来，并把当时的 HTTP 响应 body 贴出来。

---

## 4. 给审核 AI 重点关注的 5 个风险点

按优先级排：

### R1. 三层 Bug 修复是否真的覆盖？
- 读 [schemas.py:363-377](file_asset_service/app/schemas.py#L363) 确认 8 个 parse 字段都已声明
- 读 [archive_service.py:227-241](file_asset_service/app/archive_service.py#L227) 确认 ORM 输出有这些字段
- 读 [quota_api.py:379](file_asset_service/app/quota_api.py#L379) 和 [quota_api.py:454](file_asset_service/app/quota_api.py#L454) 确认 list/detail 也加了
- **重点**：直接 `curl /api/archives?limit=20` 看返回 dict 里有没有 `parse_status` key

### R2. mock runner 真的用了 reviewed_bytes？
- 读 [mock_parse_runner.py:240-245](file_asset_service/app/mock_parse_runner.py#L240)
- 确认分支 `if reviewed_bytes:` 存在
- 跑 Phase 4 的脚本，看 final.xlsx 内容是不是你上传的（不是 mock-row 占位）

### R3. file_count 公式对吗？
- 期望：PDF + (candidate 有则 1) + (final 有则 1) = 1 + 1 + 0 = 2（阶段 A 后），1 + 1 + 1 = 3（阶段 B 后），1 + 0 + 0 = 1（删除后）
- 读 [archive_service.py:465](file_asset_service/app/archive_service.py#L465) 和 [quota_api.py:379, 454](file_asset_service/app/quota_api.py#L379)
- 检查：上传时 PDF 是 ArchiveFile（1），阶段 A 后 candidate_xlsx_key 有值（+1），阶段 B 后 final_xlsx_key 有值（+1）

### R4. 前端 state machine 映射对吗？
- 读 [quota-ui.js:78](file_asset_service/app/ui/quota-ui.js#L78) 的 `resolveUiStatus`
- 期望：None → 'pending'，'parsing' → 'parsing'，'parsed' → 'review'，'qa_passed' → 'done'，'rejected' / 'failed' → 'failed'
- 检查 PARSE_STATUS_VARIANT 表（在前文附近）

### R5. 删除解析结果真的清 13 个字段吗？
- 读 [quota_api.py:1727-1781](file_asset_service/app/quota_api.py#L1727)
- 期望：parse_status / parse_profile / parse_task_id / parse_phase / parse_parser_version / parse_started_at / parse_finished_at / parse_metrics / parse_warnings / parse_error_code / parse_error_message / candidate_xlsx_key / final_xlsx_key 全部置 None
- **确认没误删 metadata_payload.province**（这是 §9 #15 省份透传字段，不能清）

---

## 5. 不要触碰的事

- **不要**触发 `git commit`（用户还没确认要 commit）
- **不要**重启服务（前端 JS 改动 FastAPI 不缓存，浏览器刷新即可生效；后端 Python 改动本轮不需要）
- **不要**改 MinIO 上的真实 PDF（上传用 `province=sc` 是落到现有桶，注意别覆盖用户的真档案）
- **不要**触碰 `quota_parser_worker.py`（真 worker 还没接入，本轮只测 mock）

---

## 6. 失败排查快速指引

| 现象 | 第一检查点 |
|---|---|
| Phase 2 parse_status 还是 None | R1 — 八成是 schemas.py 没改 |
| Phase 4 final.xlsx 是 mock-row 占位 | R2 — mock runner 的 if 分支有没有 |
| Phase 4 file_count 还是 1 | R3 — archive_service.py:465 那行 |
| Phase 5 file_count 不是 1 | R3 + R5 — delete 端点清字段顺序 |
| Phase 6 不是 422 | 那是 §3 端点校验不在本轮范围，但顺带看一下 quota_api.py:1433 附近 |
| 浏览器前端「未解析」还在 | 硬刷新（Ctrl+Shift+R）清 JS 缓存；index.html 的 `__uiAssetVersion` 是不是 `20260730-parse-wiring-v1` |

---

## 7. 已知不在本轮范围的问题（审核时跳过）

1. **MinIO 上 candidate/final.xlsx 不会被 `parse-delete` 清掉** —— 后端只清 DB 指针，是 [quota_api.py:1737-1740](file_asset_service/app/quota_api.py#L1737) 的设计决定，留给"加 ObjectStore.delete_object"那批
2. **真 worker（quota_parser_worker.py 进程 + quota_parse_job 表）还没接入** —— mock runner 是占位
3. **meta.json `page_continuation_flags` 不可靠** —— PDF2Excel 项目历史问题，与本项目无关

---

## 8. 配套 SPEC

如果审核 AI 想看规格背景：
- 入口：[file_asset_service/CLAUDE.md](file_asset_service/CLAUDE.md) §2 v0.4 mock 模式说明
- quota 接入 SPEC：[docs/quota_archive_spec_v0.1.md](docs/quota_archive_spec_v0.1.md) — 13 列 parse_* 字段定义
- 完整实现报告（本轮没写，留待审核后合并）：`docs/quota_v0.4_implementation_report.md`（占位文件名）