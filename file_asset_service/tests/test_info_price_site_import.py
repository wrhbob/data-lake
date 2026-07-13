from pathlib import Path

from app.info_price_site_import import import_info_price_site_ledger
from app.models import CollectionTask, DataSource


def write_site_csv(path: Path) -> None:
    path.write_text(
        "\ufeffseq,source_type,province,city,name,frequency,latest_period_raw,period_start,period_end,period_note,format,downloadable,status,bucket,owner,reviewer,url,url2,remark\n"
        "1,info_price,北京,北京市,北京市信息价,月刊,4月,2026-04,2026-04,,PDF,是,正常,可自动采,叶婷,李岚,https://example.com/beijing,,\n"
        "2,info_price,河北省,保定市,保定市信息价,月刊,,,,无最新期,网页,否,需会员,需人工,叶婷,李岚,https://example.com/baoding,,需要会员\n"
        "3,info_price,河北省,张家口市,张家口市信息价,,,,,无最新期,,否,未配置,待找源,叶婷,李岚,无,,\n"
        "4,info_price,甘肃省,兰州市,兰州市信息价,2月刊,2月,,,需人工归一,PDF,是,正常,可自动采,李岚,李岚,https://example.com/lanzhou,http://example.com/lanzhou-alt,\n"
        "5,info_price,河南省,省站,省站信息价,月刊,4月,2026-04,2026-04,,PDF,是,正常,可自动采,叶婷,李岚,https://example.com/henan,,\n"
        "6,info_price,湖北省,省站,省站信息价,月刊,4月,2026-04,2026-04,,PDF,是,正常,可自动采,叶婷,李岚,https://example.com/hubei,,\n",
        encoding="utf-8",
    )


def test_import_info_price_site_ledger_maps_fields_and_is_idempotent(db_session, tmp_path):
    csv_path = tmp_path / "sites.csv"
    write_site_csv(csv_path)

    first = import_info_price_site_ledger(db_session, csv_path)
    second = import_info_price_site_ledger(db_session, csv_path)

    assert first.total_rows == 6
    assert first.bucket_counts == {"可自动采": 4, "需人工": 1, "待找源": 1}
    assert first.period_start_count == 3
    assert second.created_sources == 0
    assert second.created_tasks == 0
    assert db_session.query(DataSource).count() == 6
    assert db_session.query(CollectionTask).count() == 6

    beijing = (
        db_session.query(DataSource)
        .filter(DataSource.province == "北京", DataSource.city == "北京市")
        .one()
    )
    assert beijing.base_url == "https://example.com/beijing"
    assert beijing.url == "https://example.com/beijing"
    assert beijing.format == "PDF"
    assert beijing.downloadable is True
    assert beijing.status == "pending_verify"
    assert beijing.bucket == "可自动采"
    assert beijing.owner == "叶婷"
    assert beijing.reviewer == "李岚"
    assert beijing.frequency == "月刊"
    assert beijing.config["ledger_seq"] == 1

    beijing_task = db_session.query(CollectionTask).filter_by(source_id=beijing.source_id).one()
    assert beijing_task.task_type == "ledger_import"
    assert beijing_task.trigger_type == "import"
    assert beijing_task.status == "ready"
    assert beijing_task.period_raw == "4月"
    assert beijing_task.period_start == "2026-04"
    assert beijing_task.period_end == "2026-04"
    assert beijing_task.period_note is None

    missing = (
        db_session.query(DataSource)
        .filter(DataSource.province == "河北省", DataSource.city == "张家口市")
        .one()
    )
    assert missing.base_url is None
    assert missing.url is None
    assert missing.bucket == "待找源"
    assert missing.downloadable is False

    lanzhou = (
        db_session.query(DataSource)
        .filter(DataSource.province == "甘肃省", DataSource.city == "兰州市")
        .one()
    )
    assert lanzhou.url_alt == "http://example.com/lanzhou-alt"
    lanzhou_task = db_session.query(CollectionTask).filter_by(source_id=lanzhou.source_id).one()
    assert lanzhou_task.period_raw == "2月"
    assert lanzhou_task.period_start is None
    assert lanzhou_task.period_end is None
    assert lanzhou_task.period_note == "需人工归一"

    duplicated_names = db_session.query(DataSource).filter(DataSource.name == "省站信息价").all()
    assert {source.province for source in duplicated_names} == {"河南省", "湖北省"}


