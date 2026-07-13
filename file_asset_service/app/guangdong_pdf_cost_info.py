from __future__ import annotations

from app.sichuan_pdf_cost_info import SichuanPdfCitySource, _source_config as _sichuan_pdf_source_config

GUANGZHOU_PARSER_VERSION = "guangdong.guangzhou-pdf-list.v1"
DONGGUAN_PARSER_VERSION = "guangdong.dongguan-pdf-list.v1"
ZHONGSHAN_PARSER_VERSION = "guangdong.zhongshan-pdf-list.v1"
SHANTOU_PARSER_VERSION = "guangdong.shantou-pdf-list.v1"
YUNFU_PARSER_VERSION = "guangdong.yunfu-pdf-list.v1"
FOSHAN_PARSER_VERSION = "guangdong.foshan-pdf-list.v1"
CHAOZHOU_PARSER_VERSION = "guangdong.chaozhou-pdf-list.v1"
YANGJIANG_PARSER_VERSION = "guangdong.yangjiang-gkmlpt-pdf-list.v1"
ZHUHAI_PARSER_VERSION = "guangdong.zhuhai-spa-json-file-api.v1"


GUANGDONG_PDF_CITY_SOURCE_DEFS: dict[str, SichuanPdfCitySource] = {
    "guangzhou": SichuanPdfCitySource(
        key="guangzhou",
        province_key="gd",
        city_name="广州市",
        region_code="440100",
        publisher="广州市住房和城乡建设局",
        entry_url="https://zfcj.gz.gov.cn/zwgk/zsdwxxgkzl/jsgczjglz/jgxx/",
        parser_version=GUANGZHOU_PARSER_VERSION,
        title_keywords=("人材机价格信息",),
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
    ),
    "dongguan": SichuanPdfCitySource(
        key="dongguan",
        province_key="gd",
        city_name="东莞市",
        region_code="441900",
        publisher="东莞市住房和城乡建设局",
        entry_url="https://zjj.dg.gov.cn/zjj/ztzl/zjxx/",
        parser_version=DONGGUAN_PARSER_VERSION,
        title_keywords=("建设工程造价信息",),
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
    ),
    "zhongshan": SichuanPdfCitySource(
        key="zhongshan",
        province_key="gd",
        city_name="中山市",
        region_code="442000",
        publisher="中山市住房和城乡建设局",
        entry_url="https://jsj.zs.gov.cn/zwgk/tjsj/index.html",
        parser_version=ZHONGSHAN_PARSER_VERSION,
        title_keywords=("建设工程材料综合价",),
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
    ),
    "shantou": SichuanPdfCitySource(
        key="shantou",
        province_key="gd",
        city_name="汕头市",
        region_code="440500",
        publisher="汕头市住房和城乡建设局",
        entry_url="https://www.shantou.gov.cn/zjj/ztzl/zjxx/",
        parser_version=SHANTOU_PARSER_VERSION,
        title_keywords=("部分建筑材料综合价格",),
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
    ),
    "yunfu": SichuanPdfCitySource(
        key="yunfu",
        province_key="gd",
        city_name="云浮市",
        region_code="445300",
        publisher="云浮市住房和城乡建设局",
        entry_url="https://www.yunfu.gov.cn/zjj/gsgg/zjgl/",
        parser_version=YUNFU_PARSER_VERSION,
        title_keywords=("建设工程材料参考价格",),
        exclude_keywords=("季度", "税率", "造价管理"),
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
    ),
    "foshan": SichuanPdfCitySource(
        key="foshan",
        province_key="gd",
        city_name="佛山市",
        region_code="440600",
        publisher="佛山市建设工程造价服务中心",
        entry_url="http://fszj.foshan.gov.cn/ywxt/jsgczjfwzx/zwzt_1110045/jjyjgl/jgxx/zhjg/index.html",
        parser_version=FOSHAN_PARSER_VERSION,
        title_keywords=("主要建筑材料价格指数", "造价信息"),
        exclude_keywords=("季度",),
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
    ),
    "chaozhou": SichuanPdfCitySource(
        key="chaozhou",
        province_key="gd",
        city_name="潮州市",
        region_code="445100",
        publisher="潮州市人民政府",
        entry_url="https://www.chaozhou.gov.cn/zwgk/zdlyxxgk/ggzypz/gcjs/",
        parser_version=CHAOZHOU_PARSER_VERSION,
        title_keywords=("部分材料综合价", "人工、材料综合价"),
        exclude_keywords=("指数",),
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
    ),
    "yangjiang": SichuanPdfCitySource(
        key="yangjiang",
        province_key="gd",
        city_name="阳江市",
        region_code="441700",
        publisher="阳江市住房和城乡建设局",
        entry_url="http://www.yangjiang.gov.cn/yjzjj/gkmlpt/index#721",
        parser_version=YANGJIANG_PARSER_VERSION,
        title_keywords=("工程造价信息",),
        list_strategy="gd_gkmlpt_api",
        ajax_url="http://www.yangjiang.gov.cn/yjzjj/gkmlpt/api/all/721?page=1&sid=662021",
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
    ),
    "zhuhai": SichuanPdfCitySource(
        key="zhuhai",
        province_key="gd",
        city_name="珠海市",
        region_code="440400",
        publisher="珠海市工程造价信息化平台",
        entry_url="https://zhjg.zhszjj.com:7528/#/gdzj-second/gdzj-price-info",
        parser_version=ZHUHAI_PARSER_VERSION,
        title_keywords=("珠海工程造价信息", "工程造价信息"),
        list_strategy="spa_json_file_api",
        ajax_url="https://zhjg.zhszjj.com:7528/b/info/front/infopriceList",
        json_payload={
            "params": {"areaId": "", "year": "", "month": ""},
            "pageNo": 1,
            "pageSize": 50,
            "orderBy": "createTime",
            "order": "DESC",
        },
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
    ),
}

