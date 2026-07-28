# quota_parser 集成 Plan（2026-07-28 锁定）

> 目标：把已有的 `quota_parser` Python 包（v0.3 冻结，见 [`parser/SPEC.md`](parser/SPEC.md)）
> 嵌入 file-asset service，让用户在网站上触发、监控、下载、上传解析任务全流程。
>
> 配套 SPEC：
> - 后端脚本契约：`quota/parser/SPEC.md` v0.2 + §21 v0.3 变更
> - 网站前端契约：`quota/web-frontend/SPEC.md` v0.3 + v0.3.1
> - Worker 集成层决策：`quota/README.md` §12.G

---

## 0. 现状（截至 2026-07-28）

| 项 | 状态 |
|---|---|
| `quota_parser` 包源码 | ✅ v0.3 已冻结（含 `run_quota_pipeline` / `finalize_reviewed_xlsx` / `cleanup_workspace`） |
| `quota_parser` 包打包 | ❌ 无 `pyproject.toml` / 无 `setup.py`；目前只能通过 `PYTHONPATH` 直接 import |
| file-asset 环境 `quota_parser` 可 import？ | ❌ 未安装（环境装的是 `file_asset_service` editable，site-packages 找不到 quota_parser） |
| `quota/parser/quota_parser/external/` 复用层 | ✅ 已迁入（`mineru_pdf_parse` / `quota_md_to_csv_v2` / `quota_csv_finalize`） |
| `Archive` 表 parse 子对象 | ❌ 无（web-frontend SPEC §6.1 列了 13 列 `parse_*`，未实施） |
| `quota_api.py` 7 个解析端点 | ❌ 无（现有端点只覆盖入湖/档案台/统计） |
| `file_asset_service/app/quota_parser/` adapter 子包 | ❌ 无 |
| mock 模式 (`QUOTA_PARSE_MOCK=1`) | ❌ 无 |
| 前端解析区块（`quota-parse.js / quota-parse-api.js / quota-parse-mock.js`） | ❌ 无 |
| 真实 Worker 轮询 | ❌ `serve_worker()` 是占位实现 |

**核心问题**：脚本侧 v0.3 完整、网站侧 0 落地。今天打通 web 端最小可跑链路。

---

## 1. 集成架构（数据流总览）

```
用户在档案详情点「开始解析」
  │
  │  HTTP POST /api/data-lake/quota/archives/{archive_id}/parse
  ▼
quota_api.py parse 端点
  │
  │  写 Archive.parse_status='parsing', parse_task_id=...
  │  触发后端"解析引擎"：真实 worker 或 mock runner
  ▼
┌─────────────────────────────────────────────────────────────────┐
│  解析引擎（今天是 mock；后续切真 worker）                         │
│                                                                 │
│  [MOCK 分支]  QUOTA_PARSE_MOCK=1                                │
│    → asyncio.create_task(run_mock_pipeline(archive_id))         │
│    → sleep 5-10s → 填 parse_* 13 列 + 写假 candidate.xlsx      │
│                                                                 │
│  [真分支]  独立 quota_parser worker 进程（未来 step）             │
│    → 轮询 FileProcessing 表（status='pending' + quota 域）      │
│    → lease → 下载 PDF → run_quota_pipeline → 上传 MinIO → 写库 │
│    → cleanup_workspace(success=...)                            │
└─────────────────────────────────────────────────────────────────┘
  │
  │  候选产物：candidate.xlsx / final.xlsx / manifest.json /
  │           qa_report.json / qa_report.md  → 全部上传 MinIO
  │  状态：Archive.parse_status + parse_* 13 列
  ▼
用户在档案详情（或列表操作列）看到解析状态推进：
  未解析 → 解析中 → 待审核 → 已完成
                ↓
            解析失败（红 banner + 错误原因）
```

**数据契约**（前端只读，不转换）：
- `GET /api/data-lake/quota/archives/{id}` 返回 ArchiveDetail，**追加 `parse` 子对象**（web-frontend SPEC §6.1）
- 7 个解析端点统一挂在 `/api/data-lake/quota/archives/{id}/*`（与现有 quota_api.py 同 prefix）
- 字段名与 Manifest（`quota-parser-result/v1` schema）一一对齐

---

