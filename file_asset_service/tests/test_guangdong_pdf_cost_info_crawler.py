from app.collection import create_data_source
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing
from app.guangdong_pdf_cost_info import (
    CHAOZHOU_PARSER_VERSION,
    DONGGUAN_PARSER_VERSION,
    FOSHAN_PARSER_VERSION,
    GUANGDONG_PDF_CITY_SOURCE_CONFIGS,
    SHANTOU_PARSER_VERSION,
    YANGJIANG_PARSER_VERSION,
    YUNFU_PARSER_VERSION,
    ZHUHAI_PARSER_VERSION,
    chaozhou_cost_info_source_config,
    dongguan_cost_info_source_config,
    foshan_cost_info_source_config,
    guangzhou_cost_info_source_config,
    shantou_cost_info_source_config,
    yangjiang_cost_info_source_config,
    yunfu_cost_info_source_config,
    zhuhai_cost_info_source_config,
    zhongshan_cost_info_source_config,
)
from app.sichuan_pdf_cost_info import ingest_sichuan_pdf_issue, list_sichuan_pdf_issues
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeGuangdongPdfClient:
    def __init__(self, *, texts=None, bytes_by_url=None):
        self.texts = texts or {}
        self.bytes_by_url = bytes_by_url or {}
        self.downloaded = []

    def get_text(self, url):
        if url in self.texts:
            return self.texts[url]
        raise AssertionError(f"unexpected text fetch: {url}")

    def post_form_json(self, url, data):
        raise AssertionError(f"unexpected ajax fetch: {url}")

    def post_json(self, url, payload):
        raise AssertionError(f"unexpected json fetch: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF guangdong original bytes, not parsed", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_guangdong_pdf_source_configs_are_city_level_file_sources():
    expected = {
        "guangzhou": ("440100", "广州市住房和城乡建设局"),
        "dongguan": ("441900", "东莞市住房和城乡建设局"),
        "zhongshan": ("442000", "中山市住房和城乡建设局"),
        "shantou": ("440500", "汕头市住房和城乡建设局"),
        "yunfu": ("445300", "云浮市住房和城乡建设局"),
        "foshan": ("440600", "佛山市建设工程造价服务中心"),
        "chaozhou": ("445100", "潮州市人民政府"),
        "yangjiang": ("441700", "阳江市住房和城乡建设局"),
        "zhuhai": ("440400", "珠海市工程造价信息化平台"),
    }
    configs = {
        "guangzhou": guangzhou_cost_info_source_config(),
        "dongguan": dongguan_cost_info_source_config(),
        "zhongshan": zhongshan_cost_info_source_config(),
        "shantou": shantou_cost_info_source_config(),
        "yunfu": yunfu_cost_info_source_config(),
        "foshan": foshan_cost_info_source_config(),
        "chaozhou": chaozhou_cost_info_source_config(),
        "yangjiang": yangjiang_cost_info_source_config(),
        "zhuhai": zhuhai_cost_info_source_config(),
    }

    assert sorted(GUANGDONG_PDF_CITY_SOURCE_CONFIGS) == sorted(expected)
    for city_key, config in configs.items():
        region_code, publisher = expected[city_key]
        parser_version = config["parser"]["active_parser_version"]
        parser = config["parser"]["parsers"][parser_version]
        assert config["stable"]["site_id"] == f"cost_info.gd.{city_key}"
        assert config["stable"]["region_code"] == region_code
        assert config["stable"]["coverage_region_code"] == region_code
        assert config["stable"]["publisher_scope"] == "city"
        assert config["stable"]["publisher"] == publisher
        assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
        assert config["stable"]["period_kind"] == "monthly"
        assert config["price_coordinates"]["price_source_type"] == "info_price"
        assert config["price_coordinates"]["price_kind"] == "guidance"
        assert config["source_shape"]["source_attachment_mode"] == "pdf_only"
        assert config["source_shape"]["parsability"] == "text_pdf"
        assert parser["scope"] == "list_detail_multi_attachment_probe"
        assert parser["attachments"]["allowed"] == ["pdf"]
        assert config["ops"]["queue"]["max_concurrent_per_host"] == 1
        assert config["ops"]["queue"]["min_delay_seconds"] >= 5


def test_dongguan_static_li_detail_multi_pdf_ingests_without_processing(db_session):
    list_url = "https://zjj.dg.gov.cn/zjj/ztzl/zjxx/"
    detail_url = "http://zjj.dg.gov.cn/zjj/ztzl/zjxx/cjxx/content/post_4547851.html"
    pdf_url = "http://zjj.dg.gov.cn/attachment/0/416/416914/4547851.pdf"
    trend_pdf_url = "http://zjj.dg.gov.cn/attachment/0/416/416915/4547851.pdf"
    client = FakeGuangdongPdfClient(
        texts={
            list_url: f"""
            <li>
              <a href="{detail_url}" title="2026年5月东莞建设工程造价信息">2026年5月东莞建设工程造价信息</a>
              <span>2026-06-09</span>
            </li>
            """,
            detail_url: f"""
            <p>
              <a href="{pdf_url}" alt="2026年5月东莞建设工程造价信息.pdf">2026年5月东莞建设工程造价信息.pdf</a>
              <a href="{trend_pdf_url}" alt="东莞市建设工程常用建筑材料税前综合价年度涨跌趋势图(1).pdf">趋势图.pdf</a>
                </p>
            """,
        },
        bytes_by_url={
            pdf_url: b"%PDF dongguan monthly original bytes",
            trend_pdf_url: b"%PDF dongguan trend original bytes",
        },
    )
    config = dongguan_cost_info_source_config()
    parser = config["parser"]["parsers"][DONGGUAN_PARSER_VERSION]
    source = _create_source(db_session, config=config, name="东莞市住房和城乡建设局-造价信息", region_code="441900")

    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="guangdong-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = _archive_files(db_session, archive.archive_id)
    assert stored.business_key == f"cost_info:{source.source_id}:441900:2026-05:2026年5月东莞建设工程造价信息"
    assert stored.price_kind == "guidance"
    assert stored.period_kind == "monthly"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "441900"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_only"
    assert cell_value(stored.metadata_payload["parsability"]) == "text_pdf"
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["a"]
    assert [asset.file_ext for _, asset in files] == [".pdf", ".pdf"]
    assert client.downloaded == [pdf_url, trend_pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_yunfu_table_list_detail_pdf_reuses_shared_probe_without_processing(db_session):
    list_url = "https://www.yunfu.gov.cn/zjj/gsgg/zjgl/"
    detail_url = "https://www.yunfu.gov.cn/zjj/gsgg/zjgl/content/post_2020043.html"
    pdf_url = "https://www.yunfu.gov.cn/attachment/0/131/131339/2020043.pdf"
    zip_url = "https://www.yunfu.gov.cn/attachment/0/131/131340/2020043.zip"
    client = FakeGuangdongPdfClient(
        texts={
            list_url: f"""
            <table>
              <tr>
                <th>2026-06-22</th>
                <td><div class="list_td"><a href="{detail_url}" target="_blank">关于发布云浮市2026年5月建设工程材料参考价格的通知</a></div></td>
              </tr>
            </table>
            """,
            detail_url: f"""
            <p>
              附件：<a class="nfw-cms-attachment" href="{pdf_url}" alt="云浮市2026年05月份建设工程材料参考价格.pdf">点击下载</a>
              <a class="nfw-cms-attachment" href="{zip_url}" alt="各县（市、区）2026年5月建设工程材料参考价格.zip">点击下载</a>
            </p>
            """,
        },
    )
    config = yunfu_cost_info_source_config()
    parser = config["parser"]["parsers"][YUNFU_PARSER_VERSION]
    source = _create_source(db_session, config=config, name="云浮市住房和城乡建设局-造价信息", region_code="445300")

    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="guangdong-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = _archive_files(db_session, archive.archive_id)
    assert stored.business_key == f"cost_info:{source.source_id}:445300:2026-05:关于发布云浮市2026年5月建设工程材料参考价格的通知"
    assert stored.price_kind == "guidance"
    assert stored.period_kind == "monthly"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert [asset.file_ext for _, asset in files] == [".pdf"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_shantou_direct_pdf_card_list_reuses_shared_parser_without_detail_fetch(db_session):
    list_url = "https://www.shantou.gov.cn/zjj/ztzl/zjxx/"
    pdf_url = "https://www.shantou.gov.cn/attachment/0/155/155859/2545933.pdf"
    client = FakeGuangdongPdfClient(
        texts={
            list_url: f"""
            <div class="list_div mar-top2">
              <div class="list-right_title fon_1">
                <a href="{pdf_url}" target="_blank">汕头市中心城区(北区) 2026年5月份部分建筑材料综合价格表</a>
              </div>
              <table><tr><td>发布时间：2026-06-17</td><td>造价信息</td></tr></table>
            </div>
            """,
        },
    )
    config = shantou_cost_info_source_config()
    parser = config["parser"]["parsers"][SHANTOU_PARSER_VERSION]
    source = _create_source(db_session, config=config, name="汕头市住房和城乡建设局-造价信息", region_code="440500")

    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="guangdong-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = _archive_files(db_session, archive.archive_id)
    assert rows[0]["attachments"][0]["discovery_method"] == "list_direct"
    assert stored.business_key == f"cost_info:{source.source_id}:440500:2026-05:汕头市中心城区 北区 2026年5月份部分建筑材料综合价格表"
    assert stored.price_kind == "guidance"
    assert stored.period_kind == "monthly"
    assert [asset.file_ext for _, asset in files] == [".pdf"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_foshan_static_column_is_pure_config_multi_pdf(db_session):
    list_url = "http://fszj.foshan.gov.cn/ywxt/jsgczjfwzx/zwzt_1110045/jjyjgl/jgxx/zhjg/index.html"
    detail_url = (
        "http://fszj.foshan.gov.cn/ywxt/jsgczjfwzx/zwzt_1110045/"
        "jjyjgl/jgxx/zhjg/content/post_7176738.html"
    )
    notice_pdf_url = "http://fszj.foshan.gov.cn/attachment/0/618/618186/7176738.pdf"
    attachment_pdf_url = "http://fszj.foshan.gov.cn/attachment/0/618/618187/7176738.pdf"
    client = FakeGuangdongPdfClient(
        texts={
            list_url: f"""
            <li>
              <a class="news_a" href="{detail_url}" title="佛山市建设工程造价服务中心关于发布2026年5月份台班及主要建筑材料价格指数等造价信息的通知">
                佛山市建设工程造价服务中心关于发布2026年5月份台班及主要建筑材料价格指数等造价信息的通知
              </a>
              <span>2026-06-17</span>
            </li>
            """,
            detail_url: f"""
            <p>
              <a class="nfw-cms-attachment" href="{notice_pdf_url}" alt="佛山市建设工程造价服务中心关于发布2026年5月份台班及主要建筑材料价格指数等造价信息的通知.pdf">通知.pdf</a>
              <a class="nfw-cms-attachment" href="{attachment_pdf_url}" alt="附件：佛山市2026年5月主要建筑材料市场行情风险提示.pdf">附件.pdf</a>
            </p>
            """,
        },
        bytes_by_url={
            notice_pdf_url: b"%PDF foshan notice original bytes",
            attachment_pdf_url: b"%PDF foshan attachment original bytes",
        },
    )
    config = foshan_cost_info_source_config()
    parser = config["parser"]["parsers"][FOSHAN_PARSER_VERSION]
    source = _create_source(db_session, config=config, name="佛山市建设工程造价服务中心-综合价格", region_code="440600")

    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="guangdong-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = _archive_files(db_session, archive.archive_id)
    assert stored.business_key == (
        f"cost_info:{source.source_id}:440600:2026-05:"
        "佛山市建设工程造价服务中心关于发布2026年5月份台班及主要建筑材料价格指数等造价信息的通知"
    )
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "440600"
    assert [asset.file_ext for _, asset in files] == [".pdf", ".pdf"]
    assert client.downloaded == [notice_pdf_url, attachment_pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_chaozhou_static_column_is_pure_config_pdf_attachment(db_session):
    list_url = "https://www.chaozhou.gov.cn/zwgk/zdlyxxgk/ggzypz/gcjs/"
    detail_url = "https://www.chaozhou.gov.cn/zwgk/zdlyxxgk/ggzypz/gcjs/content/post_3995464.html"
    pdf_url = "https://www.chaozhou.gov.cn/attachment/0/569/569425/3995464.pdf"
    client = FakeGuangdongPdfClient(
        texts={
            list_url: f"""
            <li>
              <a target="_blank" href="{detail_url}" title="潮州市区建设工程2026年5月部分材料综合价">
                <p class="over1"><s></s><span>潮州市区建设工程2026年5月部分材料综合价</span></p>
                <b>2026-06-15</b>
              </a>
            </li>
            """,
            detail_url: f"""
            <div class="m-attachment">
              <a class="pdf" href="{pdf_url}" target="_blank">潮州市区建设工程2026年5月部分材料综合价.pdf</a>
            </div>
            """,
        },
    )
    config = chaozhou_cost_info_source_config()
    parser = config["parser"]["parsers"][CHAOZHOU_PARSER_VERSION]
    source = _create_source(db_session, config=config, name="潮州市工程建设项目招投标领域-材料综合价", region_code="445100")

    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="guangdong-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = _archive_files(db_session, archive.archive_id)
    assert stored.business_key == f"cost_info:{source.source_id}:445100:2026-05:潮州市区建设工程2026年5月部分材料综合价"
    assert [asset.file_ext for _, asset in files] == [".pdf"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_yangjiang_gd_gkmlpt_api_is_shared_list_strategy(db_session):
    list_url = "http://www.yangjiang.gov.cn/yjzjj/gkmlpt/index#721"
    api_url = "http://www.yangjiang.gov.cn/yjzjj/gkmlpt/api/all/721?page=1&sid=662021"
    detail_url = "http://www.yangjiang.gov.cn/yjzjj/gkmlpt/content/0/963/post_963149.html"
    pdf_url = "http://www.yangjiang.gov.cn/yjzjj/attachment/0/88/88389/963149.pdf"
    client = FakeGuangdongPdfClient(
        texts={
            api_url: """
            {
              "total": 145,
              "articles": [
                {
                  "id": 963149,
                  "title": "2026年5月份阳江市工程造价信息",
                  "url": "http://www.yangjiang.gov.cn/yjzjj/gkmlpt/content/0/963/post_963149.html",
                  "created_at": "2026-06-18 16:25:04",
                  "create_time": 1781771108
                }
              ]
            }
            """,
            detail_url: f"""
            <p><a class="nfw-cms-attachment" href="{pdf_url}" alt="2026年5月份阳江市工程造价信息.pdf">2026年5月份阳江市工程造价信息.pdf</a></p>
            """,
        },
    )
    config = yangjiang_cost_info_source_config()
    parser = config["parser"]["parsers"][YANGJIANG_PARSER_VERSION]
    source = _create_source(db_session, config=config, name="阳江市住房和城乡建设局-工程造价信息", region_code="441700")

    assert parser["list_strategy"] == "gd_gkmlpt_api"
    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="guangdong-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = _archive_files(db_session, archive.archive_id)
    assert rows[0]["detail_url"] == detail_url
    assert rows[0]["publish_date"] == "2026-06-18"
    assert stored.business_key == f"cost_info:{source.source_id}:441700:2026-05:2026年5月份阳江市工程造价信息"
    assert [asset.file_ext for _, asset in files] == [".pdf"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_zhuhai_spa_json_file_api_is_shared_file_source(db_session):
    api_url = "https://zhjg.zhszjj.com:7528/b/info/front/infopriceList"
    encoded_file = (
        "eyJjb250ZW50VHlwZSI6ImFwcGxpY2F0aW9uL3BkZiIsImZpbGVOYW1lIjoi5YWz5LqO5Y-R5biDMjAyNuW5tDXmnIjnj6Dmtbflt6XnqIvpgKDku7fkv6Hmga_nmoTpgJrnn6UucGRmIiw"
        "iZmlsZVNpemUiOiIyIE1CIiwiaWQiOiIwNDZlMjVhMmExYjM0OTM5OTg3MmFjYzVkOWJiZjYzYyIsInNhdmVQYXRoIjoiL0ZJTEUvMjAyNjA2MTEvMDQ2ZTI1YTJhMWIzNDkzOTk4NzJhY2M1ZDliYmY2M2MucGRmIiw"
        "idGltZSI6IjIwMjYtMDYtMTEgMDk6MjI6MTgiLCJ0eXBlIjoiRklMRSIsInVzZXJOYW1lIjoi5ZCO5Y-w6L-Q57u0In0"
    )
    download_url = f"https://zhjg.zhszjj.com:7528/download/{encoded_file}"

    class FakeZhuhaiClient(FakeGuangdongPdfClient):
        def post_json(self, url, payload):
            assert url == api_url
            assert payload["pageNo"] == 1
            assert payload["pageSize"] == 50
            return {
                "data": [
                    {
                        "id": "8f1e87312dd8428eb5bc9b8de4be5a88",
                        "name": "关于发布2026年5月珠海工程造价信息的通知",
                        "publishDate": "2026-06-11 00:00:00",
                        "year": 2026,
                        "month": 5,
                        "file": encoded_file,
                    }
                ]
            }

    client = FakeZhuhaiClient(bytes_by_url={download_url: b"%PDF zhuhai original bytes"})
    config = zhuhai_cost_info_source_config()
    parser = config["parser"]["parsers"][ZHUHAI_PARSER_VERSION]
    source = _create_source(db_session, config=config, name="珠海市工程造价信息化平台-材价信息", region_code="440400")

    assert parser["list_strategy"] == "spa_json_file_api"
    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="guangdong-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = _archive_files(db_session, archive.archive_id)
    assert rows[0]["detail_url"] == "https://zhjg.zhszjj.com:7528/#/gdzj-second/gdzj-price-info"
    assert rows[0]["attachments"][0]["url"] == download_url
    assert stored.business_key == f"cost_info:{source.source_id}:440400:2026-05:关于发布2026年5月珠海工程造价信息的通知"
    assert stored.price_kind == "guidance"
    assert stored.period_kind == "monthly"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "440400"
    assert [asset.file_ext for _, asset in files] == [".pdf"]
    assert client.downloaded == [download_url]
    assert db_session.query(FileProcessing).count() == 0


def _create_source(db_session, *, config: dict, name: str, region_code: str):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name=name,
        data_domain="cost_info",
        region_code=region_code,
        config=config,
    )


def _archive_files(db_session, archive_id):
    return (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive_id)
        .order_by(ArchiveFile.sort_order)
        .all()
    )
