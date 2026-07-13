from __future__ import annotations

from datetime import UTC, datetime
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collection import create_data_source
from app.models import DataSource

SICHUAN_BASE_URL = "http://202.61.90.35:8032"
SICHUAN_AREA_ENDPOINT = (
    f"{SICHUAN_BASE_URL}/handler/Pubservices.ashx?ActionName=HomeAction&MethodName=GetAreaList"
)
SICHUAN_PERIOD_ENDPOINT = (
    f"{SICHUAN_BASE_URL}/handler/Pubservices.ashx?ActionName=HomeAction&MethodName=GetAllPublishPeriodList"
)
SICHUAN_COVERAGE_PARSER_VERSION = "sceci.coverage-directory.v1"

SICHUAN_CITY_AREA_CODE_TO_REGION_CODE = {
    "川A": "510100",
    "川B": "510700",
    "川C": "510300",
    "川D": "510400",
    "川E": "510500",
    "川F": "510600",
    "川H": "510800",
    "川J": "510900",
    "川K": "511000",
    "川L": "511100",
    "川M": "512000",
    "川Q": "511500",
    "川R": "511300",
    "川S": "511700",
    "川T": "511800",
    "川U": "513200",
    "川V": "513300",
    "川W": "513400",
    "川X": "511600",
    "川Y": "511900",
    "川Z": "511400",
}

SICHUAN_MIANYANG_AREA_CODE_TO_REGION_CODE = {
    "川B-0010": "510700-市区",
    "川B-0020": "510725",
    "川B-0030": "510722",
    "川B-0040": "510723",
    "川B-0050": "510705",
    "川B-0060": "510727",
    "川B-0070": "510781",
    "川B-0080": "510726",
}

SICHUAN_CITY_SOURCE_AUDIT = {
    "510100": {
        "status": "manual_upload_required",
        "note": "成都官方信息价公开下载受限，按017走manual_upload待补；已验证人工上传通道",
    },
    "510700": {
        "status": "auto_crawl_verified",
        "url": "https://zjw.my.gov.cn/myszjj/c101133/list.shtml",
        "note": "绵阳住建委栏目存在公开.xls附件，已验证爬虫入湖；覆盖范围窄于省站",
    },
    "510300": {
        "status": "auto_crawl_verified",
        "url": "https://www.zg.gov.cn/zgsrmzf/c00145/pc/list.html",
        "note": "自贡政府栏目存在公开.zip附件，已验证爬虫入湖；zip包不解压不OCR",
    },
    "510400": {
        "status": "auto_crawl_verified",
        "url": "http://zjj.panzhihua.gov.cn/ztzl/gczjxx/",
        "note": "攀枝花住建局工程造价信息栏目存在公开PDF附件；已纳入四川PDF薄adapter",
    },
    "510500": {
        "status": "auto_crawl_verified",
        "url": "https://zjj.luzhou.gov.cn/hyfw/gczjxx/",
        "note": "泸州住建局工程造价信息栏目存在公开PDF附件；已纳入四川PDF薄adapter",
    },
    "510600": {
        "status": "auto_crawl_verified",
        "url": "https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723",
        "note": "德阳政府站静态公示公告栏目可见建筑材料市场价格信息PDF附件；已替换旧搜索入口并纳入四川PDF薄adapter",
    },
    "510800": {
        "status": "source_blocked",
        "url": "https://jsj.cngy.gov.cn/Email/Detail/38021e03-c7d8-44aa-b91a-bcacbfbd8415.html",
        "note": "广元官方答复称可通过省系统或纸质版查询；未发现稳定公开文件栏目，按受阻源记录",
        "source_completeness_status": "source_blocked",
    },
    "510900": {
        "status": "auto_crawl_verified",
        "url": "https://snjsj.suining.gov.cn/xinwen/list/58b7d65c2700de24f4d639bdf982a0b1.html",
        "note": "遂宁住建局建材价格栏目存在公开PDF附件；已纳入四川PDF薄adapter",
    },
    "511100": {
        "status": "auto_crawl_verified",
        "url": "https://www.leshan.gov.cn/lsswszf/lssjzclscxxj/92337845/index.html",
        "note": "乐山市建筑材料市场信息价栏目存在公开PDF附件；已纳入四川PDF薄adapter",
    },
    "511400": {
        "status": "source_blocked",
        "url": "https://www.ms.gov.cn/info/7200/1196100.htm",
        "note": "眉山官方材料显示造价信息通过公众号等渠道公布；未发现稳定公开下载栏目，按受阻源记录",
        "source_completeness_status": "source_blocked",
    },
    "511700": {
        "status": "auto_crawl_verified",
        "url": "http://zjj.dazhou.gov.cn/news-list-zaojiaxinxi.html",
        "note": "达州住建局造价信息栏目走AJAX列表，详情页iframe挂PDF；已纳入四川PDF薄adapter",
    },
    "511800": {
        "status": "source_blocked",
        "url": "https://zfhcxjsj.yaan.gov.cn/xinwen/show/ec724c000da6951f4ff930656eae1b48.html",
        "note": "雅安官方新闻证明材料价格信息发布事实存在；未找到稳定公开下载栏目，按受阻源记录",
        "source_completeness_status": "source_blocked",
    },
    "511900": {
        "status": "online_table_declaration",
        "url": "https://zfcxjsj.cnbz.gov.cn/ztzl/bzsjzclscxxj/index.html",
        "note": "巴中官方建筑材料市场信息价为在线表/网页形态；Layer0只做覆盖声明和原站跳转",
    },
    "512000": {
        "status": "online_table_declaration",
        "url": "http://www.ziyang.gov.cn/zysrmzf/xxgkgsgg/pc/content/content_2068898430624374784.html",
        "note": "资阳官方工程造价信息为在线表/网页形态；Layer0只做覆盖声明和原站跳转",
    },
}

