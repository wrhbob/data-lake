from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collection import create_data_source
from app.guangdong_pdf_cost_info import GUANGDONG_PDF_CITY_SOURCE_DEFS, _guangdong_source_config
from app.models import DataSource


GUANGDONG_FILE_SOURCE_CITY_CODES = tuple(source.region_code for source in GUANGDONG_PDF_CITY_SOURCE_DEFS.values())

GUANGDONG_CITY_SOURCE_AUDIT = {
    "440100": {"status": "auto_crawl_verified", "note": "广州PDF栏目型源已入湖验证"},
    "440400": {"status": "spa_api_file_source", "note": "珠海SPA公开后台API可列期次，文件经/download/{encoded}下载"},
    "440500": {"status": "auto_crawl_verified", "note": "汕头PDF直链栏目已入湖验证"},
    "440600": {"status": "auto_crawl_verified", "note": "佛山PDF附件栏目已入湖验证"},
    "441700": {"status": "auto_crawl_verified", "note": "阳江gkmlpt平台API已入湖验证"},
    "441900": {"status": "auto_crawl_verified", "note": "东莞PDF栏目型源已入湖验证"},
    "442000": {"status": "auto_crawl_verified", "note": "中山PDF栏目型源已入湖验证"},
    "445100": {"status": "auto_crawl_verified", "note": "潮州PDF栏目型源已入湖验证"},
    "445300": {"status": "auto_crawl_verified", "note": "云浮PDF栏目型源已入湖验证"},
    "440200": {"status": "online_table_declaration", "note": "韶关实测为造价信息网页/系统形态，Layer0只声明和跳转"},
    "440300": {"status": "online_table_declaration", "note": "深圳造价信息查询系统为网页/系统形态，Layer0只声明和跳转"},
    "440700": {"status": "online_table_declaration", "note": "江门市场参考价为网页表格形态，Layer0只声明和跳转"},
    "440900": {"status": "online_table_declaration", "note": "茂名台账标表格源，Layer0只声明和跳转"},
    "441200": {"status": "online_table_declaration", "note": "肇庆命中gkmlpt平台但台账分类号不是可采PDF栏目，先声明和跳转"},
    "441300": {"status": "online_table_declaration", "note": "惠州部分建筑材料综合价为网页形态，Layer0只声明和跳转"},
    "441400": {"status": "online_table_declaration", "note": "梅州台账源当前按网页/声明处理，待后续补文件栏目"},
    "441500": {"status": "online_table_declaration", "note": "汕尾台账源当前按网页/声明处理，待后续补文件栏目"},
    "441600": {"status": "online_table_declaration", "note": "河源政府信息公开平台当前按声明源处理"},
    "441800": {"status": "online_table_declaration", "note": "清远台账源当前按网页/声明处理，待后续补文件栏目"},
    "440800": {
        "status": "source_blocked",
        "note": "湛江可见价格信息列表但无可下载文件，台账标无法下载；待人工补录",
        "source_completeness_status": "source_blocked",
    },
    "445200": {
        "status": "source_blocked",
        "note": "揭阳台账标网站进不去，多源探查未确认公开可下载文件；待人工补录",
        "source_completeness_status": "source_blocked",
    },
}

GUANGDONG_DECLARATION_CITY_SOURCES = {
    "440200": {
        "site_id": "cost_info.gd.shaoguan.declaration",
        "region_name": "韶关市",
        "publisher": "韶关市建设工程造价信息化管理系统",
        "entry_url": "https://gczj.zgj.sg.gov.cn/list-cffd6dde62d54fea8fde23f7c9c8838e.html?secondCategoryId=42e421c3c9064529a",
        "source_name": "韶关市造价信息-网页声明",
    },
    "440300": {
        "site_id": "cost_info.gd.shenzhen.declaration",
        "region_name": "深圳市",
        "publisher": "深圳市住房和建设局",
        "entry_url": "https://zjj.sz.gov.cn/szzjxx/web/pc/index",
        "source_name": "深圳市造价信息查询系统-网页声明",
    },
    "440700": {
        "site_id": "cost_info.gd.jiangmen.declaration",
        "region_name": "江门市",
        "publisher": "江门工程造价信息网",
        "entry_url": "https://www.jmgczj.com/views/list-price.html?mid=42",
        "source_name": "江门市市场参考价-网页声明",
    },
    "440900": {
        "site_id": "cost_info.gd.maoming.declaration",
        "region_name": "茂名市",
        "publisher": "茂名市住房和城乡建设局",
        "entry_url": "http://jianshe.maoming.gov.cn/ztzl/jsgczjckjzbhdtjgclckjgb/index.html",
        "source_name": "茂名市材料参考价格-表格声明",
    },
    "441200": {
        "site_id": "cost_info.gd.zhaoqing.declaration",
        "region_name": "肇庆市",
        "publisher": "肇庆市住房和城乡建设局",
        "entry_url": "https://www.zhaoqing.gov.cn/zqzjj/gkmlpt/index#20853",
        "source_name": "肇庆市造价管理-gkmlpt声明",
    },
    "441300": {
        "site_id": "cost_info.gd.huizhou.declaration",
        "region_name": "惠州市",
        "publisher": "惠州市建设工程造价信息网",
        "entry_url": "http://www.gdhzcost.com.cn/hyzl-34-1.html",
        "source_name": "惠州市部分建筑材料综合价-网页声明",
    },
    "441400": {
        "site_id": "cost_info.gd.meizhou.declaration",
        "region_name": "梅州市",
        "publisher": "梅州市住房和城乡建设局",
        "entry_url": "https://www.meizhou.gov.cn/zwgk/zfjg/szfhcxjsj/gsgg/",
        "source_name": "梅州市住房城乡建设局公示公告-声明",
    },
    "441500": {
        "site_id": "cost_info.gd.shanwei.declaration",
        "region_name": "汕尾市",
        "publisher": "汕尾市住房和城乡建设局",
        "entry_url": "https://www.shanwei.gov.cn/swzfsj/zhuanti/gczj/",
        "source_name": "汕尾市工程造价专题-声明",
    },
    "441600": {
        "site_id": "cost_info.gd.heyuan.declaration",
        "region_name": "河源市",
        "publisher": "河源市住房和城乡建设局",
        "entry_url": "https://www.heyuan.gov.cn/hyszjj/gkmlpt/index",
        "source_name": "河源市住房城乡建设局政府信息公开-声明",
    },
    "441800": {
        "site_id": "cost_info.gd.qingyuan.declaration",
        "region_name": "清远市",
        "publisher": "清远市住房和城乡建设局",
        "entry_url": "https://www.qyggzy.cn/xxgk/zzjg/zfjg/qyszjj/zwgk/tzgg/",
        "source_name": "清远市住房城乡建设局通知公告-声明",
    },
}

