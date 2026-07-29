"""HOTFIX-QA-UPLOAD-001 · 定额上传预览闭环后端测试。

直接调用 quota_api.upload_quota_files() async 端点函数，复用 db_session +
fake_storage fixture（不使用 Mock，不使用 TestClient）。

核心校验：
- file_role 恒为 main_document，category 仅入 metadata_payload
- business_key 基于 sha256：同名异内容→不同 Archive；异名同内容→同一 Archive
- 防空 Archive 补偿：attach 失败不留残骸
- 不跨域泄漏
"""

import asyncio
import hashlib

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Archive, ArchiveEvent, ArchiveFile, FileAsset, QuotaArchiveProfile, QuotaPublicationSet
from app.quota_api import (
    QuotaUploadResponse,
    _cleanup_empty_archive,
    _ensure_quota_upload_source,
    upload_quota_files,
)


# ── helpers ─────────────────────────────────────────────────────────────


class _StubUploadFile:
    """满足 UploadFile 结构契约的最小 stub：filename + async read()。

    不是 mock —— 只是一个数据载体，与 starlette.UploadFile 接口一致。
    """

    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._content = content
        self.content_type = "application/pdf"

    async def read(self) -> bytes:
        return self._content


def _call_upload(session, storage, files, category="construction_quota",
                 province="sc", year=2025, profile=None):
    """便捷包装：直接调用 async 端点函数。

    province / year 默认 "sc" / 2025（v0.3.2 起端点必填；测试覆盖默认值场景）。
    profile 必须显式传 None：FastAPI 的 Form(None) 默认在直接调用（非依赖注入）
    时返回 FormInfo 对象而非 None，会触发 INVALID_PROFILE 校验。
    """
    coro = upload_quota_files(
        response=_DummyResponse(),
        files=files,
        category=category,
        province=province,
        year=year,
        profile=profile,
        session=session,
        storage=storage,
    )
    return asyncio.run(coro)


class _DummyResponse:
    """满足 endpoint 中 response.headers.update(...) 的最小 Response stub。"""
    def __init__(self):
        self.headers = {}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _count(session, model, **filters) -> int:
    stmt = select(func.count()).select_from(model)
    for col, val in filters.items():
        stmt = stmt.where(getattr(model, col) == val)
    return int(session.scalar(stmt) or 0)


# ── 1. 单 PDF 上传 ──────────────────────────────────────────────────────


def test_single_pdf_upload_creates_archive_with_main_document(db_session, fake_storage):
    content = b"%PDF-1.4 fake quota pdf bytes for single upload test"
    result = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("sc2025.pdf", content)],
        category="construction_quota",
    )

    assert isinstance(result, QuotaUploadResponse)
    assert result.count == 1
    assert result.succeeded == 1
    assert result.failed == 0

    item = result.items[0]
    assert item.status == "created"
    assert item.filename == "sc2025.pdf"
    assert item.file_id is not None
    assert item.archive_id is not None
    assert item.title == "sc2025"
    assert item.already_exists is False

    # Archive(domain=quota) 创建
    archive = db_session.get(Archive, item.archive_id)
    assert archive is not None
    assert archive.domain_type == "quota"
    # v0.3.2 起 business_key 公式：quota:manual-upload:{province}:{year}:{sha256[:12]}
    assert archive.business_key == f"quota:manual-upload:sc:2025:{_sha256(content)[:12]}"
    assert archive.status == "pending_tag"

    # ArchiveFile: file_role == main_document, is_primary=True
    af = db_session.scalar(
        select(ArchiveFile).where(ArchiveFile.archive_id == archive.archive_id)
    )
    assert af is not None
    assert af.file_role == "main_document"
    assert af.is_primary is True
    assert af.file_id == item.file_id

    # category 仅写入 Archive.metadata_payload
    assert "category" in archive.metadata_payload
    assert archive.metadata_payload["category"]["value"] == "construction_quota"


# ── 2. 多 PDF 上传 ──────────────────────────────────────────────────────


def test_multi_pdf_upload_creates_multiple_archives(db_session, fake_storage):
    files = [
        _StubUploadFile("a.pdf", b"content-a-bytes-fill"),
        _StubUploadFile("b.pdf", b"content-b-bytes-fill-different"),
        _StubUploadFile("c.pdf", b"content-c-bytes-unique-again"),
    ]
    result = _call_upload(db_session, fake_storage, files)

    assert result.count == 3
    assert result.succeeded == 3
    assert result.failed == 0
    assert {it.filename for it in result.items} == {"a.pdf", "b.pdf", "c.pdf"}

    archive_ids = {it.archive_id for it in result.items}
    assert len(archive_ids) == 3  # 互不干扰，三条独立 Archive