SICHUAN_ONLINE_TABLE_CITY_SOURCES = {
    "511900": {
        "site_id": "cost_info.sc.bazhong.online_table",
        "region_name": "巴中市",
        "publisher": "巴中市住房和城乡建设局",
        "entry_url": "https://zfcxjsj.cnbz.gov.cn/ztzl/bzsjzclscxxj/index.html",
        "source_name": "巴中市建筑材料市场信息价-在线表声明",
    },
    "512000": {
        "site_id": "cost_info.sc.ziyang.online_table",
        "region_name": "资阳市",
        "publisher": "资阳市住房和城乡建设局",
        "entry_url": "http://www.ziyang.gov.cn/zysrmzf/xxgkgsgg/pc/content/content_2068898430624374784.html",
        "source_name": "资阳市工程造价信息-在线表声明",
    },
}


def sichuan_province_cost_info_source_config(
    *,
    coverage_expectation: dict | None = None,
    city_area_id: str = "川B",
) -> dict:
    config = {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.sc.province.coverage",
            "domain_type": "cost_info",
            "region_code": "510000",
            "producer": "四川省建设工程造价总站",
            "publisher": "四川省建设工程造价总站",
            "publisher_scope": "province",
            "publisher_region_code": "510000",
            "publisher_name": "四川省建设工程造价总站",
            "publisher_type": "official_cost_info_site",
            "entry_url": f"{SICHUAN_BASE_URL}/pubpages/pricelist.aspx",
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DIRECT_WEB",
            "source_attachment_mode": "html_table",
            "parsability": "structured",
            "acquisition": "coverage_directory_only",
        },
        "parser": {
            "active_parser_version": SICHUAN_COVERAGE_PARSER_VERSION,
            "area_endpoint": SICHUAN_AREA_ENDPOINT,
            "period_endpoint": SICHUAN_PERIOD_ENDPOINT,
            "parsers": {
                SICHUAN_COVERAGE_PARSER_VERSION: {
                    "scope": "directory_metadata_only",
                    "area_endpoint": SICHUAN_AREA_ENDPOINT,
                    "period_endpoint": SICHUAN_PERIOD_ENDPOINT,
                    "city_area_id": city_area_id,
                    "copyright_boundary": "coverage_directory_only_no_price_table_mirror",
                }
            },
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier0_directory_metadata",
            "schedule": {"frequency": "monthly"},
            "gated": False,
            "alert_thresholds": {"missing_period_count": 1},
        },
    }
    if coverage_expectation is not None:
        config["coverage_expectation"] = coverage_expectation
    return config


