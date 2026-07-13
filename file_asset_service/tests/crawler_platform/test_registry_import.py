from datetime import UTC, datetime

import pytest

from app.collection import create_collection_task
from app.cost_info_registry import (
    find_data_source_by_site_id,
    import_source_config,
    list_cost_info_sources,
    set_active_source,
    show_source,
)
from app.sichuan_pdf_cost_info import deyang_cost_info_source_config


def deyang_config(*, adapter_kind="mock", list_url="https://zjj.deyang.gov.cn/xxj/"):
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.sc.deyang",
            "domain_type": "cost_info",
            "region_code": "510600",
            "coverage_region_code": "510600",
            "province": "四川省",
            "city": "德阳市",
            "publisher_name": "德阳市住房和城乡建设局",
            "publisher_scope": "city",
            "entry_url": "https://zjj.deyang.gov.cn/",
        },
        "parser": {
            "active_parser_version": "deyang.cost-info-pdf-list.v1",
            "parsers": {
                "deyang.cost-info-pdf-list.v1": {
                    "adapter_kind": adapter_kind,
                    "list_url": list_url,
                    "pagination": {"type": "none"},
                }
            },
        },
        "source_shape": {
            "source_attachment_mode": "pdf_only",
            "parsability": "text_pdf",
            "publication_mode": "DIRECT_PDF",
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "tax_type": None,
        },
        "schedule_policy": {
            "enabled": True,
            "frequency": "daily",
            "timezone": "Asia/Shanghai",
            "max_attempts": 3,
            "rate_limit": {
                "host": "zjj.deyang.gov.cn",
                "min_delay_seconds": 8,
                "jitter_seconds": 4,
            },
        },
    }


def test_import_source_config_creates_pending_verify_cost_info_source(db_session):
    result = import_source_config(deyang_config(), db_session)

    source = find_data_source_by_site_id(db_session, "cost_info.sc.deyang")
    assert result.action == "created"
    assert result.site_id == "cost_info.sc.deyang"
    assert result.source_id == source.source_id
    assert source.status == "pending_verify"
    assert source.source_scope == "platform_public"
    assert source.tenant_code is None
    assert source.managed_by == "platform"
    assert source.source_type == "info_price"
    assert source.connector_type == "source_registry"
    assert source.data_domain == "cost_info"
    assert source.name == "德阳市住房和城乡建设局"
    assert source.base_url == "https://zjj.deyang.gov.cn/"
    assert source.url == "https://zjj.deyang.gov.cn/"
    assert source.province == "四川省"
    assert source.city == "德阳市"
    assert source.region_code == "510600"
    assert source.config["stable"]["site_id"] == "cost_info.sc.deyang"
    assert source.schedule_policy["rate_limit"]["min_delay_seconds"] == 8


def test_import_source_config_updates_config_without_overwriting_runtime_state(db_session):
    source = find_data_source_by_site_id(db_session, "cost_info.sc.deyang")
    assert source is None
    import_source_config(deyang_config(), db_session)
    source = find_data_source_by_site_id(db_session, "cost_info.sc.deyang")
    source.status = "active"
    source.created_at = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="system",
        task_type="crawl_incremental",
        trigger_type="scheduled",
        data_domain="cost_info",
        status="pending",
    )
    db_session.commit()

    updated_config = deyang_config(adapter_kind="sichuan_pdf", list_url="https://zjj.deyang.gov.cn/new-list/")
    updated_config["schedule_policy"]["max_attempts"] = 5
    result = import_source_config(updated_config, db_session)

    db_session.refresh(source)
    assert result.action == "updated"
    assert source.status == "active"
    assert source.created_at == datetime(2026, 6, 1, 8, 0)
    assert source.config["parser"]["parsers"]["deyang.cost-info-pdf-list.v1"]["adapter_kind"] == "sichuan_pdf"
    assert source.config["parser"]["parsers"]["deyang.cost-info-pdf-list.v1"]["list_url"] == "https://zjj.deyang.gov.cn/new-list/"
    assert source.schedule_policy["max_attempts"] == 5
    assert db_session.get(type(task), task.task_id).status == "pending"


def test_import_source_config_derives_schedule_policy_from_ops_for_real_source_config(db_session):
    result = import_source_config(deyang_cost_info_source_config(), db_session)

    source = find_data_source_by_site_id(db_session, "cost_info.sc.deyang")
    active_version = source.config["parser"]["active_parser_version"]
    parser = source.config["parser"]["parsers"][active_version]
    assert result.action == "created"
    assert parser["adapter_kind"] == "sichuan_pdf"
    assert source.schedule_policy == {
        "enabled": True,
        "frequency": "monthly",
        "timezone": "Asia/Shanghai",
        "max_attempts": 3,
        "early_stop_duplicate": True,
        "rate_limit": {
            "host": "www.deyang.gov.cn",
            "max_concurrent": 1,
            "min_delay_seconds": 5,
            "jitter_seconds": 5,
        },
    }


def test_list_show_and_set_active_source(db_session):
    import_source_config(deyang_config(), db_session)

    rows = list_cost_info_sources(db_session)
    assert rows == [
        {
            "source_id": rows[0]["source_id"],
            "site_id": "cost_info.sc.deyang",
            "name": "德阳市住房和城乡建设局",
            "status": "pending_verify",
            "province": "四川省",
            "city": "德阳市",
            "region_code": "510600",
            "adapter_kind": "mock",
        }
    ]

    shown = show_source(db_session, site_id="cost_info.sc.deyang")
    assert shown["source_id"] == rows[0]["source_id"]
    assert shown["config"]["stable"]["site_id"] == "cost_info.sc.deyang"

    activated = set_active_source(db_session, site_id="cost_info.sc.deyang")
    assert activated["status"] == "active"
    assert show_source(db_session, source_id=rows[0]["source_id"])["status"] == "active"


def test_show_and_set_active_require_a_selector(db_session):
    with pytest.raises(ValueError, match="SOURCE_SELECTOR_REQUIRED"):
        show_source(db_session)
    with pytest.raises(ValueError, match="SOURCE_SELECTOR_REQUIRED"):
        set_active_source(db_session)