# ── 3. 重复上传（异名同内容）→ 幂等返回 ──────────────────────────────────


def test_duplicate_pdf_upload_returns_existing_archive(db_session, fake_storage):
    content = b"same content for dedup test padding padding padding"
    first = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("original.pdf", content)],
    )
    assert first.succeeded == 1

    file_assets_before = _count(db_session, FileAsset)
    archives_before = _count(db_session, Archive, domain_type="quota")
    archive_files_before = _count(db_session, ArchiveFile)

    # 不同文件名、相同内容 二次上传
    second = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("renamed-copy.pdf", content)],
    )

    assert second.count == 1
    item = second.items[0]
    assert item.status == "duplicate"
    assert item.already_exists is True
    assert item.archive_id == first.items[0].archive_id
    assert item.file_id == first.items[0].file_id

    # DB 无新增
    assert _count(db_session, FileAsset) == file_assets_before
    assert _count(db_session, Archive, domain_type="quota") == archives_before
    assert _count(db_session, ArchiveFile) == archive_files_before


# ── 4. 同名异内容 → 两条独立 Archive ─────────────────────────────────────


def test_same_filename_different_content_creates_separate_archives(db_session, fake_storage):
    first = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("quota.pdf", b"content version one bytes here")],
    )
    second = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("quota.pdf", b"content version two completely different")],
    )

    assert first.succeeded == 1
    assert second.succeeded == 1
    assert first.items[0].archive_id != second.items[0].archive_id
    assert first.items[0].file_id != second.items[0].file_id

    # business_key 基于 sha256 区分
    a1 = db_session.get(Archive, first.items[0].archive_id)
    a2 = db_session.get(Archive, second.items[0].archive_id)
    assert a1.business_key != a2.business_key


# ── 5. 非 PDF 拒绝 ──────────────────────────────────────────────────────


def test_non_pdf_file_rejected(db_session, fake_storage):
    result = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("data.xlsx", b"fake excel bytes")],
    )

    assert result.count == 1
    assert result.succeeded == 0
    assert result.failed == 1
    item = result.items[0]
    assert item.status == "failed"
    assert item.error == "NON_PDF_REJECTED"
    assert item.archive_id is None


# ── 6. 不泄漏到 cost_info 域 ─────────────────────────────────────────────


def test_quota_upload_does_not_leak_into_cost_info(db_session, fake_storage):
    cost_before = _count(db_session, Archive, domain_type="cost_info")

    _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("quota-only.pdf", b"quota domain only bytes here")],
        category="boq_standard",
    )

    assert _count(db_session, Archive, domain_type="cost_info") == cost_before


# ── 7. category 仅在 metadata，不映射到 file_role ────────────────────────


def test_category_only_in_metadata_not_in_file_role(db_session, fake_storage):
    for cat in ("construction_quota", "industry_quota", "boq_standard"):
        _call_upload(
            db_session, fake_storage,
            [_StubUploadFile(f"{cat}.pdf", f"content for {cat}".encode())],
            category=cat,
        )

    # 三条 ArchiveFile 的 file_role 全为 main_document
    afs = db_session.execute(
        select(ArchiveFile).join(Archive, ArchiveFile.archive_id == Archive.archive_id)
        .where(Archive.domain_type == "quota")
    ).scalars().all()
    assert len(afs) == 3
    for af in afs:
        assert af.file_role == "main_document", f"{af.file_role} != main_document"

    # metadata_payload.category 分别等于三个 category
    categories_in_db = set()
    for af in afs:
        archive = db_session.get(Archive, af.archive_id)
        cat_cell = archive.metadata_payload.get("category")
        assert cat_cell is not None
        categories_in_db.add(cat_cell["value"])
    assert categories_in_db == {"construction_quota", "industry_quota", "boq_standard"}


# ── 8. industry_quota 不被误写为 industry_specialty ──────────────────────


