"""One-shot: register latest 6 乌海建设工程造价信息 PDFs from zjw.wuhai.gov.cn

⚠️  Skill: .claude/skills/cost-info-ingestion/SKILL.md
    Checklist: .claude/skills/cost-info-ingestion/references/metadata-field-checklist.md
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

PDF_DIR = Path(r"D:\大匠通\新指标云\data_lake_handoff\data_lake_handoff\quota\内蒙古\乌海\造价信息")

ISSUES: list[dict] = [
    {"file_name": "2026年6月乌海建设工程造价信息.pdf", "title": "关于发布乌海市2026年6月建设工程造价信息的通知",
     "period_raw": "2026年6月份", "period_kind": "monthly",
     "period_start": "2026-06", "period_year": "2026",
     "publish_date": date(2026, 7, 6),
     "source_url": "http://zjw.wuhai.gov.cn/zjj/879643/879725/879730/879750/2452446/index.html"},
    {"file_name": "2026年5月乌海建设工程造价信息.pdf", "title": "关于发布乌海市2026年5月建设工程造价信息的通知",
     "period_raw": "2026年5月份", "period_kind": "monthly",
     "period_start": "2026-05", "period_year": "2026",
     "publish_date": date(2026, 6, 9),
     "source_url": "http://zjw.wuhai.gov.cn/zjj/879643/879725/879730/879750/2441133/index.html"},
    {"file_name": "2026年4月乌海建设工程造价信息.pdf", "title": "关于发布乌海市2026年4月建设工程造价信息的通知",
     "period_raw": "2026年4月份", "period_kind": "monthly",
     "period_start": "2026-04", "period_year": "2026",
     "publish_date": date(2026, 5, 8),
     "source_url": "http://zjw.wuhai.gov.cn/zjj/879643/879725/879730/879750/2428771/index.html"},
    {"file_name": "2026年3月乌海建设工程造价信息.pdf", "title": "关于发布乌海市2026年3月建设工程造价信息的通知",
     "period_raw": "2026年3月份", "period_kind": "monthly",
     "period_start": "2026-03", "period_year": "2026",
     "publish_date": date(2026, 4, 30),
     "source_url": "http://zjw.wuhai.gov.cn/zjj/879643/879725/879730/879750/2421354/index.html"},
    {"file_name": "2026年2月乌海建设工程造价信息.pdf", "title": "关于发布乌海市2026年2月建设工程造价信息的通知",
     "period_raw": "2026年2月份", "period_kind": "monthly",
     "period_start": "2026-02", "period_year": "2026",
     "publish_date": date(2026, 4, 24),
     "source_url": "http://zjw.wuhai.gov.cn/zjj/879643/879725/879730/879750/2417606/index.html"},
    {"file_name": "2026年1月乌海建设工程造价信息.pdf", "title": "关于发布乌海市2026年1月建设工程造价信息的通知",
     "period_raw": "2026年1月份", "period_kind": "monthly",
     "period_start": "2026-01", "period_year": "2026",
     "publish_date": date(2026, 4, 20),
     "source_url": "http://zjw.wuhai.gov.cn/zjj/879643/879725/879730/879750/2417599/index.html"},
]

REGION_CODE = "150300"        # 乌海市 6-digit
COVERAGE_REGION_CODE = "150300"
SOURCE_NAME = "乌海市住建局-造价信息-人工补录"
SOURCE_TYPE = "info_price"
DOMAIN_TYPE = "cost_info"
TENANT_CODE = "platform_public"


def _now() -> datetime: return datetime.now(timezone.utc)
def _now_iso() -> str: return _now().isoformat()
def mk_source() -> dict: return {"source_level": "manual", "tagged_by": "script:wuhai_register", "tagged_at": _now_iso()}


def ensure_data_source(session: Session) -> DataSource:
    existing = session.scalar(
        select(DataSource).where(
            DataSource.source_type == SOURCE_TYPE,
            DataSource.region_code == REGION_CODE,
            DataSource.name == SOURCE_NAME))
    if existing:
        print(f"[OK] DataSource already exists: {existing.source_id}")
        return existing
    ds = DataSource(
        source_id=f"ds_{REGION_CODE}_{int(_now().timestamp())}",
        source_scope="platform_public", tenant_code=None, asset_tenant_code=TENANT_CODE,
        managed_by="platform", source_type=SOURCE_TYPE, connector_type="manual_upload",
        name=SOURCE_NAME, base_url="http://zjw.wuhai.gov.cn/zjj/879643/879725/879730/879750/",
        region_code=REGION_CODE, data_domain=DOMAIN_TYPE, format="pdf",
        downloadable=True, bucket="cost-raw", frequency="monthly", status="active",
        created_by="admin:wuhai_register",
        config={"stable": {
            "site_id": f"cost_info.manual.{REGION_CODE}",
            "domain_type": DOMAIN_TYPE, "region_code": COVERAGE_REGION_CODE,
            "coverage_region_code": COVERAGE_REGION_CODE,
            "publisher_scope": "city", "publisher_region_code": COVERAGE_REGION_CODE,
            "publisher_name": "乌海市住房和城乡建设局"},
            "ops": {"source_audit_status": "人工补录"}})
    session.add(ds)
    session.flush()
    print(f"[OK] DataSource created: {ds.source_id}")
    return ds


def main() -> None:
    try:
        from dotenv import load_dotenv

        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        load_dotenv(os.path.join(_root, ".env"), override=False)
    except Exception:
        pass
    os.environ.setdefault("FILE_ASSET_DATABASE_URL", "postgresql+psycopg://file_asset:file_asset@127.0.0.1:15432/file_asset")
    os.environ.setdefault("FILE_ASSET_S3_ENDPOINT_URL", "http://djtsoft.x3322.net:9000")

    print(f"DB: {get_settings().database_url}")
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
            fp = PDF_DIR / issue["file_name"]
            if not fp.exists():
                print(f"  [SKIP] File not found: {fp}")
                continue
            content = fp.read_bytes()

            reg = register_asset(session, storage=store,
                tenant_code=TENANT_CODE, source_type=SOURCE_TYPE, source_id=ds.source_id,
                batch_id=None, file_name=issue["file_name"], content=content,
                source_url=issue["source_url"],
                source_item_key=f"manual:{COVERAGE_REGION_CODE}:{issue['period_raw']}:{issue['file_name']}",
                source_metadata={"channel_type": "manual_upload", "collection_method": "manual_denovo",
                    "region_code": COVERAGE_REGION_CODE, "period": issue["period_raw"],
                    "download_site": "zjw.wuhai.gov.cn"},
                channel_type="manual_upload")
            session.commit()
            print(f"  [{('DUP' if reg.duplicated else 'NEW')}] file_id={reg.file_id}  sha256={reg.sha256[:12]}")

            biz_key = build_cost_info_business_key(source_id=ds.source_id,
                region_code=COVERAGE_REGION_CODE, period=issue["period_raw"], title=issue["title"])

            archive, is_new = create_archive_from_ingest_event_with_flag(session,
                event_id=reg.ingest_event_id, domain_type=DOMAIN_TYPE, channel_type="manual_upload",
                collection_method="manual_denovo", price_kind="guidance",
                period_kind=issue["period_kind"], title=issue["title"],
                visibility_scope="public", status="collected", business_key=biz_key,
                region_code=REGION_CODE, publish_date=issue["publish_date"],
                metadata={
                    "period_raw": {"value": issue["period_raw"]} | mk_source(),
                    "period_start": {"value": issue["period_start"]} | mk_source(),
                    "period_year": {"value": issue["period_year"]} | mk_source(),
                    "coverage_region_code": {"value": COVERAGE_REGION_CODE} | mk_source(),
                    "price_source_type": {"value": "info_price"} | mk_source(),
                    "publisher": {"value": "乌海市住房和城乡建设局"} | mk_source(),
                    "publisher_scope": {"value": "city"} | mk_source(),
                    "publisher_region_code": {"value": COVERAGE_REGION_CODE} | mk_source(),
                },
                field_sources={k: mk_source() for k in [
                    "domain_type", "channel_type", "collection_method",
                    "business_key", "title", "region_code", "publish_date",
                    "price_kind", "period_kind"]},
                actor_type="user", actor_id="admin:wuhai_register")
            archive.coverage_period = issue["period_start"]
            session.commit()
            print(f"  [OK]  archive_id={archive.archive_id}  cp={archive.coverage_period}")
            print()
            results.append({"issue": issue, "archive_id": archive.archive_id})

    print("=" * 60)
    print(f"Done. {len(results)} of {len(ISSUES)} registered.")
    for r in results:
        print(f"  {r['issue']['file_name']}  →  {r['archive_id']}")


if __name__ == "__main__":
    main()