## 2. 关键数据模型决策

### 2.1 status 字段命名（开放问题 #1，已选方案）

**矛盾点**：
- web-frontend SPEC §6.1 描述：`archive.status` 是主驱动状态机（`registered → parsing → parsed → qa_passed → usable`）
- 现状：`Archive.status` CheckConstraint 已绑定 cost_info 域 8 个状态（`discovered / collecting / collect_failed / quarantined / collected / pending_tag / archived / ready_for_governance`）
- 决策方向冲突：扩展 CK 与 cost_info 共存 vs 新增 quota 专属字段

**方案 A（保守，**推荐**）**：新增 `parse_status` String 字段（无 CheckConstraint），独立于 `status`。

| 字段 | 含义 | 与 cost_info 冲突？ |
|---|---|---|
| `Archive.status`（已有） | cost_info 域主状态机 | — |
| `Archive.parse_status`（新加） | quota 域解析子状态机；枚举：`pending / parsing / parsed / qa_passed / usable / failed_user / failed_permanent / transient` | ❌ 零冲突 |

**方案 B（激进）**：扩展 `ck_archive_status` 加入 quota 7 个新状态。cost_info 与 quota 走同一 `status` 列，靠 `domain_type` 区分。与 SPEC §6.1 字面一致，但污染 cost_info 域的 CheckConstraint 语义。

**今天按方案 A 实施**。后续如要把 SPEC §6.1 / §7.1 改为 `archive.status`，再做方案 B 的迁移脚本。

### 2.2 Archive 新增 13 列（parse_*）

照搬 web-frontend SPEC §6.1：

| 列名 | 类型 | 对应 Manifest 字段 |
|---|---|---|
| `parse_status` | `String(32)`（独立于 `status`，见 §2.1） | `archive.status` 推进时的"子状态" |
| `parse_profile` | `String(32)` | `manifest.profile` |
| `parse_task_id` | `String(64)` | `manifest.task_id` |
| `parse_phase` | `String(16)` | `manifest.phase` |
| `parse_parser_version` | `String(32)` | `manifest.parser_version` |
| `parse_started_at` | `DateTime(timezone=True)` | Worker 侧 |
| `parse_finished_at` | `DateTime(timezone=True)` | Worker 侧 |
| `parse_metrics` | `JSON`（nullable） | `manifest.metrics` |
| `parse_warnings` | `JSON`（nullable） | `manifest.warnings` |
| `parse_error_code` | `String(32)` | `transient / failed_permanent / failed_user / null` |
| `parse_error_message` | `Text` | — |
| `candidate_xlsx_key` | `String(512)` | MinIO key：`quota/<archive_id>/candidate.xlsx` |
| `final_xlsx_key` | `String(512)` | MinIO key：`quota/<archive_id>/final.xlsx` |

注：原 SPEC §6.1 写 `parse_status` 用 `archive.status` 映射——这里改成新加独立字段（§2.1）。SPEC §6.1 表述待 step 4 后修订。

### 2.3 MinIO bucket / key 布局（**复用** cost 域决策）

**2026-07-28 决策**：quota 解析产物**复用** cost 域已有的 `cost-extract` / `cost-report` 桶（quota/README.md §12.G 决策 4），不新增桶。靠 `key` 前缀 `quota/...` 与 cost 域产物 `cost/...` 区分。

| bucket | key | 写入方 | 生命周期 |
|---|---|---|---|
| `cost-extract`（**复用**） | `quota/<archive_id>/candidate.xlsx` | 阶段 A worker / mock | 与 archive 同生命周期 |
| `cost-extract`（**复用**） | `quota/<archive_id>/final.xlsx` | 阶段 B worker / mock | 与 archive 同生命周期 |
| `cost-report`（**复用**） | `quota/<archive_id>/manifest.json` | 阶段 A | 与 archive 同生命周期 |
| `cost-report`（**复用**） | `quota/<archive_id>/qa_report.json` | 阶段 A / B | 与 archive 同生命周期 |
| `cost-report`（**复用**） | `quota/<archive_id>/qa_report.md` | 阶段 A / B | 与 archive 同生命周期 |

adapter 层两个常量（`PARSE_BUCKET_CANDIDATE` / `PARSE_BUCKET_REPORT`），如未来要拆桶单点改即可。

