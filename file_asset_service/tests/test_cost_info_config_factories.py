from app.adapters import ADAPTERS
from app.cost_info_config_factories import (
    SITE_CONFIG_FACTORIES,
    build_source_config,
    config_adapter_kind,
    list_factory_rows,
    list_site_ids,
    restore_all_source_configs,
    restore_source_config,
)
from app.cost_info_registry import find_data_source_by_site_id
from app.info_price_site_import import build_registry_config, parser_config_for
from app.models import DataSource

CHONGQING_SITE_ID = "cost_info.cq.cqsgczjxx"


def test_registry_is_non_empty_and_covers_known_provinces():
    site_ids = list_site_ids()
    assert len(site_ids) == len(SITE_CONFIG_FACTORIES)
    assert CHONGQING_SITE_ID in site_ids
    # spot check enumerated multi-site families are present
    assert "cost_info.sc.panzhihua" in site_ids
    assert "cost_info.gd.guangzhou" in site_ids
    assert "cost_info.xj.urumqi" in site_ids


def test_every_factory_yields_crawlable_config_with_matching_site_id():
    seen: set[str] = set()
    for site_id, factory in SITE_CONFIG_FACTORIES.items():
        config = factory()
        assert config["stable"]["site_id"] == site_id
        assert config["stable"].get("entry_url")
        adapter_kind = config_adapter_kind(config)
        assert adapter_kind, site_id
        assert adapter_kind in ADAPTERS, (site_id, adapter_kind)
        assert site_id not in seen
        seen.add(site_id)


def test_list_factory_rows_reports_adapter_kind_per_site():
    rows = {row["site_id"]: row for row in list_factory_rows()}
    assert rows[CHONGQING_SITE_ID]["adapter_kind"] == "chongqing_pdf"
    assert all(row["adapter_kind"] for row in rows.values())


def test_full_config_is_richer_than_csv_summary_for_chongqing():
    full = build_source_config(CHONGQING_SITE_ID)
    parser_version = full["parser"]["active_parser_version"]
    full_parser = full["parser"]["parsers"][parser_version]

    # The CSV ledger summary only knows adapter_kind + list_url(=entry_url).
    summary = parser_config_for(
        {"adapter_kind": "chongqing_pdf", "site_id": CHONGQING_SITE_ID, "entry_url": full["stable"]["entry_url"]}
    )
    summary_parser = summary["parsers"][summary["active_parser_version"]]

    # Full config carries the real endpoints the adapter needs; summary does not.
    assert full_parser["file_list_endpoint"]
    assert full_parser["years_endpoint"]
    assert "file_list_endpoint" not in summary_parser
    # And the summary's list_url is only the site root, which would override the real list page.
    assert summary_parser["list_url"] == full["stable"]["entry_url"]


def test_restore_source_config_creates_then_updates_full_config(db_session):
    created = restore_source_config(db_session, CHONGQING_SITE_ID)
    assert created.action == "created"
    assert created.adapter_kind == "chongqing_pdf"

    source = find_data_source_by_site_id(db_session, CHONGQING_SITE_ID)
    assert source is not None
    parser_version = source.config["parser"]["active_parser_version"]
    assert source.config["parser"]["parsers"][parser_version]["file_list_endpoint"]
    assert source.status == "pending_verify"

    updated = restore_source_config(db_session, CHONGQING_SITE_ID, activate=True)
    assert updated.action == "updated"
    assert updated.source_id == created.source_id
    db_session.refresh(source)
    assert source.status == "active"


def test_restore_all_covers_every_registered_site(db_session):
    results = restore_all_source_configs(db_session)
    assert len(results) == len(SITE_CONFIG_FACTORIES)
    assert {result.site_id for result in results} == set(SITE_CONFIG_FACTORIES)
    assert all(result.action == "created" for result in results)
    for result in results:
        source = find_data_source_by_site_id(db_session, result.site_id)
        assert source is not None
        assert config_adapter_kind(source.config) in ADAPTERS


def test_ledger_import_preserves_factory_parser_not_csv_summary():
    """Re-importing the CSV ledger must NOT clobber a factory-restored parser.

    Regression for the parser_config_for pollution (P0): build_registry_config
    takes the factory parser (with the real endpoints the adapter needs) when a
    site has a registered factory, instead of the CSV summary that only carries
    adapter_kind + the site-root URL as list_url.
    """
    full = build_source_config(CHONGQING_SITE_ID)
    row = {
        "site_id": CHONGQING_SITE_ID,
        "adapter_kind": "chongqing_pdf",
        "entry_url": full["stable"]["entry_url"],
        "crawl_pattern": "li",
        "target_region_code": "500000",
        "target_region_name": "重庆市",
    }
    config = build_registry_config(DataSource(), row)
    parser = config["parser"]
    active_version = parser["active_parser_version"]
    active_parser = parser["parsers"][active_version]
    # factory parser preserved -> real endpoints present; CSV summary NOT used
    assert active_parser.get("file_list_endpoint")
    assert active_parser.get("years_endpoint")
    assert active_parser["adapter_kind"] == "chongqing_pdf"
    # the factory's parser version is used, not the CSV "{site_id}.v1" stub
    assert active_version != f"{CHONGQING_SITE_ID}.v1"


def test_ledger_import_falls_back_to_summary_for_non_factory_site():
    """Sites without a registered factory still get the CSV summary parser."""
    row = {
        "site_id": "cost_info.xx.not_a_factory",
        "adapter_kind": "beijing_pdf",
        "entry_url": "http://example.org/list",
        "crawl_pattern": "li",
    }
    config = build_registry_config(DataSource(), row)
    parser = config["parser"]
    active_parser = parser["parsers"][parser["active_parser_version"]]
    # no factory -> CSV summary: adapter_kind + list_url=entry_url, no endpoints
    assert active_parser["adapter_kind"] == "beijing_pdf"
    assert active_parser["list_url"] == "http://example.org/list"
    assert "file_list_endpoint" not in active_parser
