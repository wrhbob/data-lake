"""E2E 验证：mock 模式下 quota_api.py 7 个解析端点全链路

环境：file-asset env (Python 3.11) + SQLite in-memory + FakeObjectStore + QUOTA_PARSE_MOCK=1
跑法：
  PY=/d/miniconda3/envs/file-asset/python.exe
  $PY -m pytest quota/parser/tests/test_e2e_mock_pipeline.py -x -v
  # 或直接：
  PYTHONPATH=. QUOTA_PARSE_MOCK=1 $PY quota/parser/tests/test_e2e_mock_pipeline.py

覆盖：
  1. POST /parse            触发解析（异步后台 5s 推到 parsed）
  2. GET  /candidate.xlsx   下载假 candidate
  3. POST /reviewed         上传 reviewed（异步后台 2s 推到 qa_passed）
  4. GET  /final.xlsx       下载假 final
  5. GET  /manifest         返回 Manifest JSON
  6. GET  /qa-report        返回 QA 报告（json + md）
  7. POST /parse/delete     清 parse_* 字段
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from io import BytesIO
from pathlib import Path

# 把 cwd 切到 file_asset_service 父目录（保证 app.* import 路径）
ROOT = Path(__file__).resolve().parent.parent.parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# mock 模式强制开
os.environ["QUOTA_PARSE_MOCK"] = "1"
# 跳过 init_db 真实跑 PG
os.environ["FILE_ASSET_SCHEMA_READY"] = "1"

# === 准备 SQLite in-memory + FakeObjectStore ===

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, Archive, QuotaArchiveProfile, QuotaPublicationSet, DataSource
from app.storage import FakeObjectStore
import app.storage as _storage
import app.database as _db

# 1. 建 engine + 表（StaticPool 让所有请求共用单 connection，跨线程可见 in-memory 数据）
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
_session_factory = sessionmaker(bind=_engine, future=True, expire_on_commit=False)

# 2. 覆盖 get_session_factory / get_db_session
_db._session_factory = _session_factory  # type: ignore[attr-defined]


def _override_get_db_session():
    with _session_factory() as session:
        yield session


# 3. 覆盖 get_object_store → FakeObjectStore
_fake_store = FakeObjectStore()
_fake_store.ensure_buckets()


def _override_get_object_store() -> FakeObjectStore:
    return _fake_store


_storage.get_object_store = _override_get_object_store  # type: ignore[assignment]

# 4. 注入 FastAPI app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.quota_api import router
from app.database import get_db_session

app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_db_session] = _override_get_db_session


# 5. 准备测试数据：1 个 quota 域的 data_source + 1 个 publication_set + 1 个 archive
# 注：data_source.source_scope='platform_public' 时 tenant_code 必须为 None
#     （ck_data_source_scope_tenant CheckConstraint）
with _session_factory() as session:
    ds = DataSource(
        source_id="ds_test_quota",
        source_scope="platform_public",
        tenant_code=None,
        asset_tenant_code="platform_public",
        managed_by="platform",
        source_type="manual_upload",
        connector_type="ui",
        name="test quota source",
        data_domain="quota",
    )
    session.add(ds)

    pubset = QuotaPublicationSet(
        publication_set_id="pst_test_quota",
        biz_key="sc:2025:test",
        publication_family_code="sc-2025",
        title="四川 2025 定额测试",
        material_type="quota_base",  # 见 quota_taxonomy.MATERIAL_TYPES
        quota_system_type="construction_regional",
        jurisdiction_level="province",
        jurisdiction_code="510000",
        issuer_name="四川省住建厅",
        edition_label="2025",
        edition_year=2025,
        tenant_code="platform_public",
    )
    session.add(pubset)

    archive = Archive(
        archive_id="arc_test_quota",
        domain_type="quota",
        channel_type="manual_upload",
        collection_method="manual_denovo",
        price_kind="guidance",
        business_key="sc:2025:test:arc",
        title="四川2025 测试定额",
        region_code="510000",
        source_id="ds_test_quota",
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
    )
    session.add(archive)

    profile = QuotaArchiveProfile(
        archive_id="arc_test_quota",
        publication_set_id="pst_test_quota",
        document_role="main_volume",  # DOCUMENT_ROLES 枚举值
    )
    session.add(profile)

    session.commit()


ARCHIVE_ID = "arc_test_quota"


# === 测试 ===

client = TestClient(app)


def wait_until(predicate, timeout=10.0, interval=0.2):
    """轮询直到 predicate 为真或超时（用于 mock 异步后台任务）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_01_capabilities():
    """capabilities 端点返回新解析功能标记。"""
    r = client.get("/api/data-lake/quota/capabilities")
    assert r.status_code == 200, r.text
    body = r.json()
    print("capabilities features:", body.get("features", {}))
    # 现阶段 features 还没加 parse 标记，先不强校验


def test_02_get_archive_detail_initial():
    """初始 GET /archives/{id}：parse=null（未触发解析）。"""
    r = client.get(f"/api/data-lake/quota/archives/{ARCHIVE_ID}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parse"] is None, f"expected parse=None, got {body['parse']}"
    assert body["archive"]["archive_id"] == ARCHIVE_ID