def sichuan_online_table_source_config(
    *,
    region_code: str,
    source_def: dict[str, str],
    coverage_expectation: dict | None = None,
) -> dict:
    config = {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": source_def["site_id"],
            "domain_type": "cost_info",
            "region_code": region_code,
            "coverage_region_code": region_code,
            "producer": source_def["publisher"],
            "publisher": source_def["publisher"],
            "publisher_scope": "city",
            "publisher_region_code": region_code,
            "publisher_name": source_def["publisher"],
            "publisher_type": "official_cost_info_site",
            "entry_url": source_def["entry_url"],
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "tax_type": None,
            "coverage_region_code": region_code,
        },
        "source_shape": {
            "publication_mode": "DIRECT_WEB",
            "source_attachment_mode": "html_table",
            "parsability": "structured",
            "acquisition": "coverage_declaration_only",
        },
        "parser": {
            "active_parser_version": "manual.coverage-declaration.v1",
            "parsers": {
                "manual.coverage-declaration.v1": {
                    "scope": "coverage_declaration_only",
                    "official_url": source_def["entry_url"],
                    "copyright_boundary": "no_price_table_mirror_link_out_only",
                }
            },
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier0_declaration_only",
            "schedule": {"frequency": "monthly"},
            "gated": False,
            "alert_thresholds": {"missing_period_count": 1},
        },
    }
    if coverage_expectation is not None:
        config["coverage_expectation"] = coverage_expectation
    return config


def parse_sichuan_periods(period_payload: list[dict], *, start_period: str = "2026-01") -> list[dict[str, object]]:
    periods: list[dict[str, object]] = []
    for item in period_payload:
        period = _period_from_item(item)
        if period is None or period < start_period:
            continue
        periods.append(
            {
                "period": period,
                "period_no": item.get("PeriodNo"),
                "period_name": item.get("PeriodName"),
            }
        )
    return periods


def build_sichuan_full_coverage_expectation(
    *,
    area_payload: list[dict],
    period_payload: list[dict],
    start_period: str = "2026-01",
) -> dict[str, object]:
    periods = parse_sichuan_periods(period_payload, start_period=start_period)
    declared_periods, period_refs = _declared_period_payload(periods)
    province_node = _find_area_node(area_payload, "川")
    targets: list[dict[str, object]] = []
    for city_node in province_node.get("children", []):
        area_id = str(city_node.get("id") or "")
        region_code = SICHUAN_CITY_AREA_CODE_TO_REGION_CODE.get(area_id)
        if region_code is None:
            continue
        targets.append(
            _coverage_target(
                region_code=region_code,
                region_name=str(city_node.get("text") or ""),
                external_area_id=area_id,
                external_city_area_id=area_id,
                target_level="city",
                declared_periods=declared_periods,
                period_refs=period_refs,
                audit=_city_source_audit(region_code),
            )
        )
        if area_id == "川B":
            targets.extend(
                _mianyang_subregion_targets(
                    city_node=city_node,
                    declared_periods=declared_periods,
                    period_refs=period_refs,
                )
            )

    return {
        "mode": "coverage_declaration",
        "declaration_source": "GetAreaList/GetAllPublishPeriodList",
        "target_regions": targets,
    }


def build_sichuan_mianyang_coverage_expectation(
    *,
    area_payload: list[dict],
    period_payload: list[dict],
    start_period: str = "2026-01",
) -> dict[str, object]:
    periods = parse_sichuan_periods(period_payload, start_period=start_period)
    declared_periods, period_refs = _declared_period_payload(periods)
    city_node = _find_area_node(area_payload, "川B")
    targets = _mianyang_subregion_targets(
        city_node=city_node,
        declared_periods=declared_periods,
        period_refs=period_refs,
    )
    return {
        "mode": "coverage_declaration",
        "declaration_source": "GetAreaList/GetAllPublishPeriodList",
        "target_regions": targets,
    }


def build_sichuan_online_table_coverage_expectations(
    *,
    period_payload: list[dict],
    start_period: str = "2026-01",
) -> dict[str, dict[str, object]]:
    periods = parse_sichuan_periods(period_payload, start_period=start_period)
    declared_periods, period_refs = _declared_period_payload(periods)
    expectations: dict[str, dict[str, object]] = {}
    for region_code, source_def in SICHUAN_ONLINE_TABLE_CITY_SOURCES.items():
        audit = _city_source_audit(region_code)
        expectations[region_code] = {
            "mode": "coverage_declaration",
            "declaration_source": "official_city_online_table_link",
            "target_regions": [
                _online_table_target(
                    region_code=region_code,
                    region_name=source_def["region_name"],
                    declared_periods=declared_periods,
                    period_refs=period_refs,
                    audit=audit,
                )
            ],
        }
    return expectations


