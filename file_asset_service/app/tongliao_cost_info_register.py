"""One-shot: register latest 6 通辽建设工程材料价格 PDFs from zhujianju.tongliao.gov.cn

通辽 publishes issue-based (第X期) bimonthly cost info. period_kind="issue_based".

⚠️  Skill: .claude/skills/cost-info-ingestion/SKILL.md
"""

from __future__ import annotations

import os, sys
from datetime import date, datetime, timezone
from pathlib import Path
def _setup_path() -> None:
    here = Path(__file__).resolve().parent.parent
    if str(here) not in sys.path: sys.path.insert(0, str(here))
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

PDF_DIR = Path(r"D:\大匠通\新指标云\data_lake_handoff\data_lake_handoff\quota\内蒙古\通辽\造价信息")

ISSUES: list[dict] = [
    {"file_name": "2026年第4期通辽建设工程材料价格.pdf", "title": "关于发布通辽市2026年第4期建设工程材料价格的通知",
     "period_raw": "2026年第4期", "period_kind": "issue_based",
     "period_start": "2026-07", "period_year": "2026", "period_issue_no": "4",
     "publish_date": date(2026, 7, 10),
     "source_url": "https://zhujianju.tongliao.gov.cn/xwzx/tzgg/202607/t20260710_1059624.html"},
    {"file_name": "2026年第3期通辽建设工程材料价格.pdf", "title": "关于发布通辽市2026年第3期建设工程材料价格的通知",
     "period_raw": "2026年第3期", "period_kind": "issue_based",
     "period_start": "2026-05", "period_year": "2026", "period_issue_no": "3",
     "publish_date": date(2026, 6, 5),
     "source_url": "https://zhujianju.tongliao.gov.cn/xwzx/tzgg/202606/t20260605_1052303.html"},
    {"file_name": "2026年第2期通辽建设工程材料价格.pdf", "title": "关于发布通辽市2026年第2期建设工程材料价格的通知",
     "period_raw": "2026年第2期", "period_kind": "issue_based",
     "period_start": "2026-03", "period_year": "2026", "period_issue_no": "2",
     "publish_date": date(2026, 5, 8),
     "source_url": "https://zhujianju.tongliao.gov.cn/xwzx/tzgg/202605/t20260508_1046959.html"},
    {"file_name": "2026年第1期通辽建设工程材料价格.pdf", "title": "关于发布通辽市2026年第1期建设工程材料价格的通知",
     "period_raw": "2026年第1期", "period_kind": "issue_based",
     "period_start": "2026-01", "period_year": "2026", "period_issue_no": "1",
     "publish_date": date(2026, 3, 13),
     "source_url": "https://zhujianju.tongliao.gov.cn/xwzx/tzgg/202603/t20260313_1037764.html"},
    {"file_name": "2025年第6期通辽建设工程材料价格.pdf", "title": "关于发布通辽市2025年第6期建设工程材料价格的通知",
     "period_raw": "2025年第6期", "period_kind": "issue_based",
     "period_start": "2025-11", "period_year": "2025", "period_issue_no": "6",
     "publish_date": date(2025, 12, 31),
     "source_url": "https://zhujianju.tongliao.gov.cn/xwzx/tzgg/202512/t20251231_1025260.html"},
    {"file_name": "2025年第5期通辽建设工程材料价格.pdf", "title": "关于发布通辽市2025年第5期建设工程材料价格的通知",
     "period_raw": "2025年第5期", "period_kind": "issue_based",
     "period_start": "2025-09", "period_year": "2025", "period_issue_no": "5",
     "publish_date": date(2025, 11, 6),
     "source_url": "https://zhujianju.tongliao.gov.cn/xwzx/tzgg/202511/t20251107_1011888.html"},
]

REGION_CODE = "150500"
COVERAGE_REGION_CODE = "150500"
SOURCE_NAME = "通辽市住建局-造价信息-人工补录"
SOURCE_TYPE = "info_price"
DOMAIN_TYPE = "cost_info"
TENANT_CODE = "platform_public"

_now = lambda: datetime.now(timezone.utc)
_mk = lambda: {"source_level": "manual", "tagged_by": "script:tongliao_register", "tagged_at": _now().isoformat()}