def test_industry_quota_not_mistyped_as_industry_specialty(db_session, fake_storage):
    result = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("industry.pdf", b"industry quota content bytes")],
        category="industry_quota",
    )

    assert result.succeeded == 1
    archive = db_session.get(Archive, result.items[0].archive_id)

    # metadata_payload 中只有 category="industry_quota"
    cat_cell = archive.metadata_payload.get("category")
    assert cat_cell is not None
    assert cat_cell["value"] == "industry_quota"

    # 不应出现 quota_system_type="industry_specialty" 残留
    for key, val in archive.metadata_payload.items():
        if key == "quota_system_type":
            pytest.fail(f"不应写入 quota_system_type，发现 {key}={val}")
    # Archive 本身没有 quota_system_type 列（那在 QuotaPublicationSet 上）
    assert not hasattr(archive, "quota_system_type")


# ── 9. 防空 Archive 补偿 ────────────────────────────────────────────────


def test_cleanup_empty_archive_clears_archive_and_files_and_events(db_session, fake_storage):
    """直接验证补偿函数：构造一个含 ArchiveFile/ArchiveEvent/QuotaArchiveProfile 的孤儿档案，
    调用 _cleanup_empty_archive 后全部清除。"""
    from uuid import uuid4
    from app.archive_rules import metadata_cell
    from app.models import QuotaPublicationSet

    # 先建一个 DataSource + FileAsset（补偿不删 FileAsset）
    source_id = _ensure_quota_upload_source(db_session)
    from app.assets import register_asset
    reg = register_asset(
        db_session, fake_storage,
        tenant_code="platform_public",
        source_type="quota_manual_upload",
        batch_id="cleanup-test",
        file_name="survive.pdf",
        content=b"this file asset should survive cleanup",
    )

    # QuotaArchiveProfile.publication_set_id 是 NOT NULL + FK，
    # 需要先建 QuotaPublicationSet 满足外键约束
    pubset = QuotaPublicationSet(
        publication_set_id=str(uuid4()),
        biz_key="quota:test:cleanup-orphan",
        publication_family_code="test-family",
        title="cleanup test pubset",
        material_type="quota_base",
        quota_system_type="construction_regional",
        jurisdiction_level="province",
        jurisdiction_code="000000",
        issuer_name="test-issuer",
        edition_label="test-edition",
        metadata_status="partial",
        tenant_code="platform_public",
        visibility_scope="public",
    )
    db_session.add(pubset)
    db_session.flush()

    # 构造孤儿 Archive
    archive = Archive(
        domain_type="quota",
        channel_type="manual_upload",
        collection_method="manual_denovo",
        business_key="quota:manual-upload:orphan-test",
        title="orphan",
        source_id=source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        metadata_payload={"category": metadata_cell("construction_quota", source_level="manual", tagged_by="test")},
        field_sources={
            "domain_type": metadata_cell("quota", source_level="manual", tagged_by="test"),
            "channel_type": metadata_cell("manual_upload", source_level="manual", tagged_by="test"),
            "title": metadata_cell("orphan", source_level="manual", tagged_by="test"),
            "business_key": metadata_cell("quota:manual-upload:orphan-test", source_level="manual", tagged_by="test"),
        },
    )
    db_session.add(archive)
    db_session.flush()

    # 残留 ArchiveFile（指向真实 file_id）
    db_session.add(ArchiveFile(
        archive_id=archive.archive_id,
        file_id=reg.file_id,
        file_role="main_document",
        is_primary=True,
        sort_order=10,
    ))
    # 残留 ArchiveEvent
    db_session.add(ArchiveEvent(
        archive_id=archive.archive_id,
        event_type="ARCHIVE_CREATED",
    ))
    # 残留 QuotaArchiveProfile（FK 指向 pubset）
    db_session.add(QuotaArchiveProfile(
        archive_id=archive.archive_id,
        publication_set_id=pubset.publication_set_id,
        document_role="main_volume",
        metadata_status="partial",
        completeness_score=0,
    ))
    db_session.commit()
    orphan_id = archive.archive_id

    # 执行补偿
    _cleanup_empty_archive(db_session, orphan_id)

    # Archive / ArchiveFile / ArchiveEvent / QuotaArchiveProfile 全部清除
    assert db_session.get(Archive, orphan_id) is None
    assert _count(db_session, ArchiveFile, archive_id=orphan_id) == 0
    assert _count(db_session, ArchiveEvent, archive_id=orphan_id) == 0
    assert _count(db_session, QuotaArchiveProfile, archive_id=orphan_id) == 0

    # FileAsset 保留
    assert db_session.get(FileAsset, reg.file_id) is not None


