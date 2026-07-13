from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.qianxinan_cost_info import (
    QIANXINAN_COST_INFO_PARSER_VERSION,
    QIANXINAN_LIST_URL,
    ingest_qianxinan_cost_info_issue,
    list_qianxinan_cost_info_issues,
    qianxinan_cost_info_source_config,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeQianxinanCostInfoClient:
    def __init__(self, *, html_by_url, bytes_by_url=None):
        self.html_by_url = html_by_url
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url in self.html_by_url:
            return self.html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF qianxinan issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_qianxinan_source_config_marks_city_issue_based_mixed_cost_station_journal():
    config = qianxinan_cost_info_source_config()
    parser = config["parser"]["parsers"][QIANXINAN_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.gz.qianxinan.cost_station_journal"
    assert config["stable"]["region_code"] == "522300"
    assert config["stable"]["coverage_region_code"] == "522300"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "黔西南州住房和城乡建设发展中心"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "unspecified"
    assert "综合参考价" in config["price_coordinates"]["price_kind_basis"]
    assert parser["list_strategy"] == "static_html_single_page_article_list"
    assert parser["detail_strategy"] == "article_detail_pdf_anchor"
    assert parser["period"]["kind"] == "issue_based"
    assert target["region_code"] == "522300"
    assert target["region_name"] == "黔西南布依族苗族自治州"
    assert target["target_level"] == "city"


def test_qianxinan_list_parses_static_list_and_keeps_issue_periods():
    client = FakeQianxinanCostInfoClient(html_by_url={QIANXINAN_LIST_URL: _qianxinan_list_html()})
    parser = qianxinan_cost_info_source_config()["parser"]["parsers"][QIANXINAN_COST_INFO_PARSER_VERSION]

    rows = list_qianxinan_cost_info_issues(client, parser=parser, max_items=5)

    assert [row["period"] for row in rows] == [
        "2026年第5期",
        "2026年第4期",
        "2026年第3期",
        "2026年第2期",
        "2026年第1期",
    ]
    assert rows[0]["title"] == "黔西南州建设工程造价信息2026年第5期"
    assert rows[0]["source_title"] == "黔西南州建设工程造价信息2026年第5期"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_issue_no"] == 5
    assert rows[0]["publish_date"] == "2026-06-15"
    assert rows[0]["source_item_key"] == "90523456"
    assert rows[0]["detail_url"] == (
        "https://www.qxn.gov.cn/zwgk/zfjg/zzfcxjsj_5135158/bmxxgkml_5136493/"
        "zjxx_5135167/202606/t20260615_90523456.html"
    )
    assert all("2026-05" != row["period"] for row in rows)


def test_qianxinan_detail_pdf_attachment_ingests_issue_based_archive_without_processing(db_session):
    detail_url = (
        "https://www.qxn.gov.cn/zwgk/zfjg/zzfcxjsj_5135158/bmxxgkml_5136493/"
        "zjxx_5135167/202606/t20260615_90523456.html"
    )
    pdf_url = (
        "https://www.qxn.gov.cn/zwgk/zfjg/zzfcxjsj_5135158/bmxxgkml_5136493/"
        "zjxx_5135167/202606/P020260615382295081505.pdf"
    )
    client = FakeQianxinanCostInfoClient(
        html_by_url={
            QIANXINAN_LIST_URL: _qianxinan_list_html(),
            detail_url: _qianxinan_detail_html(),
        },
        bytes_by_url={pdf_url: b"%PDF qianxinan 2026 issue 5"},
    )
    config = qianxinan_cost_info_source_config()
    parser = config["parser"]["parsers"][QIANXINAN_COST_INFO_PARSER_VERSION]
    source = _create_qianxinan_source(db_session, config=config)
    row = list_qianxinan_cost_info_issues(client, parser=parser, max_items=1)[0]

    archive = ingest_qianxinan_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="qianxinan-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "90523456"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "522300"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "unspecified"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第5期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第5期"
    assert cell_value(stored.metadata_payload["period_start"]) is None
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 5
    assert "period_month" not in stored.metadata_payload
    assert cell_value(stored.metadata_payload["source_title"]) == "黔西南州建设工程造价信息2026年第5期"
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "未文档级分离" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_pdf_anchor"]
    assert client.get_text_calls == [QIANXINAN_LIST_URL, detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _qianxinan_list_html():
    return """
    <ul class="list mb5">
      <li>
        <span>[2026年06月15日]</span>
        <a TARGET="_blank" href="https://www.qxn.gov.cn/zwgk/zfjg/zzfcxjsj_5135158/bmxxgkml_5136493/zjxx_5135167/202606/t20260615_90523456.html" title="黔西南州建设工程造价信息2026年第5期">
          黔西南州建设工程造价信息2026年第5期
        </a>
      </li>
      <li>
        <span>[2026年05月14日]</span>
        <a TARGET="_blank" href="https://www.qxn.gov.cn/zwgk/zfjg/zzfcxjsj_5135158/bmxxgkml_5136493/zjxx_5135167/202605/t20260514_90173455.html" title="黔西南州建设工程造价信息2026年第4期">
          黔西南州建设工程造价信息2026年第4期
        </a>
      </li>
      <li>
        <span>[2026年04月15日]</span>
        <a TARGET="_blank" href="https://www.qxn.gov.cn/zwgk/zfjg/zzfcxjsj_5135158/bmxxgkml_5136493/zjxx_5135167/202604/t20260415_90006287.html" title="黔西南州建设工程造价信息2026年第3期">
          黔西南州建设工程造价信息2026年第3期
        </a>
      </li>
      <li>
        <span>[2026年03月16日]</span>
        <a TARGET="_blank" href="https://www.qxn.gov.cn/zwgk/zfjg/zzfcxjsj_5135158/bmxxgkml_5136493/zjxx_5135167/202603/t20260316_89873297.html" title="黔西南州建设工程造价信息2026年第2期">
          黔西南州建设工程造价信息2026年第2期
        </a>
      </li>
      <li>
        <span>[2026年02月13日]</span>
        <a TARGET="_blank" href="https://www.qxn.gov.cn/zwgk/zfjg/zzfcxjsj_5135158/bmxxgkml_5136493/zjxx_5135167/202602/t20260213_89522320.html" title="黔西南州建设工程造价信息2026年第1期">
          黔西南州建设工程造价信息2026年第1期
        </a>
      </li>
      <li>
        <span>[2026年01月14日]</span>
        <a TARGET="_blank" href="https://www.qxn.gov.cn/zwgk/zfjg/zzfcxjsj_5135158/bmxxgkml_5136493/zjxx_5135167/202601/t20260114_89298816.html" title="黔西南州建设工程造价信息2025年第12期">
          黔西南州建设工程造价信息2025年第12期
        </a>
      </li>
    </ul>
    """


def _qianxinan_detail_html():
    return """
    <html><head>
      <meta name="ArticleTitle" content="黔西南州建设工程造价信息2026年第5期">
      <meta name="ContentSource" content="黔西南州住房和城乡建设局">
      <meta name="Description" content="黔西南州建设工程造价信息2026年第5期.pdf">
    </head><body>
      <div id="NewsArticleTitle" style="display:none;">黔西南州建设工程造价信息2026年第5期</div>
      <div id="NewsArticleSource" style="display:none;">黔西南州住房和城乡建设局</div>
      <div class="trs_editor_view TRS_UEDITOR trs_paper_default">
        <p class="insertfileTag">
          <a appendix="true" data-appendix="true" needdownload="true"
             href="./P020260615382295081505.pdf"
             title="黔西南州建设工程造价信息2026年第5期.pdf"
             download="黔西南州建设工程造价信息2026年第5期.pdf"
             OLDSRC="/protect/P0202606/P020260615/P020260615382295081505.pdf">黔西南州建设工程造价信息2026年第5期.pdf</a>
        </p>
      </div>
    </body></html>
    """


def _create_qianxinan_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="黔西南州住房和城乡建设发展中心-建设工程造价信息",
        data_domain="cost_info",
        province="贵州省",
        city="黔西南布依族苗族自治州",
        region_code="522300",
        config=config,
    )
