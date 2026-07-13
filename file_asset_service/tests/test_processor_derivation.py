from app.assets import register_asset, register_processor_output
from app.models import FileAsset, FileProcessing
from app.processors import derive_processors


def processor_names(db_session, file_id):
    return [
        row.processor
        for row in db_session.query(FileProcessing).filter_by(file_id=file_id).order_by(FileProcessing.processor)
    ]


def test_info_price_xlsx_does_not_create_layer0_content_parse_tasks(db_session, fake_storage):
    result = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="信息价.xlsx",
        content=b"xlsx bytes",
    )

    assert processor_names(db_session, result.file_id) == []


def test_processor_derivation_by_extension():
    assert derive_processors(".zip", "info_price") == ["unzip"]
    assert derive_processors("pdf", "info_price") == []
    assert derive_processors("xlsx", "info_price") == []
    assert derive_processors("xlsx", "info_price_governance") == ["xls_parse", "info_price_parse"]
    assert derive_processors("dwg", "project_archive") == ["dwg_render", "dwg_extract"]


def test_zip_child_outputs_reuse_main_dedupe_path(db_session, fake_storage):
    parent = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="archive.zip",
        content=b"zip bytes",
    )

    first_child = register_processor_output(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        processor="unzip",
        parent_file_id=parent.file_id,
        file_name="inside/材料信息价.xlsx",
        content=b"same child xlsx bytes",
    )
    second_child = register_processor_output(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        processor="unzip",
        parent_file_id=parent.file_id,
        file_name="inside/材料信息价-copy.xlsx",
        content=b"same child xlsx bytes",
    )

    child_asset = db_session.get(FileAsset, first_child.file_id)
    assert first_child.file_id == second_child.file_id
    assert second_child.duplicated is True
    assert child_asset.parent_file_id == parent.file_id
    assert db_session.query(FileAsset).count() == 2
    assert fake_storage.put_count == 2


def test_manual_upload_info_price_enqueues_parse_task():
    # a. pdf via manual channel enqueues info_price_parse
    assert derive_processors(".pdf", "info_price", channel_type="manual_upload") == ["info_price_parse"]
    # 补充：xls/xlsx 经 manual 通道只回 info_price_parse（消费者直读 xlsx，不叠 xls_parse）
    assert derive_processors(".xlsx", "info_price", channel_type="manual_upload") == ["info_price_parse"]
    assert derive_processors(".xls", "info_price", channel_type="manual_upload") == ["info_price_parse"]
    # b. zip 优先级在 manual 门控之上不被覆盖
    assert derive_processors(".zip", "info_price", channel_type="manual_upload") == ["unzip"]
    # 非文档扩展名即便 manual 通道也不入队
    assert derive_processors(".jpg", "info_price", channel_type="manual_upload") == []


def test_crawler_info_price_channel_unchanged():
    # c. 回归护栏：爬虫路径（非 manual_upload 通道）info_price 仍返回 []
    assert derive_processors(".pdf", "info_price") == []
    assert derive_processors(".pdf", "info_price", channel_type=None) == []
    assert derive_processors(".pdf", "info_price", channel_type="crawler") == []
    assert derive_processors(".xlsx", "info_price", channel_type="crawler") == []


def test_register_asset_manual_upload_creates_pending_parse_task(db_session, fake_storage):
    # d. 端到端：manual_upload 通道的 info_price 文件入湖后建 pending 的 info_price_parse 任务。
    # ⚠️ 必须用全新 content（新 sha256）；重复文件走 reuse 分支不建任务（assets.py:148-170）。
    result = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-manual",
        file_name="眉山2025第5期信息价.pdf",
        content=b"unique-manual-upload-bytes-pdf",
        source_metadata={"channel_type": "manual_upload"},
        channel_type="manual_upload",
    )

    assert "info_price_parse" in processor_names(db_session, result.file_id)
    tasks = (
        db_session.query(FileProcessing)
        .filter_by(file_id=result.file_id, processor="info_price_parse")
        .all()
    )
    assert tasks and all(task.status == "pending" for task in tasks)

    # 重复 content 复用 asset，本次不建任何新任务
    duplicate = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-manual-2",
        file_name="same.pdf",
        content=b"unique-manual-upload-bytes-pdf",
        channel_type="manual_upload",
    )
    assert duplicate.duplicated is True
    assert duplicate.processing_ids == []