GUANGDONG_BLOCKED_CITY_SOURCES = {
    "440800": {
        "site_id": "cost_info.gd.zhanjiang.blocked",
        "region_name": "湛江市",
        "publisher": "湛江工程造价信息网",
        "entry_url": "https://www.zjcost.net/Channel.aspx?id=14&&&&area=&&&&year=2026&&&&month=01&&&",
        "source_name": "湛江市价格信息-受阻源",
    },
    "445200": {
        "site_id": "cost_info.gd.jieyang.blocked",
        "region_name": "揭阳市",
        "publisher": "揭阳市工程造价信息网",
        "entry_url": "http://gczj.jyszzj.com/Channel.aspx?id=1",
        "source_name": "揭阳市工程造价信息-受阻源",
    },
}


def upsert_guangdong_demo_coverage_sources(
    session: Session,
    *,
    declared_periods: list[str],
    actor_id: str = "guangdong-demo-coverage",
) -> dict[str, list[DataSource]]:
    return {
        "file_sources": _upsert_file_sources(session, actor_id=actor_id),
        "declaration_sources": _upsert_declaration_sources(session, declared_periods=declared_periods, actor_id=actor_id),
        "blocked_sources": _upsert_blocked_sources(session, actor_id=actor_id),
    }


def _upsert_file_sources(session: Session, *, actor_id: str) -> list[DataSource]:
    sources: list[DataSource] = []
    for source_def in GUANGDONG_PDF_CITY_SOURCE_DEFS.values():
        config = _guangdong_source_config(source_def)
        config["coverage_expectation"] = _coverage_expectation_for_file_source(source_def.region_code, source_def.city_name)
        audit = GUANGDONG_CITY_SOURCE_AUDIT[source_def.region_code]
        config.setdefault("ops", {})["source_audit_status"] = audit["status"]
        sources.append(
            _upsert_source(
                session,
                site_id=config["stable"]["site_id"],
                name=f"{source_def.city_name}{source_def.publisher}-文件源",
                province="广东",
                city=source_def.city_name,
                region_code=source_def.region_code,
                format="pdf",
                downloadable=True,
                bucket="可自动采",
                frequency="monthly",
                url=source_def.entry_url,
                config=config,
                actor_id=actor_id,
            )
        )
    return sources


def _upsert_declaration_sources(
    session: Session,
    *,
    declared_periods: list[str],
    actor_id: str,
) -> list[DataSource]:
    sources: list[DataSource] = []
    for region_code, source_def in GUANGDONG_DECLARATION_CITY_SOURCES.items():
        config = _declaration_source_config(region_code=region_code, source_def=source_def, declared_periods=declared_periods)
        sources.append(
            _upsert_source(
                session,
                site_id=source_def["site_id"],
                name=source_def["source_name"],
                province="广东",
                city=source_def["region_name"],
                region_code=region_code,
                format="html_table",
                downloadable=False,
                bucket="覆盖声明",
                frequency="monthly",
                url=source_def["entry_url"],
                config=config,
                actor_id=actor_id,
            )
        )
    return sources


