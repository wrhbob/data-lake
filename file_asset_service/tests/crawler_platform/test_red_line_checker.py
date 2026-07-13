from app.collection import create_collection_task, create_data_source
from app.crawler_platform.red_line_checker import check_file_processing_red_lines
from app.models import FileAsset, FileProcessing, IngestEvent


def make_source_and_task(db_session):
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="德阳市住房和城乡建设局",
        data_domain="cost_info",
    )
    task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="system",
        task_type="crawl_incremental",
        trigger_type="manual",
        data_domain="cost_info",
    )
    return source, task


def add_file_processing_for_task(db_session, source, task):
    asset = FileAsset(
        tenant_code=source.asset_tenant_code,
        bucket="cost-raw",
        object_key=f"objects/{task.task_id}.pdf",
        sha256=f"sha-{task.task_id}",
        file_name="德阳信息价.pdf",
        file_ext=".pdf",
        file_size=128,
    )
    db_session.add(asset)
    db_session.flush()
    db_session.add(
        IngestEvent(
            file_id=asset.file_id,
            source_id=source.source_id,
            task_id=task.task_id,
            source_type=source.source_type,
            batch_id=task.batch_id,
            original_name=asset.file_name,
        )
    )
    db_session.add(
        FileProcessing(
            file_id=asset.file_id,
            processor="pdf_extract",
            status="pending",
        )
    )
    db_session.commit()
    return asset


def test_red_line_checker_reports_file_processing_for_current_task(db_session):
    source, task = make_source_and_task(db_session)
    asset = add_file_processing_for_task(db_session, source, task)

    result = check_file_processing_red_lines([task.task_id], db_session)

    assert result.rows_created == 1
    assert result.passes is False
    assert result.violations == [
        {
            "processing_id": result.violations[0]["processing_id"],
            "file_id": asset.file_id,
            "task_id": task.task_id,
            "processor": "pdf_extract",
            "status": "pending",
            "rule": "FileProcessing count must be 0 for cost_info Layer 0",
        }
    ]


def test_red_line_checker_ignores_file_processing_from_other_tasks(db_session):
    source, task = make_source_and_task(db_session)
    _other_source, other_task = make_source_and_task(db_session)
    add_file_processing_for_task(db_session, source, other_task)

    result = check_file_processing_red_lines([task.task_id], db_session)

    assert result.rows_created == 0
    assert result.passes is True
    assert result.violations == []


def test_red_line_checker_empty_task_ids_passes(db_session):
    result = check_file_processing_red_lines([], db_session)

    assert result.rows_created == 0
    assert result.passes is True
    assert result.violations == []