def ensure_data_source(s: Session) -> DataSource:
    ds = s.scalar(select(DataSource).where(DataSource.source_type==SOURCE_TYPE, DataSource.region_code==REGION_CODE, DataSource.name==SOURCE_NAME))
    if ds: print(f"[OK] DataSource exists: {ds.source_id}"); return ds
    ds = DataSource(source_id=f"ds_{REGION_CODE}_{int(_now().timestamp())}",
        source_scope="platform_public", tenant_code=None, asset_tenant_code=TENANT_CODE,
        managed_by="platform", source_type=SOURCE_TYPE, connector_type="manual_upload",
        name=SOURCE_NAME, base_url="https://zhujianju.tongliao.gov.cn/xwzx/tzgg/",
        region_code=REGION_CODE, data_domain=DOMAIN_TYPE, format="pdf",
        downloadable=True, bucket="cost-raw", frequency="bimonthly", status="active",
        created_by="admin:tongliao_register",
        config={"stable":{"site_id":f"cost_info.manual.{REGION_CODE}","domain_type":DOMAIN_TYPE,
            "region_code":COVERAGE_REGION_CODE,"coverage_region_code":COVERAGE_REGION_CODE,
            "publisher_scope":"city","publisher_region_code":COVERAGE_REGION_CODE,
            "publisher_name":"通辽市住房和城乡建设局"},"ops":{"source_audit_status":"人工补录"}})
    s.add(ds); s.flush(); print(f"[OK] DataSource created: {ds.source_id}")
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
    init_db(); store = get_object_store(); factory = get_session_factory()
    with factory() as session:
        ds = ensure_data_source(session); session.commit(); print()
        results = []
        for i, issue in enumerate(ISSUES, 1):
            print(f"--- [{i}/{len(ISSUES)}] {issue['file_name']} ---")
            fp = PDF_DIR / issue["file_name"]
            if not fp.exists(): print(f"  [SKIP] Not found"); continue
            content = fp.read_bytes()
            reg = register_asset(session, storage=store, tenant_code=TENANT_CODE, source_type=SOURCE_TYPE,
                source_id=ds.source_id, batch_id=None, file_name=issue["file_name"], content=content,
                source_url=issue["source_url"],
                source_item_key=f"manual:{COVERAGE_REGION_CODE}:{issue['period_raw']}:{issue['file_name']}",
                source_metadata={"channel_type":"manual_upload","collection_method":"manual_denovo",
                    "region_code":COVERAGE_REGION_CODE,"period":issue["period_raw"],"download_site":"zhujianju.tongliao.gov.cn"},
                channel_type="manual_upload")
            session.commit()
            print(f"  [{('DUP' if reg.duplicated else 'NEW')}] {reg.file_id} sha256={reg.sha256[:12]}")
            biz_key = build_cost_info_business_key(source_id=ds.source_id, region_code=COVERAGE_REGION_CODE,
                period=issue["period_raw"], title=issue["title"])
            archive, is_new = create_archive_from_ingest_event_with_flag(session,
                event_id=reg.ingest_event_id, domain_type=DOMAIN_TYPE, channel_type="manual_upload",
                collection_method="manual_denovo", price_kind="guidance", period_kind=issue["period_kind"],
                title=issue["title"], visibility_scope="public", status="collected", business_key=biz_key,
                region_code=REGION_CODE, publish_date=issue["publish_date"],
                metadata={k:{"value":issue[k]}|_mk() for k in ["period_raw","period_start","period_year","period_issue_no"]}
                |{"coverage_region_code":{"value":COVERAGE_REGION_CODE}|_mk(),
                  "price_source_type":{"value":"info_price"}|_mk(),
                  "publisher":{"value":"通辽市住房和城乡建设局"}|_mk(),
                  "publisher_scope":{"value":"city"}|_mk(),
                  "publisher_region_code":{"value":COVERAGE_REGION_CODE}|_mk()},
                field_sources={k:_mk() for k in ["domain_type","channel_type","collection_method",
                    "business_key","title","region_code","publish_date","price_kind","period_kind"]},
                actor_type="user", actor_id="admin:tongliao_register")
            archive.coverage_period = issue["period_start"]
            session.commit()
            print(f"  [OK]  {archive.archive_id} cp={archive.coverage_period}"); print()
            results.append({"issue":issue,"archive_id":archive.archive_id})
    print("="*60)
    print(f"Done. {len(results)}/{len(ISSUES)} registered.")
    for r in results: print(f"  {r['issue']['file_name']} → {r['archive_id']}")

if __name__ == "__main__": main()