GUANGDONG_PDF_CITY_SOURCE_CONFIGS = {key: source.parser_version for key, source in GUANGDONG_PDF_CITY_SOURCE_DEFS.items()}


def _guangdong_source_config(source: SichuanPdfCitySource) -> dict:
    config = _sichuan_pdf_source_config(source)
    config["stable"]["period_kind"] = "monthly"
    config["price_coordinates"]["price_kind"] = "guidance"
    return config


def guangzhou_cost_info_source_config() -> dict:
    return _guangdong_source_config(GUANGDONG_PDF_CITY_SOURCE_DEFS["guangzhou"])


def dongguan_cost_info_source_config() -> dict:
    return _guangdong_source_config(GUANGDONG_PDF_CITY_SOURCE_DEFS["dongguan"])


def zhongshan_cost_info_source_config() -> dict:
    return _guangdong_source_config(GUANGDONG_PDF_CITY_SOURCE_DEFS["zhongshan"])


def shantou_cost_info_source_config() -> dict:
    return _guangdong_source_config(GUANGDONG_PDF_CITY_SOURCE_DEFS["shantou"])


def yunfu_cost_info_source_config() -> dict:
    return _guangdong_source_config(GUANGDONG_PDF_CITY_SOURCE_DEFS["yunfu"])


def foshan_cost_info_source_config() -> dict:
    return _guangdong_source_config(GUANGDONG_PDF_CITY_SOURCE_DEFS["foshan"])


def chaozhou_cost_info_source_config() -> dict:
    return _guangdong_source_config(GUANGDONG_PDF_CITY_SOURCE_DEFS["chaozhou"])


def yangjiang_cost_info_source_config() -> dict:
    return _guangdong_source_config(GUANGDONG_PDF_CITY_SOURCE_DEFS["yangjiang"])


def zhuhai_cost_info_source_config() -> dict:
    return _guangdong_source_config(GUANGDONG_PDF_CITY_SOURCE_DEFS["zhuhai"])