---

## 3. 完整 18 步路线图（按依赖排序）

### 阶段 1 · 数据库 schema（1 步）

| # | 任务 | 状态 |
|---|---|---|
| **1** | `models.py` 在 `Archive` 表加 `parse_status` + 12 个 `parse_*` 列；写完 `init_db()` 跑一次 | 🔵 今天 |

### 阶段 2 · quota_parser 包打包 + 安装（2 步）

| # | 任务 | 状态 |
|---|---|---|
| **2a** | `quota/parser/` 加 `pyproject.toml`（最小包定义；`pip install -e .` 模式） | 🔵 今天前置 |
| **2b** | file-asset 环境装 `quota_parser` editable；验证 `import quota_parser` 通过 | 🔵 今天前置 |

### 阶段 3 · adapter 层（2 步）

| # | 任务 | 状态 |
|---|---|---|
| **3** | 新建 `file_asset_service/app/quota_parser/service.py` —— DB ↔ MinIO ↔ quota_parser 桥 | 🔵 今天 |
| **4** | 新建 `file_asset_service/app/quota_parser/__init__.py` | 🔵 今天 |

### 阶段 4 · web API 端点（2 步）

| # | 任务 | 状态 |
|---|---|---|
| **5** | `quota_api.py` 新增 7 个端点（`POST /parse` / `GET /candidate.xlsx` / `POST /reviewed` / `GET /final.xlsx` / `GET /manifest` / `GET /qa-report` / 扩展 `GET /archives/{id}` 返回 `parse` 子对象）；含 `QUOTA_PARSE_MOCK=1` mock 分支 | 🔵 今天 |
| **6** | `main.py` 注册 `quota_parser` router；启动 server 验证 `/healthz` + `/api/data-lake/quota/capabilities` | 🔵 今天 |

### 阶段 5 · mock 模式（1 步，含在 #5 里）

| # | 任务 | 状态 |
|---|---|---|
| **7** | `file_asset_service/app/mock_parse_runner.py` —— `run_mock_pipeline(archive_id)` 跑 asyncio.create_task 后台；sleep 5-10s → 填 parse_* 字段 → 写假 candidate.xlsx 到 MinIO；新 session 写库（避免 DetachedInstanceError） | 🔵 今天 |

### 阶段 6 · 真实 Worker 轮询（2 步）

| # | 任务 | 状态 |
|---|---|---|
| **8** | 替换 `quota_parser/pipeline.serve_worker()` 占位 → 真实轮询 FileProcessing：`status='pending' AND processor='quota_parser_v2'` → lease → 下载 PDF → `run_quota_pipeline` → 上传 MinIO → 写库 | ⏳ 后续批次 |
| **9** | `scripts/run_quota_worker.sh`（仿 `serve.py` onlogon）独立进程启动脚本 | ⏳ 后续批次 |

### 阶段 7 · 前端（4 步）

| # | 任务 | 状态 |
|---|---|---|
| **10** | 新建 `file_asset_service/app/ui/quota-parse.js` —— 解析区块渲染 + 解析任务 tab | ⏳ 后续批次 |
| **11** | 新建 `file_asset_service/app/ui/quota-parse-api.js` —— HTTP 客户端 + mock 模式开关 | ⏳ 后续批次 |
| **12** | 新建 `file_asset_service/app/ui/quota-parse-mock.js` —— 浏览器内 mock fixture | ⏳ 后续批次 |
| **13** | 改 `quota-ui.js` 加 parse tab + 档案详情解析区块；`index.html` 注册 3 个新 script；`quota-api.js` 暴露 `archive.parse` 格式化器 | ⏳ 后续批次 |

### 阶段 8 · 测试 + 文档（5 步）

| # | 任务 | 状态 |
|---|---|---|
| **14** | quota_parser 包单元测试（cleanup_workspace / cleanup_expired_jobs） | ⏳ |
| **15** | quota_api.py 7 个端点 happy path + error case 单测 | ⏳ |
| **16** | E2E：mock 模式下端到端跑通一份四川 PDF（curl 7 端点 + 看 Archive.parse_status 推进） | ⏳ |
| **17** | CLAUDE.md 更新（worker 启动命令 + FileProcessing 表 quota 域用法） | ⏳ |
| **18** | README / web-frontend SPEC / parser SPEC 同步（"v0.3 草案" → "v0.3 web 端在轨"） | ⏳ |

