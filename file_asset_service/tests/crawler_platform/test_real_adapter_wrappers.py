from datetime import UTC, datetime
from urllib.error import HTTPError

from app.adapters import get_adapter
from app.adapters.base_cost_info_adapter import DiscoveredIssue
from app.archive_rules import build_cost_info_business_key
from app.beijing_cost_info import beijing_cost_info_source_config
from app.chongqing_cost_info import chongqing_cost_info_source_config
from app.collection import create_collection_task, create_data_source
from app.cost_info_worker import run_worker
from app.hefei_cost_info import hefei_cost_info_source_config
from app.hubei_cost_info import hubei_cost_info_source_config
from app.jiuquan_cost_info import jiuquan_cost_info_source_config
from app.jinan_cost_info import jinan_cost_info_source_config
from app.linxia_cost_info import linxia_cost_info_source_config
from app.models import Archive
from app.nanjing_cost_info import nanjing_cost_info_source_config
from app.ningbo_cost_info import ningbo_cost_info_source_config
from app.pingliang_cost_info import pingliang_cost_info_source_config
from app.qingyang_cost_info import qingyang_cost_info_source_config
from app.shanghai_cost_info import shanghai_cost_info_source_config
from app.shangluo_cost_info import shangluo_cost_info_source_config
from app.sichuan_pdf_cost_info import deyang_cost_info_source_config
from app.storage import FakeObjectStore
from app.wuhan_cost_info import wuhan_cost_info_source_config
from app.xinjiang_cost_info import xinjiang_urumqi_cost_info_source_config
from app.zhangye_cost_info import zhangye_cost_info_source_config


class FakeClient:
    pass


def source_with_config(db_session, config: dict, *, name="测试造价站"):
    stable = config["stable"]
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name=name,
        data_domain="cost_info",
        base_url=stable["entry_url"],
        url=stable["entry_url"],
        region_code=stable.get("region_code"),
        config=config,
        schedule_policy={"max_items_per_run": 20},
        status="active",
    )


def task_for_source(db_session, source):
    task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="system",
        task_type="crawl_incremental",
        trigger_type="manual",
        data_domain="cost_info",
    )
    task.scheduled_at = datetime(2026, 6, 26, 2, 0, tzinfo=UTC)
    db_session.commit()
    return task


def test_dispatch_registers_first_real_cost_info_adapters():
    assert get_adapter("sichuan_pdf").adapter_kind == "sichuan_pdf"
    assert get_adapter("chongqing_pdf").adapter_kind == "chongqing_pdf"
    assert get_adapter("aspnet_ajax_area").adapter_kind == "aspnet_ajax_area"
    assert get_adapter("beijing_pdf").adapter_kind == "beijing_pdf"
    assert get_adapter("shanghai_excel").adapter_kind == "shanghai_excel"
    assert get_adapter("hubei_pdf").adapter_kind == "hubei_pdf"
    assert get_adapter("wuhan_pdf").adapter_kind == "wuhan_pdf"
    assert get_adapter("nanjing_pdf").adapter_kind == "nanjing_pdf"
    assert get_adapter("ningbo_pdf").adapter_kind == "ningbo_pdf"
    assert get_adapter("hefei_pdf").adapter_kind == "hefei_pdf"
    assert get_adapter("jinan_pdf").adapter_kind == "jinan_pdf"
    assert get_adapter("jiuquan_pdf").adapter_kind == "jiuquan_pdf"
    assert get_adapter("linxia_xlsx").adapter_kind == "linxia_xlsx"
    assert get_adapter("zhangye_zip").adapter_kind == "zhangye_zip"
    assert get_adapter("pingliang_pdf").adapter_kind == "pingliang_pdf"
    assert get_adapter("qingyang_pdf").adapter_kind == "qingyang_pdf"
    assert get_adapter("shangluo_pdf").adapter_kind == "shangluo_pdf"