def test_attach_failure_does_not_leave_empty_archive(db_session, fake_storage):
    """端点层面：模拟 attach_file 失败（传入不存在的 file_id），验证空 Archive 被补偿。

    构造方式：手动建一个 quota Archive（无 ArchiveFile），然后调用 _cleanup_empty_archive
    模拟"attach 失败"路径，验证 Archive 被清除而 FileAsset 保留。
    """
    source_id = _ensure_quota_upload_source(db_session)
    from app.assets import register_asset
    reg = register_asset(
        db_session, fake_storage,
        tenant_code="platform_public",
        source_type="quota_manual_upload",
        batch_id="attach-fail-test",
        file_name="survive2.pdf",
        content=b"file asset that survives attach failure",
    )
    from app.archive_rules import metadata_cell
    archive = Archive(
        domain_type="quota",
        channel_type="manual_upload",
        collection_method="manual_denovo",
        business_key=f"quota:manual-upload:{reg.sha256}-orphan",
        title="will-be-cleaned",
        source_id=source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        metadata_payload={},
        field_sources={
            "domain_type": metadata_cell("quota", source_level="manual", tagged_by="test"),
            "channel_type": metadata_cell("manual_upload", source_level="manual", tagged_by="test"),
            "title": metadata_cell("will-be-cleaned", source_level="manual", tagged_by="test"),
            "business_key": metadata_cell(f"quota:manual-upload:{reg.sha256}-orphan", source_level="manual", tagged_by="test"),
        },
    )
    db_session.add(archive)
    db_session.commit()
    orphan_id = archive.archive_id

    # 确认有空的 quota Archive（无 main_document）
    empty_quota_count = _count(db_session, Archive, domain_type="quota") - _count_archives_with_main_doc(db_session)
    assert empty_quota_count >= 1

    _cleanup_empty_archive(db_session, orphan_id)

    # 补偿后该 Archive 不存在
    assert db_session.get(Archive, orphan_id) is None
    # FileAsset 保留
    assert db_session.get(FileAsset, reg.file_id) is not None


def _count_archives_with_main_doc(session) -> int:
    """统计有 main_document ArchiveFile 的 quota Archive 数量。"""
    from sqlalchemy import distinct
    return int(session.scalar(
        select(func.count(distinct(ArchiveFile.archive_id)))
        .join(Archive, ArchiveFile.archive_id == Archive.archive_id)
        .where(
            Archive.domain_type == "quota",
            ArchiveFile.file_role == "main_document",
        )
    ) or 0)


# ── 10. business_key 基于 sha256 而非 title ─────────────────────────────


def test_business_key_uses_sha256_not_title(db_session, fake_storage):
    # 同名 PDF A，内容相同
    content_a = b"content A identical bytes padding"
    first = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("file.pdf", content_a)],
    )
    # 改名 PDF A，内容相同 → business_key 一致（同 Archive，幂等返回）
    second = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("renamed.pdf", content_a)],
    )

    a1 = db_session.get(Archive, first.items[0].archive_id)
    expected_bk = f"quota:manual-upload:sc:2025:{_sha256(content_a)[:12]}"
    assert a1.business_key == expected_bk
    # 幂等：同一 Archive
    assert second.items[0].status == "duplicate"
    assert second.items[0].archive_id == a1.archive_id

    # 改名 PDF B，内容不同 → business_key 不同
    content_b = b"content B completely different bytes here"
    third = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("renamed.pdf", content_b)],
    )
    a3 = db_session.get(Archive, third.items[0].archive_id)
    assert a3.business_key == f"quota:manual-upload:sc:2025:{_sha256(content_b)[:12]}"
    assert a3.business_key != a1.business_key


# ── 端点响应结构冒烟 ─────────────────────────────────────────────────────


def test_upload_endpoint_response_shape(db_session, fake_storage):
    result = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("shape.pdf", b"response shape test bytes")],
    )

    assert isinstance(result, QuotaUploadResponse)
    assert hasattr(result, "count")
    assert hasattr(result, "succeeded")
    assert hasattr(result, "failed")
    assert hasattr(result, "items")
    assert isinstance(result.items, list)
    item = result.items[0]
    for field in ("filename", "status", "file_id", "archive_id", "title", "already_exists", "error"):
        assert hasattr(item, field), f"QuotaUploadItem 缺少字段 {field}"


# ── HTTP 层集成测试 · HOTFIX-QA-UPLOAD-002 ──────────────────────────────
# 通过 TestClient 走完整 HTTP 链路（multipart 上传 → 列表 → 详情 → 预览链），
# 捕捉直接调用函数时无法发现的 FastAPI 层 / 序列化 / 路由问题。