---

## 4. 今天的前 4 步（详细化）

### Step 1 · Archive 加 parse_* 13 列

**改 `file_asset_service/app/models.py`**（`Archive` 类内部追加）：

```python
# --- quota_parser integration (v0.3, 2026-07-28) ---
parse_status: Mapped[str | None] = mapped_column(String(32), index=True)
parse_profile: Mapped[str | None] = mapped_column(String(32))
parse_task_id: Mapped[str | None] = mapped_column(String(64))
parse_phase: Mapped[str | None] = mapped_column(String(16))
parse_parser_version: Mapped[str | None] = mapped_column(String(32))
parse_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
parse_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
parse_metrics: Mapped[dict | None] = mapped_column(archive_json_type())
parse_warnings: Mapped[list | None] = mapped_column(archive_json_type())
parse_error_code: Mapped[str | None] = mapped_column(String(32))
parse_error_message: Mapped[str | None] = mapped_column(Text)
candidate_xlsx_key: Mapped[str | None] = mapped_column(String(512))
final_xlsx_key: Mapped[str | None] = mapped_column(String(512))
```

**注意**：
- 字段顺序：**不**加 CheckConstraint；与 §2.1 方案 A 一致（status 独立字段，不污染 ck_archive_status）
- `init_db()` 自动建表（SQLAlchemy declarative），不需要手写 ALTER TABLE
- 既有数据：所有新列默认 NULL，无迁移成本

**验证**：
```bash
PY=/d/miniconda3/envs/file-asset/python.exe
$PY -c "from app.models import Archive; print([c.name for c in Archive.__table__.columns if c.name.startswith('parse_') or c.name.endswith('_xlsx_key')])"
# → ['parse_status', 'parse_profile', 'parse_task_id', 'parse_phase', 'parse_parser_version',
#     'parse_started_at', 'parse_finished_at', 'parse_metrics', 'parse_warnings',
#     'parse_error_code', 'parse_error_message', 'candidate_xlsx_key', 'final_xlsx_key']
```

### Step 2 · quota_parser adapter 子包

**新建 `file_asset_service/app/quota_parser/__init__.py`**：

```python
"""quota_parser web-side adapter (DB ↔ MinIO ↔ quota_parser 包)

薄桥接层 —— 不复制 quota_parser 包的业务逻辑，只做：
  1. 解析阶段 A/B 状态机推进（写 Archive.parse_* 13 列）
  2. PDF 从 MinIO 临时下载到本地 work_root，调 run_quota_pipeline
  3. 产物（candidate.xlsx / final.xlsx / manifest.json / qa_report.*）上传 MinIO
  4. 异常 → parse_error_code 映射（parser/SPEC.md §11）
"""
from .service import (
    trigger_parse,
    upload_reviewed,
    build_parse_section,
    PARSE_BUCKET,
    PROFILES,
)

__all__ = [
    "trigger_parse",
    "upload_reviewed",
    "build_parse_section",
    "PARSE_BUCKET",
    "PROFILES",
]
```

**新建 `file_asset_service/app/quota_parser/service.py`**（核心）：