def test_first_real_cost_info_source_configs_expose_worker_adapter_kind():
    configs = [
        (deyang_cost_info_source_config(), "sichuan_pdf"),
        (chongqing_cost_info_source_config(), "chongqing_pdf"),
        (xinjiang_urumqi_cost_info_source_config(), "aspnet_ajax_area"),
        (beijing_cost_info_source_config(), "beijing_pdf"),
        (shanghai_cost_info_source_config(), "shanghai_excel"),
        (hubei_cost_info_source_config(), "hubei_pdf"),
        (wuhan_cost_info_source_config(), "wuhan_pdf"),
        (nanjing_cost_info_source_config(), "nanjing_pdf"),
        (ningbo_cost_info_source_config(), "ningbo_pdf"),
        (hefei_cost_info_source_config(), "hefei_pdf"),
        (jinan_cost_info_source_config(), "jinan_pdf"),
        (jiuquan_cost_info_source_config(), "jiuquan_pdf"),
        (linxia_cost_info_source_config(), "linxia_xlsx"),
        (zhangye_cost_info_source_config(), "zhangye_zip"),
        (pingliang_cost_info_source_config(), "pingliang_pdf"),
        (qingyang_cost_info_source_config(), "qingyang_pdf"),
        (shangluo_cost_info_source_config(), "shangluo_pdf"),
    ]

    for config, expected_adapter_kind in configs:
        active_version = config["parser"]["active_parser_version"]
        parser = config["parser"]["parsers"][active_version]
        assert parser["adapter_kind"] == expected_adapter_kind