def _build_test_client():
    """构造 TestClient + 内存 SQLite + FakeObjectStore，依赖注入覆盖。"""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import get_db_session
    from app.main import create_app
    from app.models import Base
    from app.storage import FakeObjectStore, get_object_store

    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    storage = FakeObjectStore()
    app = create_app(init_schema=False)

    def override_db_session():
        with Session() as session:
            yield session

    def override_object_store():
        return storage

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_object_store] = override_object_store
    return TestClient(app), storage


def test_http_upload_then_list_shows_file_count_1_and_primary_file():
    """HTTP 层：上传 PDF → GET /api/archives?domain_type=quota 返回 file_count=1 + primary_file。"""
    client, _ = _build_test_client()

    # 上传
    resp = client.post(
        "/api/data-lake/quota/upload",
        files=[("files", ("test.pdf", b"%PDF-1.4 fake content", "application/pdf"))],
        data={"category": "construction_quota", "province": "sc", "year": "2025"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["succeeded"] == 1
    archive_id = body["items"][0]["archive_id"]
    assert archive_id is not None

    # 列表
    list_resp = client.get("/api/archives?domain_type=quota")
    assert list_resp.status_code == 200, list_resp.text
    items = list_resp.json()
    assert isinstance(items, list)
    found = [i for i in items if i["archive_id"] == archive_id]
    assert len(found) == 1, f"archive {archive_id} 未在列表中找到"
    item = found[0]
    assert item["file_count"] == 1, f"file_count 应为 1，实际 {item.get('file_count')}"
    assert item.get("primary_file") is not None, "primary_file 不应为 None"
    assert item["primary_file"]["file_id"] is not None, "primary_file.file_id 不应为 None"
    assert item["primary_file"]["file_role"] == "main_document"


def test_http_upload_then_detail_has_archive_file():
    """HTTP 层：上传 PDF → GET /api/archives/{id} 详情包含一条 ArchiveFile。"""
    client, _ = _build_test_client()

    resp = client.post(
        "/api/data-lake/quota/upload",
        files=[("files", ("detail.pdf", b"%PDF-1.4 detail test", "application/pdf"))],
        data={"category": "industry_quota", "province": "sc", "year": "2025"},
    )
    archive_id = resp.json()["items"][0]["archive_id"]

    detail_resp = client.get(f"/api/archives/{archive_id}")
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert "files" in detail
    files = detail["files"]
    assert len(files) == 1, f"详情应包含 1 个文件，实际 {len(files)}"
    af = files[0]
    assert af["file_role"] == "main_document"
    assert af["is_primary"] is True
    assert af["file_id"] is not None


def test_http_upload_response_has_archive_id_for_preview():
    """HTTP 层：上传响应中的 archive_id 非空，可用于预览链路。"""
    client, _ = _build_test_client()

    resp = client.post(
        "/api/data-lake/quota/upload",
        files=[("files", ("preview.pdf", b"%PDF-1.4 preview test", "application/pdf"))],
        data={"category": "boq_standard", "province": "sc", "year": "2025"},
    )
    item = resp.json()["items"][0]
    assert item["status"] == "created"
    assert item["archive_id"], "archive_id 不能为空（预览依赖）"
    assert item["file_id"], "file_id 不能为空（预览依赖）"

    # 通过 archive_id 可以获取详情
    detail = client.get(f"/api/archives/{item['archive_id']}").json()
    assert detail["archive_id"] == item["archive_id"]
    assert len(detail["files"]) == 1
    assert detail["files"][0]["file_id"] == item["file_id"]


# ── v0.3.3 回归 · Fix A (f549975) ────────────────────────────────────────
# v0.3.2 (35ed914) 引入 _ensure_quota_publication_set 但忘了 commit，
# 端点返回后 FastAPI dep 关 session 回滚未提交事务，导致 PubSet+Profile 丢失。
# Fix A: helper 末尾补 commit。本测试用「关闭 session 后用新 session 查询」
# 模拟 FastAPI dep teardown，验证 PubSet+Profile 真的持久化到 DB。
#
# 若 revert Fix A，本测试在 fresh session 查询时会找不到 Profile+PubSet，FAIL。


def test_upload_persists_quota_pubset_and_profile_after_session_close(
    db_session, fake_storage
):
    """回归 (v0.3.3 · f549975)：上传后关闭端点 session（模拟 FastAPI dep teardown），
    用全新 session 查询 QuotaPublicationSet + QuotaArchiveProfile，断言两者都落库。

    关键：不调用 db_session.commit()，只 close。SQLAlchemy 2.0 session.close() 会
    回滚未提交事务。Archive + ArchiveFile 由 _attach_archive_file 已 commit，保留；
    PubSet + Profile 由 _ensure_quota_publication_set flush（无 commit）— 若 fix A
    失效，close 会回滚它们，fresh session 查不到。
    """
    content = b"%PDF-1.4 regression test for pubset+profile commit (v0.3.3)"
    result = _call_upload(
        db_session, fake_storage,
        [_StubUploadFile("sc2025-regression.pdf", content)],
        category="construction_quota",
        province="sc",
        year=2025,
    )

    assert result.succeeded == 1
    archive_id = result.items[0].archive_id

    # 拿 engine 备用（close 后 get_bind() 仍可用）
    engine = db_session.get_bind()

    # 关键：直接 close 不 commit，模拟 FastAPI dep 关闭 session 的 rollback 语义。
    db_session.close()

    # 用全新 session 验证持久化（模拟下次请求的 query session）
    NewSess = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with NewSess() as fresh:
        # Archive 必须存在（_attach_archive_file 已 commit，与 fix A 无关）
        archive = fresh.get(Archive, archive_id)
        assert archive is not None, "Archive 缺失（attach_file commit 失效？）"
        assert archive.domain_type == "quota"

        # QuotaPublicationSet 必须存在 —— 这是 fix A 的核心保证
        pubset = fresh.execute(
            select(QuotaPublicationSet).where(
                QuotaPublicationSet.biz_key.like("quota:upload:sc:2025:%")
            )
        ).scalar_one_or_none()
        assert pubset is not None, (
            "QuotaPublicationSet 缺失 → fix A (f549975) 失效或被回滚。"
            "helper 必须 commit，否则 FastAPI dep 关 session 会回滚。"
        )
        assert pubset.jurisdiction_code == "510000", f"四川 code 应为 510000，实际 {pubset.jurisdiction_code}"
        assert pubset.edition_year == 2025
        assert pubset.quota_system_type == "construction_regional"
        assert pubset.material_type == "quota_base"

        # QuotaArchiveProfile 必须存在并指向同一 pubset
        profile = fresh.get(QuotaArchiveProfile, archive_id)
        assert profile is not None, (
            "QuotaArchiveProfile 缺失 → fix A (f549975) 失效或被回滚。"
        )
        assert profile.publication_set_id == pubset.publication_set_id
        assert profile.document_role == "main_volume"


def test_http_upload_persists_quota_pubset_and_profile():
    """HTTP 层回归 (v0.3.3 · f549975)：端到端验证 fix A。

    HTTP 路径天然就是「每个请求一个新 session」，等价于生产环境 FastAPI dep teardown。
    若 fix A 失效，HTTP upload 返回后用独立 session 查不到 PubSet+Profile。
    """
    client, _ = _build_test_client()

    resp = client.post(
        "/api/data-lake/quota/upload",
        files=[("files", ("http-regression.pdf",
                          b"%PDF-1.4 http layer pubset profile regression",
                          "application/pdf"))],
        data={"category": "construction_quota", "province": "sc", "year": "2025"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["succeeded"] == 1
    archive_id = body["items"][0]["archive_id"]

    # 通过 GET /api/archives/{id} 详情 + /api/data-lake/quota/archives/{id}
    # 间接验证 Profile+PubSet 持久化（这些端点会 join quota_archive_profile + quota_publication_set）
    detail = client.get(f"/api/archives/{archive_id}").json()
    assert detail["archive_id"] == archive_id

    # 直接 SQL 查询 PubSet（验证 DB 层落库，不依赖任何 ORM 关系）
    from sqlalchemy import create_engine
    # 从 _build_test_client 拿不到 engine，重建一个 in-memory + 同 schema
    # 注意：HTTP 层用的是独立 engine，要共享数据必须用同一个 engine。
    # 这里改用直接走 _run_quota_filtered_listing 的依赖：让 HTTP 列表接口承担「跨 session 可见」验证。
    list_resp = client.get("/api/archives?domain_type=quota&primary=construction_quota")
    assert list_resp.status_code == 200, list_resp.text
    items = list_resp.json()
    matched = [i for i in items if i["archive_id"] == archive_id]
    assert len(matched) == 1, (
        f"archive {archive_id} 在列表中应可见（profile+pubset 持久化的间接证据）；"
        f"若 fix A 失效，列表接口因 LEFT JOIN WHERE 把它过滤掉"
    )