```python
"""DB ↔ MinIO ↔ quota_parser 桥

- 阶段 A: trigger_parse(archive_id, profile) → 写 parse_status='parsing'
          → asyncio.create_task → run_mock_pipeline 或 run_quota_pipeline
- 阶段 B: upload_reviewed(archive_id, xlsx_path) → 写 final_xlsx_key
- 序列化: build_parse_section(archive) → ArchiveDetail.parse 子对象
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from app.models import Archive
from app.storage import ObjectStore

logger = logging.getLogger(__name__)

PARSE_BUCKET = "quota-extract"  # §2.3 待运维拍板；adapter 层单点常量
PROFILES = ("sichuan", "chongqing")

# Manifest → Archive.parse_* 字段映射（与 web-frontend SPEC §6.1 对齐）
MANIFEST_TO_ARCHIVE = [
    "profile", "task_id", "phase", "status",
    "parser_version", "started_at", "finished_at",
    "metrics", "warnings",
]


def trigger_parse(archive: Archive, *, profile: str | None = None) -> Archive:
    """触发阶段 A：写 parse_status='parsing' + parse_task_id。

    Args:
        archive: 已加载的 Archive 实例（locked by caller for update）
        profile: 'sichuan' / 'chongqing' / None（None = 用档案已有的 profile）

    Returns:
        更新后的 Archive 实例（caller 负责 commit）

    Raises:
        ValueError: archive 不在 quota 域；profile 不合法
    """
    if archive.domain_type != "quota":
        raise ValueError(f"archive {archive.archive_id} 不是 quota 域（{archive.domain_type}）")
    if profile is not None and profile not in PROFILES:
        raise ValueError(f"profile {profile!r} 不在注册表 {PROFILES}")
    # 状态机校验：registered → parsing；parsed/qa_passed/usable 也允许（重新解析）
    if archive.parse_status in ("parsing",):
        raise ValueError(f"parse_status 已在 parsing，不可重入")
    archive.parse_status = "parsing"
    archive.parse_profile = profile or archive.parse_profile
    archive.parse_task_id = f"qp_{_ts()}_{archive.archive_id[:8]}"
    archive.parse_started_at = _now()
    archive.parse_finished_at = None
    archive.parse_error_code = None
    archive.parse_error_message = None
    return archive


def upload_reviewed(archive: Archive, reviewed_xlsx_path: Path) -> Archive:
    """触发阶段 B：upload reviewed.xlsx → 写 final_xlsx_key。

    不在本步调 finalize_reviewed_xlsx（mock 模式下 mock runner 调；真模式下 worker 调）。
    """
    # ...


def build_parse_section(archive: Archive) -> dict | None:
    """把 Archive.parse_* 字段打包成 web-frontend SPEC §6.1 的 `parse` 子对象。"""
    if archive.parse_status is None:
        return None
    return {
        "profile": archive.parse_profile,
        "task_id": archive.parse_task_id,
        "phase": archive.parse_phase,
        "status": archive.parse_status,
        "started_at": archive.parse_started_at.isoformat() if archive.parse_started_at else None,
        "finished_at": archive.parse_finished_at.isoformat() if archive.parse_finished_at else None,
        "parser_version": archive.parse_parser_version,
        "metrics": archive.parse_metrics,
        "warnings": archive.parse_warnings,
        "error_code": archive.parse_error_code,
        "error_message": archive.parse_error_message,
        "candidate_xlsx_key": archive.candidate_xlsx_key,
        "final_xlsx_key": archive.final_xlsx_key,
        "manifest": _reconstruct_manifest(archive),
    }


def _reconstruct_manifest(archive: Archive) -> dict | None:
    """从 Archive.parse_* 字段反推 Manifest（前端零翻译）。"""
    if archive.parse_status is None:
        return None
    return {
        "$schema": "quota-parser-result/v1",
        "task_id": archive.parse_task_id,
        "phase": archive.parse_phase,
        "status": archive.parse_status,
        "parser_version": archive.parse_parser_version,
        "profile": archive.parse_profile,
        "province": archive.parse_profile,
        "ocr_api_url": "http://172.16.20.23:8000",
        "source_pdf_sha256": None,  # archive 暂未存原 PDF sha256，留 None
        "candidate_xlsx_sha256": None,
        "artifacts": _reconstruct_artifacts(archive),
        "metrics": archive.parse_metrics or {},
        "warnings": archive.parse_warnings or [],
    }


def _reconstruct_artifacts(archive: Archive) -> list[dict]:
    out: list[dict] = []
    if archive.candidate_xlsx_key:
        out.append({"kind": "candidate_xlsx", "key": archive.candidate_xlsx_key})
    if archive.final_xlsx_key:
        out.append({"kind": "final_xlsx", "key": archive.final_xlsx_key})
    return out
```

**新建 `file_asset_service/app/mock_parse_runner.py`**（web-frontend SPEC §8 硬约束 1-4）：