def test_import_national_info_price_source_row_writes_cost_info_registry_config(db_session, tmp_path):
    csv_path = tmp_path / "national_sources.csv"
    csv_path.write_text(
        "\ufeffseq,admin_division_version,province_code,province_name,target_region_code,target_region_name,target_level,"
        "source_status,site_id,source_type,province,city,name,source_name,publisher_name,publisher_scope,"
        "entry_url,evidence_url,url,format,downloadable,bucket,frequency,latest_period_raw,period_start,period_end,"
        "source_attachment_mode,publication_mode,adapter_kind,crawl_pattern,price_kind,period_kind,"
        "blocked_reason,manual_path,audit_note,review_status\n"
        "1,mca_geoname_2026_snapshot,510000,四川省,510300,自贡市,prefecture,"
        "auto_crawl_ready,cost_info.sc.zigong,info_price,四川省,自贡市,自贡市信息价,自贡市信息价,自贡市住房和城乡建设局,city,"
        "https://example.com/zigong,https://example.com/zigong/list,https://example.com/zigong,ZIP,是,可自动采,月刊,2026年5月,2026-05,2026-05,"
        "zip_package,DIRECT_WEB,sichuan_pdf,li,guidance,monthly,,,公开 ZIP 原件,accepted\n",
        encoding="utf-8",
    )

    result = import_info_price_site_ledger(db_session, csv_path)

    assert result.total_rows == 1
    source = db_session.query(DataSource).one()
    task = db_session.query(CollectionTask).one()

    assert source.source_type == "info_price"
    assert source.data_domain == "cost_info"
    assert source.connector_type == "source_registry"
    assert source.status == "pending_verify"
    assert source.region_code == "510300"
    assert source.schedule_policy == {
        "enabled": False,
        "frequency": "daily",
        "timezone": "Asia/Shanghai",
        "max_attempts": 3,
        "early_stop_duplicate": True,
        "rate_limit": {
            "host": "example.com",
            "max_concurrent": 1,
            "min_delay_seconds": 8,
            "jitter_seconds": 4,
        },
    }
    assert source.config["registry_schema_version"] == "source_registry.v1"
    assert source.config["stable"] == {
        "site_id": "cost_info.sc.zigong",
        "domain_type": "cost_info",
        "province": "四川省",
        "city": "自贡市",
        "region_code": "510300",
        "coverage_region_code": "510300",
        "publisher_name": "自贡市住房和城乡建设局",
        "publisher_scope": "city",
        "entry_url": "https://example.com/zigong",
    }
    assert source.config["parser"]["active_parser_version"] == "cost_info.sc.zigong.v1"
    assert source.config["parser"]["parsers"]["cost_info.sc.zigong.v1"]["adapter_kind"] == "sichuan_pdf"
    assert source.config["source_shape"]["source_attachment_mode"] == "zip_package"
    assert source.config["reachability"]["source_status"] == "auto_crawl_ready"
    assert source.config["coverage_expectation"]["target_regions"] == [
        {
            "region_code": "510300",
            "region_name": "自贡市",
            "target_level": "prefecture",
            "business_coverage_status": "pending_verify",
            "source_completeness_status": "city_source_present",
            "coverage_note": "公开 ZIP 原件",
        }
    ]
    assert source.config["audit"]["review_status"] == "accepted"
    assert task.data_domain == "cost_info"
    assert task.config_override["site_id"] == "cost_info.sc.zigong"