def test_sichuan_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import sichuan_pdf

    calls = []

    def fake_list(client, *, parser):
        calls.append(("list", client, parser["list_url"]))
        return [
            {
                "source_item_id": "dy-2026-06",
                "title": "德阳市2026年6月建筑材料市场价格信息",
                "publish_date": "2026-06-20",
                "detail_url": "https://zjj.deyang.gov.cn/detail/202606",
                "attachments": [{"url": "https://zjj.deyang.gov.cn/files/202606.pdf"}],
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, parser, actor_id, task_id=None):
        calls.append(("ingest", session, storage, source_id, client, row["source_item_id"], actor_id, task_id))

    monkeypatch.setattr(sichuan_pdf, "list_sichuan_pdf_issues", fake_list)
    monkeypatch.setattr(sichuan_pdf, "ingest_sichuan_pdf_issue", fake_ingest)
    source = source_with_config(db_session, deyang_cost_info_source_config())
    task = task_for_source(db_session, source)
    adapter = get_adapter("sichuan_pdf")
    client = FakeClient()

    issues = adapter.discover(source, task, client)
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="dy-2026-06",
            title="德阳市2026年6月建筑材料市场价格信息",
            publish_date="2026-06-20",
            period_raw="德阳市2026年6月建筑材料市场价格信息",
            detail_url="https://zjj.deyang.gov.cn/detail/202606",
            attachment_urls=["https://zjj.deyang.gov.cn/files/202606.pdf"],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_worker_uses_real_adapter_business_key_for_early_stop(db_session, monkeypatch):
    from app.adapters import sichuan_pdf

    title = "德阳市2026年6月建筑材料市场价格信息"

    def fake_list(client, *, parser):
        return [
            {
                "source_item_id": "dy-2026-06",
                "title": title,
                "publish_date": "2026-06-20",
                "detail_url": "https://zjj.deyang.gov.cn/detail/202606",
            }
            for _index in range(3)
        ]

    def fake_ingest(*_args, **_kwargs):
        raise AssertionError("duplicate canonical business_key should stop before ingest")

    monkeypatch.setattr(sichuan_pdf, "list_sichuan_pdf_issues", fake_list)
    monkeypatch.setattr(sichuan_pdf, "ingest_sichuan_pdf_issue", fake_ingest)
    source = source_with_config(db_session, deyang_cost_info_source_config())
    source.schedule_policy = {"max_items_per_run": 3, "early_stop_duplicate": True}
    task_for_source(db_session, source)
    db_session.add(
        Archive(
            domain_type="cost_info",
            channel_type="crawler",
            business_key=build_cost_info_business_key(
                source_id=source.source_id,
                region_code=source.region_code,
                period="2026-06",
                title=title,
            ),
            title=title,
            source_id=source.source_id,
            tenant_code=source.asset_tenant_code,
            visibility_scope="public",
            status="collected",
        )
    )
    db_session.commit()

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    assert report.duplicate_count == 3
    assert report.archive_created_count == 0
    assert report.per_source[0]["early_stopped"] is True


def test_chongqing_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import chongqing_pdf

    calls = []

    def fake_list(client, *, parser):
        calls.append(("list", client, parser["file_list_endpoint"]))
        return [
            {
                "guid": "cq-guid-1",
                "fileTitle": "2026年第六期 总第415期",
                "filePath": "|stored.pdf|重庆2026年第六期.pdf|128",
                "pubDate": "2026-06-18T14:17:26.163",
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, parser, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["guid"], actor_id, task_id))

    monkeypatch.setattr(chongqing_pdf, "list_chongqing_pdf_issues", fake_list)
    monkeypatch.setattr(chongqing_pdf, "ingest_chongqing_pdf_row", fake_ingest)
    config = chongqing_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("chongqing_pdf")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues[0].source_item_key == "cq-guid-1"
    assert issues[0].title == "2026年第六期 总第415期"
    assert issues[0].publish_date == "2026-06-18"
    assert issues[0].detail_url == config["stable"]["entry_url"]
    assert calls[1][-1] == task.task_id


def test_chongqing_pdf_adapter_bootstraps_cookie_and_retries_412(db_session, monkeypatch):
    from app.adapters import chongqing_pdf

    class Inner:
        cookie = None

    class CookieAwareClient:
        def __init__(self):
            self.inner = Inner()
            self.calls = []

        def post_form_json(self, url, payload):
            self.calls.append(("post_form_json", url, payload, self.inner.cookie))
            if self.inner.cookie is None:
                raise HTTPError(url, 412, "Precondition Failed", hdrs=None, fp=None)
            return {
                "_Items": [
                    {
                        "guid": "cq-cookie-guid",
                        "fileTitle": "2026年第六期 总第415期",
                        "filePath": "|stored.pdf|重庆2026年第六期.pdf|128",
                        "pubDate": "2026-06-18T14:17:26.163",
                    }
                ]
            }

    bootstrap_calls = []

    def fake_bootstrap():
        bootstrap_calls.append("called")
        return "anti=token"

    monkeypatch.setattr(chongqing_pdf, "bootstrap_chongqing_cookie_with_playwright", fake_bootstrap, raising=False)
    config = chongqing_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    client = CookieAwareClient()

    issues = get_adapter("chongqing_pdf").discover(source, task, client)

    assert bootstrap_calls == ["called"]
    assert client.inner.cookie == "anti=token"
    assert [call[3] for call in client.calls] == [None, "anti=token"]
    assert issues[0].source_item_key == "cq-cookie-guid"


def test_beijing_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import beijing_pdf

    calls = []

    def fake_list(client, *, parser):
        calls.append(("list", client, parser["list_url"]))
        return [
            {
                "source_item_id": "bj-2026-06",
                "title": "2026年6月北京工程造价信息",
                "publish_date": "2026-06-20",
                "detail_url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202606.pdf",
                "attachments": [{"url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202606.pdf"}],
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, parser, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_id"], actor_id, task_id))

    monkeypatch.setattr(beijing_pdf, "list_beijing_cost_info_issues", fake_list)
    monkeypatch.setattr(beijing_pdf, "ingest_beijing_cost_info_issue", fake_ingest)
    config = beijing_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("beijing_pdf")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="bj-2026-06",
            title="2026年6月北京工程造价信息",
            publish_date="2026-06-20",
            period_raw="2026年6月北京工程造价信息",
            detail_url="https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202606.pdf",
            attachment_urls=["https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202606.pdf"],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_beijing_pdf_adapter_filters_discovery_by_coverage_backfill_period(db_session, monkeypatch):
    from app.adapters import beijing_pdf

    def fake_list(client, *, parser):
        return [
            {
                "source_item_id": "bj-2026-05",
                "title": "2026年5月北京工程造价信息",
                "publish_date": "2026-05-20",
                "detail_url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202605.pdf",
                "attachments": [{"url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202605.pdf"}],
            },
            {
                "source_item_id": "bj-2026-06",
                "title": "2026年6月北京工程造价信息",
                "publish_date": "2026-06-20",
                "detail_url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202606.pdf",
                "attachments": [{"url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202606.pdf"}],
            },
        ]

    monkeypatch.setattr(beijing_pdf, "list_beijing_cost_info_issues", fake_list)
    source = source_with_config(db_session, beijing_cost_info_source_config())
    task = task_for_source(db_session, source)
    task.config_override = {
        "coverage_backfill": {
            "region_code": "110000",
            "period": "2026-06",
            "requested_periods": ["2026-06"],
            "source_id": source.source_id,
            "site_id": "cost_info.bj.zjw.main",
        }
    }
    db_session.commit()

    issues = get_adapter("beijing_pdf").discover(source, task, FakeClient())

    assert [issue.source_item_key for issue in issues] == ["bj-2026-06"]


def test_beijing_pdf_adapter_lowers_min_period_for_coverage_backfill_request(db_session, monkeypatch):
    from app.adapters import beijing_pdf

    observed_min_periods = []

    def fake_list(client, *, parser):
        observed_min_periods.append(parser.get("min_period"))
        return [
            {
                "source_item_id": "bj-2025-01",
                "title": "2025年1月北京工程造价信息",
                "publish_date": "2025-01-20",
                "detail_url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202501.pdf",
                "attachments": [{"url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202501.pdf"}],
            },
            {
                "source_item_id": "bj-2026-06",
                "title": "2026年6月北京工程造价信息",
                "publish_date": "2026-06-20",
                "detail_url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202606.pdf",
                "attachments": [{"url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/202606.pdf"}],
            },
        ]

    monkeypatch.setattr(beijing_pdf, "list_beijing_cost_info_issues", fake_list)
    config = beijing_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    task.config_override = {
        "coverage_backfill": {
            "region_code": "110000",
            "period": "2025-01",
            "requested_periods": ["2025-01"],
            "source_id": source.source_id,
            "site_id": "cost_info.bj.zjw.main",
        }
    }
    db_session.commit()

    issues = get_adapter("beijing_pdf").discover(source, task, FakeClient())

    assert observed_min_periods == ["2025-01"]
    active = source.config["parser"]["active_parser_version"]
    assert source.config["parser"]["parsers"][active]["min_period"] == "2026-01"
    assert [issue.source_item_key for issue in issues] == ["bj-2025-01"]


def test_shanghai_excel_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import shanghai_excel

    calls = []

    def fake_list(client, *, parser, page=1, page_size=None):
        calls.append(("list", client, parser["list_api_url"], page, page_size))
        return [
            {
                "source_item_key": "003002/989178665503817728",
                "source_item_id": "989178665503817728",
                "title": "2026年6月信息价",
                "period": "2026-06",
                "period_year": 2026,
                "period_month": 6,
                "publish_date": "2026-06-20",
                "detail_url": "https://ciac.zjw.sh.gov.cn/JGBXMGCZJInterWeb/pc/#/hyxxHynr?bmCode=003002",
                "file_id": "file-202606",
                "attachments": [{"url": "https://ciac.zjw.sh.gov.cn/download?id=file-202606"}],
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_key"], actor_id, task_id))

    monkeypatch.setattr(shanghai_excel, "list_shanghai_cost_info_issues", fake_list)
    monkeypatch.setattr(shanghai_excel, "ingest_shanghai_cost_info_issue", fake_ingest)
    config = shanghai_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("shanghai_excel")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="003002/989178665503817728",
            title="2026年6月信息价",
            publish_date="2026-06-20",
            period_raw="2026年6月信息价",
            detail_url="https://ciac.zjw.sh.gov.cn/JGBXMGCZJInterWeb/pc/#/hyxxHynr?bmCode=003002",
            attachment_urls=["https://ciac.zjw.sh.gov.cn/download?id=file-202606"],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_hubei_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import hubei_pdf

    calls = []

    def fake_list(client, *, parser, page=1, page_size=None):
        calls.append(("list", client, parser["list_api_url"], page, page_size))
        return [
            {
                "source_item_key": "bJournal/1921840662938880",
                "title": "2026年第2期《湖北工程造价管理》",
                "listed_title": "湖北工程造价管理",
                "period": "2026年第2期",
                "period_year": 2026,
                "period_issue_no": 2,
                "publish_date": "2026-05-25",
                "detail_url": "http://bzcljg.hbcic.net.cn:8091/gcms-busi/#/materialMessageInquire/periodical?type=self",
                "attachments": [{"url": "http://bzcljg.hbcic.net.cn:8091/basic/o/file/download-img?remoteFile=issue.pdf"}],
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_key"], actor_id, task_id))

    monkeypatch.setattr(hubei_pdf, "list_hubei_cost_info_issues", fake_list)
    monkeypatch.setattr(hubei_pdf, "ingest_hubei_cost_info_issue", fake_ingest)
    config = hubei_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("hubei_pdf")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="bJournal/1921840662938880",
            title="2026年第2期《湖北工程造价管理》",
            publish_date="2026-05-25",
            period_raw="2026年第2期《湖北工程造价管理》",
            detail_url="http://bzcljg.hbcic.net.cn:8091/gcms-busi/#/materialMessageInquire/periodical?type=self",
            attachment_urls=["http://bzcljg.hbcic.net.cn:8091/basic/o/file/download-img?remoteFile=issue.pdf"],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_wuhan_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import wuhan_pdf

    calls = []

    def fake_list(client, *, parser, page=1):
        calls.append(("list", client, parser["list_url"], page))
        return [
            {
                "source_item_key": "xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml",
                "title": "市招标设计造价中心、市绿建中心关于发布2026年5月武汉市建设工程综合价格信息的通知",
                "period": "2026-05",
                "period_year": 2026,
                "period_month": 5,
                "publish_date": "2026-05-29",
                "detail_url": "https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml",
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_key"], actor_id, task_id))

    monkeypatch.setattr(wuhan_pdf, "list_wuhan_cost_info_issues", fake_list)
    monkeypatch.setattr(wuhan_pdf, "ingest_wuhan_cost_info_issue", fake_ingest)
    config = wuhan_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("wuhan_pdf")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml",
            title="市招标设计造价中心、市绿建中心关于发布2026年5月武汉市建设工程综合价格信息的通知",
            publish_date="2026-05-29",
            period_raw="市招标设计造价中心、市绿建中心关于发布2026年5月武汉市建设工程综合价格信息的通知",
            detail_url="https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml",
            attachment_urls=[],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_nanjing_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import nanjing_pdf

    calls = []

    def fake_list(client, *, parser, max_pages=1):
        calls.append(("list", client, parser["list_url"], max_pages))
        return [
            {
                "source_item_key": "c481dc5c-314e-4edb-a259-d85e0cb2954f",
                "title": "南京市2026年5月建设工程材料市场信息价格",
                "listed_title": "南京市二〇二六年五月建设工程材料市场信息价格",
                "period": "2026-05",
                "period_year": 2026,
                "period_month": 5,
                "publish_date": "2026-05-29",
                "detail_url": "https://www.njszj.cn/zjweb/MessageShow.aspx?Id=c481dc5c-314e-4edb-a259-d85e0cb2954f",
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_key"], actor_id, task_id))

    monkeypatch.setattr(nanjing_pdf, "list_nanjing_cost_info_issues", fake_list)
    monkeypatch.setattr(nanjing_pdf, "ingest_nanjing_cost_info_issue", fake_ingest)
    config = nanjing_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("nanjing_pdf")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="c481dc5c-314e-4edb-a259-d85e0cb2954f",
            title="南京市2026年5月建设工程材料市场信息价格",
            publish_date="2026-05-29",
            period_raw="南京市2026年5月建设工程材料市场信息价格",
            detail_url="https://www.njszj.cn/zjweb/MessageShow.aspx?Id=c481dc5c-314e-4edb-a259-d85e0cb2954f",
            attachment_urls=[],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_ningbo_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import ningbo_pdf

    calls = []

    def fake_list(client, *, parser, max_pages=1):
        calls.append(("list", client, parser["list_url"], max_pages))
        return [
            {
                "source_item_key": "9784",
                "title": "宁波造价信息2026年5月刊综合版",
                "listed_title": "2026年5月刊综合版",
                "period": "2026-05",
                "period_year": 2026,
                "period_month": 5,
                "publish_date": "2026-06-01",
                "detail_url": "https://www.nbzj.net/Book/ElectronicJournalView.aspx?ContentId=9784",
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_key"], actor_id, task_id))

    monkeypatch.setattr(ningbo_pdf, "list_ningbo_cost_info_issues", fake_list)
    monkeypatch.setattr(ningbo_pdf, "ingest_ningbo_cost_info_issue", fake_ingest)
    config = ningbo_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("ningbo_pdf")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="9784",
            title="宁波造价信息2026年5月刊综合版",
            publish_date="2026-06-01",
            period_raw="宁波造价信息2026年5月刊综合版",
            detail_url="https://www.nbzj.net/Book/ElectronicJournalView.aspx?ContentId=9784",
            attachment_urls=[],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_hefei_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import hefei_pdf

    calls = []
    attachment_url = "http://112.124.14.202:8009/updatePDF/2026/6/5/93ab25e7-f82c-44b6-bcb9-aefdd7fc6019.pdf"

    def fake_list(client, *, parser, years=None, max_pages_per_year=None):
        calls.append(("list", client, parser["list_url"], years, max_pages_per_year))
        return [
            {
                "source_item_key": "updatePDF/2026/6/5/93ab25e7-f82c-44b6-bcb9-aefdd7fc6019.pdf",
                "title": "2026年6月合肥建设工程市场价格信息",
                "listed_title": "2026年6月刊",
                "period": "2026-06",
                "period_year": 2026,
                "period_month": 6,
                "issue_type": "正刊",
                "publish_date": None,
                "detail_url": "http://112.124.14.202:8009/PeriodicalPublish/index.aspx",
                "attachments": [{"url": attachment_url}],
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_key"], actor_id, task_id))

    monkeypatch.setattr(hefei_pdf, "list_hefei_cost_info_issues", fake_list)
    monkeypatch.setattr(hefei_pdf, "ingest_hefei_cost_info_issue", fake_ingest)
    config = hefei_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("hefei_pdf")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="updatePDF/2026/6/5/93ab25e7-f82c-44b6-bcb9-aefdd7fc6019.pdf",
            title="2026年6月合肥建设工程市场价格信息",
            publish_date=None,
            period_raw="2026年6月合肥建设工程市场价格信息",
            detail_url="http://112.124.14.202:8009/PeriodicalPublish/index.aspx",
            attachment_urls=[attachment_url],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_jinan_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import jinan_pdf

    calls = []
    attachment_url = "http://jnxxj.jngczjxh.com:5020/cj/all/50485054485450532288000/510e01fd13d6403f9bc750465c81bbc4.pdf"

    def fake_list(client, *, parser, max_periods=5):
        calls.append(("list", client, parser["entry_url"], max_periods))
        return [
            {
                "source_item_key": "819376379319493",
                "title": "2026年05月济南工程造价信息",
                "source_title": "2026年第5期济南工程造价信息",
                "period": "2026-05",
                "period_year": 2026,
                "period_month": 5,
                "period_api_id": 811589252355269,
                "publish_date": "2026-06-25",
                "detail_url": "http://jnxxj.jngczjxh.com:5020/cj/material-wave",
                "attachments": [{"url": attachment_url}],
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_key"], actor_id, task_id))

    monkeypatch.setattr(jinan_pdf, "list_jinan_cost_info_issues", fake_list)
    monkeypatch.setattr(jinan_pdf, "ingest_jinan_cost_info_issue", fake_ingest)
    config = jinan_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("jinan_pdf")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="819376379319493",
            title="2026年05月济南工程造价信息",
            publish_date="2026-06-25",
            period_raw="2026年05月济南工程造价信息",
            detail_url="http://jnxxj.jngczjxh.com:5020/cj/material-wave",
            attachment_urls=[attachment_url],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_jiuquan_pdf_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import jiuquan_pdf

    calls = []
    attachment_url = (
        "https://zjj.jiuquan.gov.cn/zjj/c106845/202604/"
        "7c0f7f036f8347a0bbfae8649cede720/files/"
        "关于发布酒泉市2026年第二期建设工程材料信息价格及人工信息价的通知.pdf"
    )

    def fake_list(client, *, parser, max_items=5):
        calls.append(("list", client, parser["list_url"], max_items))
        return [
            {
                "source_item_key": "7c0f7f036f8347a0bbfae8649cede720",
                "title": "酒泉市2026年第2期建设工程人工市场信息价及材料信息价格",
                "source_title": "酒泉市住房和城乡建设局关于发布酒泉市2026年第二期建设工程人工市场信息价及材料信息价格的通知",
                "period": "2026年第2期",
                "period_year": 2026,
                "period_issue_no": 2,
                "publish_date": "2026-04-30",
                "detail_url": "https://zjj.jiuquan.gov.cn/zjj/c106845/202604/7c0f7f036f8347a0bbfae8649cede720.shtml",
                "attachments": [{"url": attachment_url}],
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_key"], actor_id, task_id))

    monkeypatch.setattr(jiuquan_pdf, "list_jiuquan_cost_info_issues", fake_list)
    monkeypatch.setattr(jiuquan_pdf, "ingest_jiuquan_cost_info_issue", fake_ingest)
    config = jiuquan_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("jiuquan_pdf")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="7c0f7f036f8347a0bbfae8649cede720",
            title="酒泉市2026年第2期建设工程人工市场信息价及材料信息价格",
            publish_date="2026-04-30",
            period_raw="酒泉市2026年第2期建设工程人工市场信息价及材料信息价格",
            detail_url="https://zjj.jiuquan.gov.cn/zjj/c106845/202604/7c0f7f036f8347a0bbfae8649cede720.shtml",
            attachment_urls=[attachment_url],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_linxia_xlsx_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import linxia_xlsx

    calls = []
    attachment_url = (
        "https://www.linxia.gov.cn/api-gateway/jpaas-web-server/front/document/download?"
        "fileUrl=encoded&fileName=%E4%B8%B4%E5%A4%8F%E5%B7%9E2026%E5%B9%B43%7E4%E6%9C%88.xlsx"
    )

    def fake_list(client, *, parser, max_items=5):
        calls.append(("list", client, parser["list_api_url"], max_items))
        return [
            {
                "source_item_key": "ad11ac42d05a440ca88b05cd72968a1f",
                "title": "临夏州2026年3-4月建设工程一类材料信息价",
                "source_title": "关于发布临夏州2026年3—4月建设工程...",
                "period": "2026年3-4月",
                "period_year": 2026,
                "period_start_month": 3,
                "period_end_month": 4,
                "period_start": "2026-03",
                "period_end": "2026-04",
                "publish_date": "2026-05-20",
                "detail_url": (
                    "https://www.linxia.gov.cn/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/art/2026/"
                    "art_ad11ac42d05a440ca88b05cd72968a1f.html"
                ),
                "attachments": [{"url": attachment_url}],
            }
        ]

    def fake_ingest(session, storage, *, source_id, client, row, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["source_item_key"], actor_id, task_id))

    monkeypatch.setattr(linxia_xlsx, "list_linxia_cost_info_issues", fake_list)
    monkeypatch.setattr(linxia_xlsx, "ingest_linxia_cost_info_issue", fake_ingest)
    config = linxia_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("linxia_xlsx")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues == [
        DiscoveredIssue(
            source_item_key="ad11ac42d05a440ca88b05cd72968a1f",
            title="临夏州2026年3-4月建设工程一类材料信息价",
            publish_date="2026-05-20",
            period_raw="临夏州2026年3-4月建设工程一类材料信息价",
            detail_url=(
                "https://www.linxia.gov.cn/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/art/2026/"
                "art_ad11ac42d05a440ca88b05cd72968a1f.html"
            ),
            attachment_urls=[attachment_url],
        )
    ]
    assert calls[0][0] == "list"
    assert calls[1][-1] == task.task_id


def test_xinjiang_aspnet_ajax_adapter_discovers_and_ingests_via_existing_functions(db_session, monkeypatch):
    from app.adapters import xinjiang_aspnet_ajax

    calls = []

    class FakeIssueList:
        total = 1
        rows = [
            {
                "ID": "6788",
                "Name": "乌鲁木齐市2026年4月份建设工程综合价格信息",
                "ReleaseDate": "/Date(1780358400000)/",
            }
        ]

    def fake_list(client, *, parser, page=1, page_size=20):
        calls.append(("list", client, parser["areaid"], page, page_size))
        return FakeIssueList()

    def fake_ingest(session, storage, *, source_id, client, row, parser, actor_id, task_id=None):
        calls.append(("ingest", source_id, row["ID"], actor_id, task_id))

    monkeypatch.setattr(xinjiang_aspnet_ajax, "list_xinjiang_area_issues", fake_list)
    monkeypatch.setattr(xinjiang_aspnet_ajax, "ingest_xinjiang_area_issue", fake_ingest)
    config = xinjiang_urumqi_cost_info_source_config()
    source = source_with_config(db_session, config)
    task = task_for_source(db_session, source)
    adapter = get_adapter("aspnet_ajax_area")

    issues = adapter.discover(source, task, FakeClient())
    adapter.ingest(source, task, issues[0], FakeObjectStore())

    assert issues[0].source_item_key == "6788"
    assert issues[0].title == "乌鲁木齐市2026年4月份建设工程综合价格信息"
    assert issues[0].publish_date == "2026-06-02"
    assert issues[0].detail_url.endswith("/Home/PoliciesDetail/6788")
    assert calls[0][-1] == 20
    assert calls[1][-1] == task.task_id