```python
"""Mock 模式：当 QUOTA_PARSE_MOCK=1 时替代真 worker

- 端点必须 async def，asyncio.create_task 启动后台任务，立即返回
- 后台任务独立 SessionLocal（避免 DetachedInstanceError）
- task 引用存到模块级 set，防止被 GC
- 5-10s sleep 后填 Archive.parse_* 字段 + 写假 candidate.xlsx 到 MinIO
"""
from __future__ import annotations
import asyncio
import logging
from pathlib import Path

from app.database import SessionLocal
from app.models import Archive
from app.quota_parser.service import PARSE_BUCKET
from app.storage import get_object_store

logger = logging.getLogger(__name__)

# task 引用集合（防止被 GC；task done 时回调 discard）
_TASKS: set[asyncio.Task] = set()


async def run_mock_pipeline(archive_id: str, *, candidate_seconds: float = 5.0) -> None:
    """mock 模式下的"阶段 A"模拟。

    1. 拿新 session
    2. 加载 Archive
    3. sleep candidate_seconds（模拟 OCR 耗时）
    4. 写 Archive.parse_status='parsed' + 假 metrics/warnings
    5. 写假 candidate.xlsx 到 MinIO（最小可用的 xlsx 字节）
    """
    loop = asyncio.get_running_loop()
    try:
        await asyncio.sleep(candidate_seconds)
    except asyncio.CancelledError:
        logger.warning("mock pipeline cancelled archive_id=%s", archive_id)
        return

    with SessionLocal() as session:
        archive = session.get(Archive, archive_id)
        if archive is None:
            logger.error("mock pipeline: archive %s 不存在", archive_id)
            return
        archive.parse_status = "parsed"
        archive.parse_phase = "stage_a"
        archive.parse_parser_version = "0.2.0"  # 与 quota_parser 包的 PARSER_VERSION 对齐
        archive.parse_finished_at = _now()
        archive.parse_metrics = {
            "pages": 100,
            "ocr_seconds": int(candidate_seconds),
            "candidate_rows": 1000,
        }
        archive.parse_warnings = ["mock mode — metrics are fake"]
        archive.candidate_xlsx_key = f"quota/{archive_id}/candidate.xlsx"
        # 写假 xlsx 到 MinIO
        _upload_fake_xlsx(archive.candidate_xlsx_key, sheet_name="定额条目", rows=10)
        session.commit()
    logger.info("mock pipeline done archive_id=%s status=parsed", archive_id)


def _upload_fake_xlsx(key: str, *, sheet_name: str, rows: int) -> None:
    """写一个最小可用的 xlsx 到 MinIO（用 openpyxl 构造 10 行假数据）。"""
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    for i in range(rows):
        ws.append([f"mock-row-{i}"] * 10)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    store = get_object_store()
    store.put_object(PARSE_BUCKET, key, buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def schedule_mock(archive_id: str) -> asyncio.Task:
    """封装 asyncio.create_task，把 task 引用放进 _TASKS 集合。"""
    task = asyncio.create_task(run_mock_pipeline(archive_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _now():
    from datetime import datetime, UTC
    return datetime.now(UTC)
```

### Step 3 · quota_api.py 新增 7 个端点

**追加到 `file_asset_service/app/quota_api.py`**（与现有 router 共用 prefix `/api/data-lake/quota`）：

