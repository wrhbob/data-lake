from io import BytesIO
from zipfile import ZipFile

from app.assets import register_asset
from app.models import FileAsset, FileProcessing, FileRelation, IngestEvent
from app.runner import run_processing_task


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def unzip_task_id(result):
    assert len(result.processing_ids) == 1
    return result.processing_ids[0]


def test_run_unzip_task_registers_children_and_marks_task_succeeded(db_session, fake_storage):
    archive = zip_bytes(
        {
            "inside/材料信息价.xlsx": b"xlsx child bytes",
            "inside/readme.txt": b"readme bytes",
        }
    )
    parent = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="信息价.zip",
        content=archive,
    )

    run_result = run_processing_task(db_session, fake_storage, unzip_task_id(parent))

    task = db_session.get(FileProcessing, parent.processing_ids[0])
    children = db_session.query(FileAsset).filter(FileAsset.parent_file_id == parent.file_id).all()
    assert run_result.status == "succeeded"
    assert len(run_result.created_file_ids) == 2
    assert run_result.duplicated_file_ids == []
    assert task.status == "succeeded"
    assert task.attempt == 1
    assert task.output_bucket == "cost-extract"
    assert task.output_key == "unzip:2"
    assert task.finished_at is not None
    assert {child.file_name for child in children} == {"inside/材料信息价.xlsx", "inside/readme.txt"}
    assert db_session.query(IngestEvent).count() == 1
    assert fake_storage.put_count == 3


def test_unzip_reuses_existing_child_content_across_different_parent_archives(db_session, fake_storage):
    first_parent = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="first.zip",
        content=zip_bytes({"材料信息价.xlsx": b"same child bytes", "a.txt": b"a"}),
    )
    second_parent = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-002",
        file_name="second.zip",
        content=zip_bytes({"材料信息价.xlsx": b"same child bytes", "b.txt": b"b"}),
    )

    first_run = run_processing_task(db_session, fake_storage, unzip_task_id(first_parent))
    second_run = run_processing_task(db_session, fake_storage, unzip_task_id(second_parent))

    assert len(first_run.created_file_ids) == 2
    assert len(second_run.created_file_ids) == 1
    assert len(second_run.duplicated_file_ids) == 1
    duplicated_child_id = second_run.duplicated_file_ids[0]
    parent_ids = {
        relation.rel_id
        for relation in db_session.query(FileRelation).filter_by(
            file_id=duplicated_child_id,
            rel_type="derived_from",
        )
    }
    assert parent_ids == {first_parent.file_id, second_parent.file_id}
    assert db_session.query(FileAsset).count() == 5
    assert fake_storage.put_count == 5


def test_duplicate_zip_ingest_after_unzip_keeps_single_parent_and_child_set(db_session, fake_storage):
    archive = zip_bytes({"inside/材料信息价.xlsx": b"xlsx child bytes"})
    first = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="first.zip",
        content=archive,
    )
    run_processing_task(db_session, fake_storage, unzip_task_id(first))

    before_asset_count = db_session.query(FileAsset).count()
    before_put_count = fake_storage.put_count
    duplicate = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-002",
        file_name="same-again.zip",
        content=archive,
    )

    assert duplicate.file_id == first.file_id
    assert duplicate.duplicated is True
    assert duplicate.processing_ids == []
    assert db_session.query(FileAsset).count() == before_asset_count
    assert db_session.query(IngestEvent).filter_by(file_id=first.file_id).count() == 2
    assert fake_storage.put_count == before_put_count