def _upsert_blocked_sources(session: Session, *, actor_id: str) -> list[DataSource]:
    sources: list[DataSource] = []
    for region_code, source_def in GUANGDONG_BLOCKED_CITY_SOURCES.items():
        config = _blocked_source_config(region_code=region_code, source_def=source_def)
        sources.append(
            _upsert_source(
                session,
                site_id=source_def["site_id"],
                name=source_def["source_name"],
                province="广东",
                city=source_def["region_name"],
                region_code=region_code,
                format="blocked",
                downloadable=False,
                bucket="受阻源",
                frequency="manual",
                url=source_def["entry_url"],
                config=config,
                actor_id=actor_id,
            )
        )
    return sources


def _coverage_expectation_for_file_source(region_code: str, region_name: str) -> dict[str, object]:
    audit = GUANGDONG_CITY_SOURCE_AUDIT[region_code]
    return {
        "mode": "file_source",
        "target_regions": [
            {
                "region_code": region_code,
                "region_name": region_name,
                "target_level": "city",
                "requires_city_source": True,
                "source_audit_status": audit["status"],
                "source_audit_note": audit["note"],
                "coverage_note": "广东官方文件源；Layer0只入原件不解析价格",
            }
        ],
    }


def _declaration_source_config(*, region_code: str, source_def: dict[str, str], declared_periods: list[str]) -> dict:
    audit = GUANGDONG_CITY_SOURCE_AUDIT[region_code]
    return _base_declaration_config(
        region_code=region_code,
        source_def=source_def,
        acquisition="coverage_declaration_only",
        attachment_mode="html_table",
        audit=audit,
        declared_periods=declared_periods,
        coverage_note="广东官方网页/表格源覆盖声明；Layer0只给原站跳转，不镜像价格内容",
    )


def _blocked_source_config(*, region_code: str, source_def: dict[str, str]) -> dict:
    audit = GUANGDONG_CITY_SOURCE_AUDIT[region_code]
    return _base_declaration_config(
        region_code=region_code,
        source_def=source_def,
        acquisition="manual_upload_required",
        attachment_mode="blocked",
        audit=audit,
        declared_periods=[],
        coverage_note="广东官方源存在但公开下载受阻；待017人工补录",
    )


def _base_declaration_config(
    *,
    region_code: str,
    source_def: dict[str, str],
    acquisition: str,
    attachment_mode: str,
    audit: dict[str, str],
    declared_periods: list[str],
    coverage_note: str,
) -> dict:
    return {
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
            "source_attachment_mode": attachment_mode,
            "parsability": "structured" if attachment_mode == "html_table" else "unknown",
            "acquisition": acquisition,
        },
        "parser": {
            "active_parser_version": "manual.coverage-declaration.v1",
            "parsers": {
                "manual.coverage-declaration.v1": {
                    "scope": acquisition,
                    "official_url": source_def["entry_url"],
                    "copyright_boundary": "no_price_table_mirror_link_out_only",
                }
            },
        },
        "coverage_expectation": {
            "mode": "coverage_declaration",
            "target_regions": [
                {
                    "region_code": region_code,
                    "region_name": source_def["region_name"],
                    "target_level": "city",
                    "requires_city_source": True,
                    "declared_periods": declared_periods,
                    "coverage_note": coverage_note,
                    "source_audit_status": audit["status"],
                    "source_audit_note": audit["note"],
                    "city_source_url": source_def["entry_url"],
                    "source_completeness_status": audit.get("source_completeness_status"),
                }
            ],
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier0_declaration_only",
            "source_audit_status": audit["status"],
            "schedule": {"frequency": "monthly"},
            "gated": False,
        },
    }


def _upsert_source(
    session: Session,
    *,
    site_id: str,
    name: str,
    province: str,
    city: str,
    region_code: str,
    format: str,
    downloadable: bool,
    bucket: str,
    frequency: str,
    url: str,
    config: dict,
    actor_id: str,
) -> DataSource:
    source = _find_source(session, site_id=site_id, region_code=region_code)
    if source is None:
        return create_data_source(
            session,
            source_scope="platform_public",
            tenant_code=None,
            managed_by="platform",
            source_type="info_price",
            connector_type="source_registry",
            name=name,
            data_domain="cost_info",
            base_url=url,
            url=url,
            province=province,
            city=city,
            region_code=region_code,
            format=format,
            downloadable=downloadable,
            bucket=bucket,
            frequency=frequency,
            config=config,
            created_by=actor_id,
        )

    source.name = name
    source.base_url = url
    source.url = url
    source.province = province
    source.city = city
    source.region_code = region_code
    source.format = format
    source.downloadable = downloadable
    source.bucket = bucket
    source.frequency = frequency
    source.config = config
    source.updated_at = datetime.now(UTC)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _find_source(session: Session, *, site_id: str, region_code: str) -> DataSource | None:
    candidates = session.scalars(
        select(DataSource)
        .where(DataSource.source_scope == "platform_public")
        .where(DataSource.source_type == "info_price")
        .where(DataSource.data_domain == "cost_info")
        .where(DataSource.region_code == region_code)
    ).all()
    for source in candidates:
        stable = source.config.get("stable") if isinstance(source.config, dict) else {}
        if stable.get("site_id") == site_id:
            return source
    return None