```python
# ── P0-4B · quota_parser integration (v0.3, 2026-07-28) ─────────────────

# env: QUOTA_PARSE_MOCK=1 → 用 mock runner；否则调用真 worker（未来）
import os as _os
_QUOTA_PARSE_MOCK = _os.environ.get("QUOTA_PARSE_MOCK", "0") == "1"

if _QUOTA_PARSE_MOCK:
    from app.mock_parse_runner import schedule_mock as _schedule_parse


@router.post("/archives/{archive_id}/parse")
async def trigger_parse(archive_id: str, body: dict | None = None, session: Session = Depends(get_db_session)):
    """触发阶段 A。QUOTA_PARSE_MOCK=1 时调 mock；否则立即返回 501（真 worker 待上线）。"""
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, "ARCHIVE_NOT_FOUND")
    if archive.domain_type != "quota":
        raise HTTPException(400, "NOT_A_QUOTA_ARCHIVE")
    profile = (body or {}).get("profile")
    try:
        from app.quota_parser.service import trigger_parse as _trigger
        archive = _trigger(archive, profile=profile)
    except ValueError as e:
        raise HTTPException(422, str(e))
    session.commit()
    session.refresh(archive)
    if _QUOTA_PARSE_MOCK:
        _schedule_parse(archive_id)
    return _build_archive_detail(archive, session)


@router.get("/archives/{archive_id}/candidate.xlsx")
def get_candidate_xlsx(archive_id: str, session: Session = Depends(get_db_session)):
    """下载 candidate.xlsx。"""
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, "ARCHIVE_NOT_FOUND")
    if not archive.candidate_xlsx_key:
        raise HTTPException(404, "CANDIDATE_NOT_READY")
    from app.storage import get_object_store
    from app.quota_parser.service import PARSE_BUCKET
    store = get_object_store()
    data = store.get_object(PARSE_BUCKET, archive.candidate_xlsx_key)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{archive.title}-candidate.xlsx"'},
    )


@router.post("/archives/{archive_id}/reviewed")
async def upload_reviewed(
    archive_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_db_session),
):
    """上传 reviewed.xlsx → 阶段 B → 写 final_xlsx_key。"""
    # 实现见 Step 3 详细代码（含 openpyxl 结构校验 + mock runner 路径分支）
    ...


@router.get("/archives/{archive_id}/final.xlsx")
def get_final_xlsx(...):
    """下载 final.xlsx。"""
    ...


@router.get("/archives/{archive_id}/manifest")
def get_manifest(...):
    """返回 Manifest JSON（与 quota-parser-result/v1 schema 对齐）。"""
    ...


@router.get("/archives/{archive_id}/qa-report")
def get_qa_report(...):
    """返回 QA 报告（JSON / ?format=md 切换）。"""
    ...


# 扩展 GET /archives/{archive_id}：在返回里追加 `parse` 子对象
def _build_archive_detail(archive: Archive, session: Session) -> dict:
    """现有 archive detail 构建函数追加 `parse` 子对象。"""
    from app.quota_parser.service import build_parse_section
    detail = _existing_archive_detail(archive, session)  # 现有逻辑
    detail["parse"] = build_parse_section(archive)
    return detail
```

> **完整代码在 Step 3 实施时落到 quota_api.py**（避免本文档膨胀）。本 plan 给接口签名 + 数据契约。

### Step 4 · main.py 注册 router + 启动验证

**改 `file_asset_service/app/main.py`**（L179 后追加一行）：

```python
from app.quota_parser_api import router as quota_parser_router  # 见下方

app.include_router(quota_router)
app.include_router(quota_parser_router)  # 新增
```

**注意**：现有 `quota_api.py` 的 router prefix 已经是 `/api/data-lake/quota`；新端点也挂同一 prefix，避免拆两个 router。可选择：

- **方案 1（推荐）**：把 7 个新端点直接追加到 `quota_api.py`（不拆 router）。改动量最小，import 不变。
- **方案 2**：拆 `quota_parser_api.py`，prefix 同 `/api/data-lake/quota`，在 main.py 多 `include_router` 一次。

**今天按方案 1 实施**：Step 3 在 `quota_api.py` 末尾追加 7 个端点 + `trigger_parse` / `_build_archive_detail` 等 helper。

**启动验证**：

```bash
PY=/d/miniconda3/envs/file-asset/python.exe
export QUOTA_PARSE_MOCK=1   # 默认开 mock，curl 可直接 demo
cd file_asset_service
$PY -m app.main serve  # 或 python serve.py

# 另一窗口
curl http://localhost:8000/healthz
curl http://localhost:8000/api/data-lake/quota/capabilities
curl http://localhost:8000/api/data-lake/quota/archives/<archive_id>
# → 应返回 ArchiveDetail，含 parse=null（未触发解析）

curl -X POST http://localhost:8000/api/data-lake/quota/archives/<archive_id>/parse \
  -H "Content-Type: application/json" -d '{"profile":"sichuan"}'
# → 200 + ArchiveDetail，parse.status="parsing"；5-10s 后 status="parsed"

curl http://localhost:8000/api/data-lake/quota/archives/<archive_id>/candidate.xlsx \
  -o /tmp/candidate.xlsx
# → mock 写入的假 xlsx（10 行 10 列）
```

---

## 5. 前置先决条件（**今天 step 1 前必须做**）

