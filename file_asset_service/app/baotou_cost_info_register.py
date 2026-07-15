"""One-shot: register latest 6 包头工程造价信息 PDFs from
http://116.114.161.151:200/btszfhcxjsj/zfxxgk/fdzdgknr/fd_sjkf/

Lessons from 呼和浩特:
- region_code MUST be 6-digit (150200) to match regionTree city codes
- period_kind must be "monthly"
- period_start + period_year must be in metadata for year/month filter extraction
- coverage_period must be populated
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path


def _setup_path() -> None:
    here = Path(__file__).resolve().parent.parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))


_setup_path()

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assets import register_asset
from app.archive_rules import build_cost_info_business_key
from app.archive_service import create_archive_from_ingest_event_with_flag
from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import DataSource
from app.storage import get_object_store

# ── Constants ────────────────────────────────────────────────────────────────

PDF_DIR = Path(
    r"D:\大匠通\新指标云\data_lake_handoff\data_lake_handoff\quota\内蒙古\包头\造价信息"
)

ISSUES: list[dict] = [
    {"file_name": "2026年6月份包头工程造价信息.pdf", "title": "2026年6月份包头工程造价信息",
     "period_raw": "2026年6月份", "period_kind": "monthly",
     "period_start": "2026-06", "period_year": "2026",
     "publish_date": date(2026, 7, 3),
     "source_url": "http://116.114.161.151:200/btszfhcxjsj/zfxxgk/fdzdgknr/fd_sjkf/202607/t20260703_934316.html"},
    {"file_name": "2026年5月份包头工程造价信息.pdf", "title": "2026年5月份包头工程造价信息",
     "period_raw": "2026年5月份", "period_kind": "monthly",
     "period_start": "2026-05", "period_year": "2026",
     "publish_date": date(2026, 6, 10),
     "source_url": "http://116.114.161.151:200/btszfhcxjsj/zfxxgk/fdzdgknr/fd_sjkf/202606/t20260610_919669.html"},
    {"file_name": "2026年4月份包头工程造价信息.pdf", "title": "2026年4月份包头工程造价信息",
     "period_raw": "2026年4月份", "period_kind": "monthly",
     "period_start": "2026-04", "period_year": "2026",
     "publish_date": date(2026, 5, 9),
     "source_url": "http://116.114.161.151:200/btszfhcxjsj/zfxxgk/fdzdgknr/fd_sjkf/202605/t20260509_904441.html"},
    {"file_name": "2026年3月份包头工程造价信息.pdf", "title": "2026年3月份包头工程造价信息",
     "period_raw": "2026年3月份", "period_kind": "monthly",
     "period_start": "2026-03", "period_year": "2026",
     "publish_date": date(2026, 4, 10),
     "source_url": "http://116.114.161.151:200/btszfhcxjsj/zfxxgk/fdzdgknr/fd_sjkf/202604/t20260410_870794.html"},
    {"file_name": "2026年2月份包头工程造价信息.pdf", "title": "2026年2月份包头工程造价信息",
     "period_raw": "2026年2月份", "period_kind": "monthly",
     "period_start": "2026-02", "period_year": "2026",
     "publish_date": date(2026, 3, 11),
     "source_url": "http://116.114.161.151:200/btszfhcxjsj/zfxxgk/fdzdgknr/fd_sjkf/202603/t20260311_817588.html"},
    {"file_name": "2026年1月份包头工程造价信息.pdf", "title": "2026年1月份包头工程造价信息",
     "period_raw": "2026年1月份", "period_kind": "monthly",
     "period_start": "2026-01", "period_year": "2026",
     "publish_date": date(2026, 2, 10),
     "source_url": "http://116.114.161.151:200/btszfhcxjsj/zfxxgk/fdzdgknr/fd_sjkf/202602/t20260210_783284.html"},
]

REGION_CODE = "150200"        # 包头市 6-digit (must match regionTree city code!)
COVERAGE_REGION_CODE = "150200"
SOURCE_NAME = "包头市住建局-数据开放-造价信息-人工补录"
SOURCE_TYPE = "info_price"
DOMAIN_TYPE = "cost_info"
TENANT_CODE = "platform_public"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def ensure_data_source(session: Session) -> DataSource:
    existing = session.scalar(
        select(DataSource).where(
            DataSource.source_type == SOURCE_TYPE,
            DataSource.region_code == REGION_CODE,
            DataSource.name == SOURCE_NAME,
        )
    )
    if existing is not None:
        print(f"[OK] DataSource already exists: {existing.source_id}")
        return existing

    ds = DataSource(
        source_id=f"ds_{REGION_CODE}_{int(_now().timestamp())}",
        source_scope="platform_public",
        tenant_code=None,
        asset_tenant_code=TENANT_CODE,
        managed_by="platform",
        source_type=SOURCE_TYPE,
        connector_type="manual_upload",
        name=SOURCE_NAME,
        base_url="http://116.114.161.151:200/btszfhcxjsj/zfxxgk/fdzdgknr/fd_sjkf/",
        region_code=REGION_CODE,
        data_domain=DOMAIN_TYPE,
        format="pdf",
        downloadable=True,
        bucket="cost-raw",
        frequency="monthly",
        status="active",
        created_by="admin:baotou_register",
        config={
            "stable": {
                "site_id": f"cost_info.manual.{REGION_CODE}",
                "domain_type": DOMAIN_TYPE,
                "region_code": COVERAGE_REGION_CODE,
                "coverage_region_code": COVERAGE_REGION_CODE,
                "publisher_scope": "city",
                "publisher_region_code": COVERAGE_REGION_CODE,
                "publisher_name": "包头市住房和城乡建设局",
            },
            "ops": {"source_audit_status": "人工补录"},
        },
    )
    session.add(ds)
    session.flush()
    print(f"[OK] DataSource created: {ds.source_id}")
    return ds


def mk_field_source() -> dict:
    return {"source_level": "manual", "tagged_by": "script:baotou_register", "tagged_at": _now_iso()}


def main() -> None:
    # 从仓库根 .env 加载 DB/S3 凭据；源码不硬编码任何密钥。
    try:
        from dotenv import load_dotenv

        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        load_dotenv(os.path.join(_root, ".env"), override=False)
    except Exception:
        pass
    print(f"DB:  {get_settings().database_url}")
    print(f"S3:  {get_settings().s3_endpoint_url}")
    print(f"PDF: {PDF_DIR}")
    print(f"N:   {len(ISSUES)} issues")
    print()

    init_db()

    store = get_object_store()
    factory = get_session_factory()

    with factory() as session:
        ds = ensure_data_source(session)
        session.commit()
        print()

        results: list[dict] = []
        for i, issue in enumerate(ISSUES, 1):
            print(f"--- [{i}/{len(ISSUES)}] {issue['file_name']} ---")
            file_path = PDF_DIR / issue["file_name"]
            if not file_path.exists():
                print(f"  [SKIP] File not found: {file_path}")
                continue

            content = file_path.read_bytes()

            reg = register_asset(
                session,
                storage=store,
                tenant_code=TENANT_CODE,
                source_type=SOURCE_TYPE,
                source_id=ds.source_id,
                batch_id=None,
                file_name=issue["file_name"],
                content=content,
                source_url=issue["source_url"],
                source_item_key=f"manual:{COVERAGE_REGION_CODE}:{issue['period_raw']}:{issue['file_name']}",
                source_metadata={
                    "channel_type": "manual_upload",
                    "collection_method": "manual_denovo",
                    "region_code": COVERAGE_REGION_CODE,
                    "period": issue["period_raw"],
                    "download_site": "116.114.161.151:200",
                },
                channel_type="manual_upload",
            )
            session.commit()
            dupe = "DUP" if reg.duplicated else "NEW"
            print(f"  [{dupe}] file_id={reg.file_id}  sha256={reg.sha256[:12]}")

            biz_key = build_cost_info_business_key(
                source_id=ds.source_id,
                region_code=COVERAGE_REGION_CODE,
                period=issue["period_raw"],
                title=issue["title"],
            )

            archive, is_new = create_archive_from_ingest_event_with_flag(
                session,
                event_id=reg.ingest_event_id,  # type: ignore[arg-type]
                domain_type=DOMAIN_TYPE,
                channel_type="manual_upload",
                collection_method="manual_denovo",
                price_kind="guidance",
                period_kind=issue["period_kind"],
                title=issue["title"],
                visibility_scope="public",
                status="collected",
                business_key=biz_key,
                region_code=REGION_CODE,
                publish_date=issue["publish_date"],
                metadata={
                    "period_raw": {"value": issue["period_raw"]} | mk_field_source(),
                    "period_start": {"value": issue["period_start"]} | mk_field_source(),
                    "period_year": {"value": issue["period_year"]} | mk_field_source(),
                    "coverage_region_code": {"value": COVERAGE_REGION_CODE} | mk_field_source(),
                    "price_source_type": {"value": "info_price"} | mk_field_source(),
                    "publisher": {"value": "包头市住房和城乡建设局"} | mk_field_source(),
                    "publisher_scope": {"value": "city"} | mk_field_source(),
                    "publisher_region_code": {"value": COVERAGE_REGION_CODE} | mk_field_source(),
                },
                field_sources={k: mk_field_source() for k in [
                    "domain_type", "channel_type", "collection_method",
                    "business_key", "title", "region_code", "publish_date",
                    "price_kind", "period_kind",
                ]},
                actor_type="user",
                actor_id="admin:baotou_register",
            )
            # coverage_period backfill (must be done after Archive creation)
            archive.coverage_period = issue["period_start"]
            session.commit()
            print(f"  [OK]  archive_id={archive.archive_id}  coverage_period={archive.coverage_period}")
            print()

            results.append({"issue": issue, "archive_id": archive.archive_id, "sha256": reg.sha256})

    print("=" * 60)
    print(f"Done. {len(results)} of {len(ISSUES)} registered.")
    for r in results:
        print(f"  {r['issue']['file_name']}  →  {r['archive_id']}")


if __name__ == "__main__":
    main()