def upsert_sichuan_mianyang_coverage_source(
    session: Session,
    *,
    area_payload: list[dict],
    period_payload: list[dict],
    start_period: str = "2026-01",
    actor_id: str = "sichuan-coverage-directory",
) -> DataSource:
    coverage_expectation = build_sichuan_mianyang_coverage_expectation(
        area_payload=area_payload,
        period_payload=period_payload,
        start_period=start_period,
    )
    config = sichuan_province_cost_info_source_config(coverage_expectation=coverage_expectation, city_area_id="川B")
    return _upsert_sichuan_coverage_source(session, config=config, actor_id=actor_id)


def upsert_sichuan_full_coverage_source(
    session: Session,
    *,
    area_payload: list[dict],
    period_payload: list[dict],
    start_period: str = "2026-01",
    actor_id: str = "sichuan-coverage-directory",
) -> DataSource:
    coverage_expectation = build_sichuan_full_coverage_expectation(
        area_payload=area_payload,
        period_payload=period_payload,
        start_period=start_period,
    )
    config = sichuan_province_cost_info_source_config(coverage_expectation=coverage_expectation, city_area_id="*")
    return _upsert_sichuan_coverage_source(session, config=config, actor_id=actor_id)


def upsert_sichuan_online_table_declaration_sources(
    session: Session,
    *,
    period_payload: list[dict],
    start_period: str = "2026-01",
    actor_id: str = "sichuan-online-table-declaration",
) -> list[DataSource]:
    expectations = build_sichuan_online_table_coverage_expectations(
        period_payload=period_payload,
        start_period=start_period,
    )
    sources: list[DataSource] = []
    for region_code, source_def in SICHUAN_ONLINE_TABLE_CITY_SOURCES.items():
        config = sichuan_online_table_source_config(
            region_code=region_code,
            source_def=source_def,
            coverage_expectation=expectations[region_code],
        )
        sources.append(
            _upsert_sichuan_online_table_source(
                session,
                region_code=region_code,
                source_def=source_def,
                config=config,
                actor_id=actor_id,
            )
        )
    return sources


def _upsert_sichuan_coverage_source(
    session: Session,
    *,
    config: dict,
    actor_id: str,
) -> DataSource:
    source = session.scalar(
        select(DataSource)
        .where(DataSource.source_scope == "platform_public")
        .where(DataSource.source_type == "info_price")
        .where(DataSource.data_domain == "cost_info")
        .where(DataSource.name == "四川省工程造价信息网-覆盖目录")
    )
    if source is None:
        return create_data_source(
            session,
            source_scope="platform_public",
            tenant_code=None,
            managed_by="platform",
            source_type="info_price",
            connector_type="source_registry",
            name="四川省工程造价信息网-覆盖目录",
            data_domain="cost_info",
            base_url=SICHUAN_BASE_URL,
            url=SICHUAN_AREA_ENDPOINT,
            province="四川",
            region_code="510000",
            format="html_table",
            downloadable=False,
            bucket="覆盖目录",
            frequency="monthly",
            config=config,
            created_by=actor_id,
        )

    source.base_url = SICHUAN_BASE_URL
    source.url = SICHUAN_AREA_ENDPOINT
    source.province = "四川"
    source.region_code = "510000"
    source.format = "html_table"
    source.downloadable = False
    source.bucket = "覆盖目录"
    source.frequency = "monthly"
    source.config = config
    source.updated_at = datetime.now(UTC)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _upsert_sichuan_online_table_source(
    session: Session,
    *,
    region_code: str,
    source_def: dict[str, str],
    config: dict,
    actor_id: str,
) -> DataSource:
    source = session.scalar(
        select(DataSource)
        .where(DataSource.source_scope == "platform_public")
        .where(DataSource.source_type == "info_price")
        .where(DataSource.data_domain == "cost_info")
        .where(DataSource.name == source_def["source_name"])
    )
    if source is None:
        return create_data_source(
            session,
            source_scope="platform_public",
            tenant_code=None,
            managed_by="platform",
            source_type="info_price",
            connector_type="source_registry",
            name=source_def["source_name"],
            data_domain="cost_info",
            base_url=source_def["entry_url"],
            url=source_def["entry_url"],
            province="四川",
            city=source_def["region_name"],
            region_code=region_code,
            format="html_table",
            downloadable=False,
            bucket="覆盖声明",
            frequency="monthly",
            config=config,
            created_by=actor_id,
        )

    source.base_url = source_def["entry_url"]
    source.url = source_def["entry_url"]
    source.province = "四川"
    source.city = source_def["region_name"]
    source.region_code = region_code
    source.format = "html_table"
    source.downloadable = False
    source.bucket = "覆盖声明"
    source.frequency = "monthly"
    source.config = config
    source.updated_at = datetime.now(UTC)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _declared_period_payload(periods: list[dict[str, object]]) -> tuple[list[str], list[dict[str, object]]]:
    declared_periods = [str(item["period"]) for item in periods]
    period_refs = [
        {
            "period": item["period"],
            "period_no": item["period_no"],
            "period_name": item["period_name"],
        }
        for item in periods
    ]
    return declared_periods, period_refs