| # | 任务 | 阻塞 | 谁做 |
|---|---|---|---|
| P1 | `quota/parser/` 加 `pyproject.toml`（最小包声明，依赖 quota_parser 实际需要的库：openpyxl / requests / beautifulsoup4 / lxml） | step 2/3 端点要 `import quota_parser` | CC |
| P2 | file-asset 环境装 `quota_parser` editable：`pip install -e quota/parser/` | 同上 | CC |
| P3 | `quota_parser.config.PROFILE` 与 `external/` 复用层在 file-asset 环境也能 import（路径依赖 `D:/工程造价学习/...`） | mock 跑起来时避免 FileNotFoundError | CC + 验证 |

**P3 验证脚本**：

```bash
PY=/d/miniconda3/envs/file-asset/python.exe
cd /d/工程造价学习/定额解析   # 或 file-asset 的 cwd
$PY -c "from quota_parser import run_quota_pipeline, finalize_reviewed_xlsx; print('ok')"
```

注意：`quota_parser` 包的 `external/` 复用层依赖 `D:/工程造价学习/...` 绝对路径。在 file-asset 工作目录里跑时需要先确认 cwd。

---

## 6. 验收（今天 step 1-4 完成后的最小标准）

- [ ] `models.py` Archive 加了 13 列，`init_db()` 跑后 PG `archive` 表有新列
- [ ] `quota_parser` 包通过 `pyproject.toml` 在 file-asset 环境可 `import`
- [ ] `file_asset_service/app/quota_parser/` 子包就位（`__init__.py` + `service.py`）
- [ ] `mock_parse_runner.py` 实现完成（async def + 新 session + 假 xlsx 上传）
- [ ] `quota_api.py` 加 7 个端点 + `trigger_parse` helper；`GET /archives/{id}` 返回 `parse` 子对象
- [ ] `main.py` 启动后 `/healthz` 通；`QUOTA_PARSE_MOCK=1` 下 POST /parse → 5-10s 后状态变 `parsed`
- [ ] curl 7 个端点全部 200 / 预期错误码
- [ ] git commit 提交今天所有改动

---

## 7. 留作后续批次（不在今天 4 步内）

| 批次 | 内容 |
|---|---|
| **批次 2 · 真 Worker** | step 8/9 —— `serve_worker()` 真实轮询 FileProcessing + 启动脚本 |
| **批次 3 · 前端** | step 10-13 —— `quota-parse.js` / `quota-parse-api.js` / `quota-parse-mock.js` + `quota-ui.js` 改 + `index.html` 改 |
| **批次 4 · 测试 + 文档** | step 14-18 —— quota_parser 包单测 + quota_api.py 端点单测 + E2E（curl 7 端点 + 看 Archive.parse_status 推进）+ CLAUDE.md / README / SPEC 同步 |

---

## 8. 开放问题（待用户/运维确认）

| # | 问题 | 推荐方案 | 阻塞哪步 |
|---|---|---|---|
| 1 | `Archive.status` vs `parse_status` 命名冲突（cost_info 8 状态 vs quota 7 状态） | 新增独立 `parse_status` 字段（§2.1 方案 A） | step 1 |
| 2 | MinIO bucket 名 `quota-extract` 是否与运维约定冲突 | 单点常量 `PARSE_BUCKET`，1 行可改；先用 `quota-extract` | step 2/3 |
| 3 | `parse_task_id` 是否要唯一约束 | 不加（1 archive 1 run） | 无 |
| 4 | QA 报告保留 `json` 还是只 `md` | 两者都保留，`?format=md` 切换 | step 3 |
| 5 | archive 原 PDF sha256 是否要存到 Archive 表 | 暂不存（FileAsset.sha256 已有，跨表 join） | 无 |
| 6 | web-frontend SPEC §6.1 中 `archive.status == parsing` 的表述是否要改为 `archive.parse_status == "parsing"` | 改（与方案 A 对齐） | step 4 后单独改 SPEC |
| 7 | 前端解析区块何时做 | 批次 3（不在今天 4 步内） | 无 |

---

**锁版本**：v1（2026-07-28 讨论锁定）。

后续破坏性变更（拆 adapter 子包到独立子项目、改 `Archive.parse_status` 命名、加 DB 唯一约束等）必须走 v2。