def test_03_post_parse_triggers_mock():
    """POST /parse → 200 + parse.status='parsing'；随后 await mock runner 推进。

    注：TestClient 同步调用关闭 anyio portal 后，后台 asyncio.create_task 会被取消。
    这里用 asyncio.run 直接 await 跑完 mock runner（不走 HTTP），验证推进逻辑。
    """
    r = client.post(f"/api/data-lake/quota/archives/{ARCHIVE_ID}/parse", json={"profile": "sichuan"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parse"]["status"] == "parsing"
    assert body["parse"]["profile"] == "sichuan"
    assert body["parse"]["task_id"].startswith("qp_")
    print(f"triggered: status=parsing, task_id={body['parse']['task_id']}")

    # 直接 await mock runner 推进（短 sleep 加速测试）
    from app.mock_parse_runner import run_mock_pipeline_a
    asyncio.run(run_mock_pipeline_a(ARCHIVE_ID, candidate_seconds=0.1))
    status = _check_parse_status(ARCHIVE_ID)
    assert status == "parsed", f"expected 'parsed', got {status!r}"
    print(f"after mock_a: status={status}")


def test_04_get_candidate_xlsx():
    """parsed 后 GET /candidate.xlsx 返回 200 + 字节流。"""
    r = client.get(f"/api/data-lake/quota/archives/{ARCHIVE_ID}/candidate.xlsx")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert len(r.content) > 100, f"candidate.xlsx 太小: {len(r.content)} bytes"
    # 验证是 xlsx 字节（PK\x03\x04 ZIP 头）
    assert r.content[:2] == b"PK", "candidate.xlsx 不是 ZIP 格式"
    print(f"candidate.xlsx: {len(r.content)} bytes")


def test_05_post_reviewed_triggers_mock_b():
    """POST /reviewed → mock 模式直接接受，2s 后推到 qa_passed。"""
    # 构造一个最小假 xlsx（10 行 10 列 + sheet 名「定额条目」）
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "定额条目"
    for i in range(10):
        ws.append([f"reviewed-{i}-{j}" for j in range(10)])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    r = client.post(
        f"/api/data-lake/quota/archives/{ARCHIVE_ID}/reviewed",
        files={"file": ("reviewed.xlsx", buf.getvalue(),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("scheduled") is True, f"expected scheduled=True, got {body}"

    # 直接 await mock_b 跑完（同 test_03：TestClient portal 关闭会取消后台 task）
    from app.mock_parse_runner import run_mock_pipeline_b
    asyncio.run(run_mock_pipeline_b(ARCHIVE_ID, reviewed_bytes=buf.getvalue(), candidate_seconds=0.1))
    status = _check_parse_status(ARCHIVE_ID)
    assert status == "qa_passed", f"expected 'qa_passed', got {status!r}"
    print(f"after mock_b: status={status}")


def test_06_get_final_xlsx():
    """qa_passed 后 GET /final.xlsx 返回 200 + 字节流。"""
    r = client.get(f"/api/data-lake/quota/archives/{ARCHIVE_ID}/final.xlsx")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert len(r.content) > 100
    assert r.content[:2] == b"PK"
    print(f"final.xlsx: {len(r.content)} bytes")


def test_07_get_manifest():
    """GET /manifest 返回符合 quota-parser-result/v1 schema 的 dict。"""
    r = client.get(f"/api/data-lake/quota/archives/{ARCHIVE_ID}/manifest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["$schema"] == "quota-parser-result/v1"
    assert body["profile"] == "sichuan"
    assert body["phase"] == "stage_b"
    assert body["status"] == "qa_passed"
    print(f"manifest profile={body['profile']} phase={body['phase']}")


def test_08_get_qa_report_json():
    """GET /qa-report（默认 json）返回 quota-parser-qa/v1 dict。"""
    r = client.get(f"/api/data-lake/quota/archives/{ARCHIVE_ID}/qa-report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["$schema"] == "quota-parser-qa/v1"
    assert body["summary"] in ("ok", "warning", "failed")
    print(f"qa-report json: summary={body['summary']}, checks={len(body.get('checks', []))}")


def test_09_get_qa_report_md():
    """GET /qa-report?format=md 返回 markdown 字符串。"""
    r = client.get(f"/api/data-lake/quota/archives/{ARCHIVE_ID}/qa-report?format=md")
    assert r.status_code == 200, r.text
    assert "markdown" in r.headers["content-type"]
    assert "QA Report" in r.text
    print(f"qa-report md: {len(r.text)} chars")


def test_10_delete_parse_result():
    """POST /parse/delete 清空 parse_* 字段；后续 GET candidate 应 404。"""
    r = client.post(f"/api/data-lake/quota/archives/{ARCHIVE_ID}/parse/delete")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True

    # GET /candidate.xlsx 应返回 404 CANDIDATE_NOT_READY
    r2 = client.get(f"/api/data-lake/quota/archives/{ARCHIVE_ID}/candidate.xlsx")
    assert r2.status_code == 404, f"expected 404 after delete, got {r2.status_code}: {r2.text}"
    print("delete: parse_status 清空 ✓ candidate.xlsx 404 ✓")


def test_11_re_parse_after_delete():
    """删除后重新解析能正常工作（共用端点决策）。"""
    r = client.post(f"/api/data-lake/quota/archives/{ARCHIVE_ID}/parse", json={"profile": "chongqing"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parse"]["status"] == "parsing"
    assert body["parse"]["profile"] == "chongqing"
    print(f"re-parse: profile=chongqing ✓")


# === helpers ===

def _check_parse_status(archive_id: str) -> str | None:
    with _session_factory() as session:
        a = session.get(Archive, archive_id)
        return a.parse_status if a else None


# === 入口（pytest / 脚本两用） ===

def run_all() -> None:
    tests = [
        test_01_capabilities,
        test_02_get_archive_detail_initial,
        test_03_post_parse_triggers_mock,
        test_04_get_candidate_xlsx,
        test_05_post_reviewed_triggers_mock_b,
        test_06_get_final_xlsx,
        test_07_get_manifest,
        test_08_get_qa_report_json,
        test_09_get_qa_report_md,
        test_10_delete_parse_result,
        test_11_re_parse_after_delete,
    ]
    for t in tests:
        print(f"\n=== {t.__name__} ===")
        t()
    print(f"\n✓ ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    run_all()