def _city_source_audit(region_code: str) -> dict[str, str]:
    return SICHUAN_CITY_SOURCE_AUDIT.get(
        region_code,
        {
            "status": "pending_source_audit",
            "note": "地市独立官方源待审计；当前业务覆盖由四川省站目录声明承载",
        },
    )


def _coverage_target(
    *,
    region_code: str,
    region_name: str,
    external_area_id: str,
    external_city_area_id: str,
    target_level: str,
    declared_periods: list[str],
    period_refs: list[dict[str, object]],
    audit: dict[str, str],
) -> dict[str, object]:
    return {
        "region_code": region_code,
        "region_name": region_name,
        "external_area_id": external_area_id,
        "external_city_area_id": external_city_area_id,
        "target_level": target_level,
        "requires_city_source": True,
        "declared_periods": declared_periods,
        "declared_period_refs": period_refs,
        "coverage_note": "四川省站GetAreaList/GetAllPublishPeriodList目录声明；不镜像价格表内容",
        "source_audit_status": audit.get("status"),
        "source_audit_note": audit.get("note"),
        "city_source_url": audit.get("url"),
        "source_completeness_status": audit.get("source_completeness_status"),
    }


def _online_table_target(
    *,
    region_code: str,
    region_name: str,
    declared_periods: list[str],
    period_refs: list[dict[str, object]],
    audit: dict[str, str],
) -> dict[str, object]:
    return {
        "region_code": region_code,
        "region_name": region_name,
        "external_area_id": None,
        "external_city_area_id": None,
        "target_level": "city",
        "requires_city_source": True,
        "declared_periods": declared_periods,
        "declared_period_refs": period_refs,
        "coverage_note": "官方在线表/网页源覆盖声明；Layer0只给原站跳转，不镜像价格表内容",
        "source_audit_status": audit.get("status"),
        "source_audit_note": audit.get("note"),
        "city_source_url": audit.get("url"),
    }


def _mianyang_subregion_targets(
    *,
    city_node: dict,
    declared_periods: list[str],
    period_refs: list[dict[str, object]],
) -> list[dict[str, object]]:
    audit = _city_source_audit("510700")
    targets: list[dict[str, object]] = []
    for child in city_node.get("children", []):
        external_area_id = str(child.get("id") or "")
        region_code = SICHUAN_MIANYANG_AREA_CODE_TO_REGION_CODE.get(external_area_id)
        if region_code is None:
            continue
        targets.append(
            _coverage_target(
                region_code=region_code,
                region_name=str(child.get("text") or ""),
                external_area_id=external_area_id,
                external_city_area_id="川B",
                target_level="subregion",
                declared_periods=declared_periods,
                period_refs=period_refs,
                audit=audit,
            )
        )
    return targets


def _period_from_item(item: dict) -> str | None:
    year = item.get("Year")
    month = item.get("CurYearNo")
    if isinstance(year, int) and isinstance(month, int):
        return f"{year:04d}-{month:02d}"
    name = str(item.get("PeriodName") or "")
    match = re.search(r"(20\d{2})年(\d{1,2})月", name)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"


def _find_area_node(nodes: list[dict], area_id: str) -> dict:
    for node in nodes:
        if node.get("id") == area_id:
            return node
        children = node.get("children")
        if isinstance(children, list):
            try:
                return _find_area_node(children, area_id)
            except ValueError:
                pass
    raise ValueError(f"SICHUAN_AREA_NOT_FOUND: {area_id}")
