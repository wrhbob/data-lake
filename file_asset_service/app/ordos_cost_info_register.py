"""One-shot: register latest 6 鄂尔多斯建设工程造价信息 PDFs from zjj.ordos.gov.cn

Mixed format: bimonthly (11、12月) + monthly. All use period_kind="monthly".

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

PDF_DIR = Path(r"D:\大匠通\新指标云\data_lake_handoff\data_lake_handoff\quota\内蒙古\鄂尔多斯\造价信息")

ISSUES: list[dict] = [
    {"file_name": "2025年11-12月鄂尔多斯建设工程造价信息.pdf", "title": "鄂尔多斯市2025年11、12月建设工程造价信息",
     "period_raw": "2025年11-12月", "period_kind": "monthly",
     "period_start": "2025-11", "period_year": "2025",
     "publish_date": date(2025, 12, 31),
     "source_url": "https://zjj.ordos.gov.cn/zxfwx/bmcx/jzaz/2023_160854/202512/t20251231_3866017.html"},
    {"file_name": "2025年10月鄂尔多斯建设工程造价信息.pdf", "title": "鄂尔多斯市2025年10月建设工程造价信息",
     "period_raw": "2025年10月", "period_kind": "monthly",
     "period_start": "2025-10", "period_year": "2025",
     "publish_date": date(2025, 11, 3),
     "source_url": "https://zjj.ordos.gov.cn/zxfwx/bmcx/jzaz/2023_160854/202511/t20251103_3847601.html"},
    {"file_name": "2025年9月鄂尔多斯建设工程造价信息.pdf", "title": "鄂尔多斯市2025年9月建设工程造价信息",
     "period_raw": "2025年9月", "period_kind": "monthly",
     "period_start": "2025-09", "period_year": "2025",
     "publish_date": date(2025, 9, 30),
     "source_url": "https://zjj.ordos.gov.cn/zxfwx/bmcx/jzaz/2023_160854/202509/t20250930_3839775.html"},
    {"file_name": "2025年8月鄂尔多斯建设工程造价信息.pdf", "title": "鄂尔多斯市2025年8月建设工程造价信息",
     "period_raw": "2025年8月", "period_kind": "monthly",
     "period_start": "2025-08", "period_year": "2025",
     "publish_date": date(2025, 8, 31),
     "source_url": "https://zjj.ordos.gov.cn/zxfwx/bmcx/jzaz/2023_160854/202509/t20250903_3831758.html"},
    {"file_name": "2025年7月鄂尔多斯建设工程造价信息.pdf", "title": "鄂尔多斯市2025年7月建设工程造价信息",
     "period_raw": "2025年7月", "period_kind": "monthly",
     "period_start": "2025-07", "period_year": "2025",
     "publish_date": date(2025, 7, 31),
     "source_url": "https://zjj.ordos.gov.cn/zxfwx/bmcx/jzaz/2023_160854/202508/t20250806_3823159.html"},
    {"file_name": "2025年6月鄂尔多斯建设工程造价信息.pdf", "title": "鄂尔多斯市2025年6月建设工程造价信息",
     "period_raw": "2025年6月", "period_kind": "monthly",
     "period_start": "2025-06", "period_year": "2025",
     "publish_date": date(2025, 6, 30),
     "source_url": "https://zjj.ordos.gov.cn/zxfwx/bmcx/jzaz/2023_160854/202507/t20250701_3811117.html"},
]

REGION_CODE = "150600"; COVERAGE_REGION_CODE = "150600"
SOURCE_NAME = "鄂尔多斯市住建局-造价信息-人工补录"
SOURCE_TYPE = "info_price"; DOMAIN_TYPE = "cost_info"; TENANT_CODE = "platform_public"

_now = lambda: datetime.now(timezone.utc)
_mk = lambda: {"source_level": "manual", "tagged_by": "script:ordos_register", "tagged_at": _now().isoformat()}

def ensure_data_source(s: Session) -> DataSource:
    ds = s.scalar(select(DataSource).where(DataSource.source_type==SOURCE_TYPE, DataSource.region_code==REGION_CODE, DataSource.name==SOURCE_NAME))
    if ds: print(f"[OK] DataSource exists: {ds.source_id}"); return ds
    ds = DataSource(source_id=f"ds_{REGION_CODE}_{int(_now().timestamp())}",
        source_scope="platform_public", tenant_code=None, asset_tenant_code=TENANT_CODE,
        managed_by="platform", source_type=SOURCE_TYPE, connector_type="manual_upload",
        name=SOURCE_NAME, base_url="https://zjj.ordos.gov.cn/zxfwx/bmcx/jzaz/",
        region_code=REGION_CODE, data_domain=DOMAIN_TYPE, format="pdf",
        downloadable=True, bucket="cost-raw", frequency="monthly", status="active",
        created_by="admin:ordos_register",
        config={"stable":{"site_id":f"cost_info.manual.{REGION_CODE}","domain_type":DOMAIN_TYPE,
            "region_code":COVERAGE_REGION_CODE,"coverage_region_code":COVERAGE_REGION_CODE,
            "publisher_scope":"city","publisher_region_code":COVERAGE_REGION_CODE,
            "publisher_name":"鄂尔多斯市住房和城乡建设局"},"ops":{"source_audit_status":"人工补录"}})
    s.add(ds); s.flush(); print(f"[OK] DataSource created: {ds.source_id}")
    return ds

def main() -> None:
    init_db(); store = get_object_store(); factory = get_session_factory()
    with factory() as session:
        ds = ensure_data_source(session); session.commit(); print()
        results = []
        for i, issue in enumerate(ISSUES, 1):
            print(f"--- [{i}/{len(ISSUES)}] {issue['file_name']} ---")
            fp = PDF_DIR / issue["file_name"]
            if not fp.exists(): print(f"  [SKIP] Not found"); continue
            reg = register_asset(session, storage=store, tenant_code=TENANT_CODE, source_type=SOURCE_TYPE,
                source_id=ds.source_id, batch_id=None, file_name=issue["file_name"], content=fp.read_bytes(),
                source_url=issue["source_url"],
                source_item_key=f"manual:{COVERAGE_REGION_CODE}:{issue['period_raw']}:{issue['file_name']}",
                source_metadata={"channel_type":"manual_upload","collection_method":"manual_denovo",
                    "region_code":COVERAGE_REGION_CODE,"period":issue["period_raw"],"download_site":"zjj.ordos.gov.cn"},
                channel_type="manual_upload")
            session.commit()
            print(f"  [{('DUP' if reg.duplicated else 'NEW')}] {reg.file_id}")
            biz_key = build_cost_info_business_key(source_id=ds.source_id, region_code=COVERAGE_REGION_CODE,
                period=issue["period_raw"], title=issue["title"])
            archive, _ = create_archive_from_ingest_event_with_flag(session,
                event_id=reg.ingest_event_id, domain_type=DOMAIN_TYPE, channel_type="manual_upload",
                collection_method="manual_denovo", price_kind="guidance", period_kind=issue["period_kind"],
                title=issue["title"], visibility_scope="public", status="collected", business_key=biz_key,
                region_code=REGION_CODE, publish_date=issue["publish_date"],
                metadata={k:{"value":issue[k]}|_mk() for k in ["period_raw","period_start","period_year"]}
                |{"coverage_region_code":{"value":COVERAGE_REGION_CODE}|_mk(),
                  "price_source_type":{"value":"info_price"}|_mk(),
                  "publisher":{"value":"鄂尔多斯市住房和城乡建设局"}|_mk(),
                  "publisher_scope":{"value":"city"}|_mk(),
                  "publisher_region_code":{"value":COVERAGE_REGION_CODE}|_mk()},
                field_sources={k:_mk() for k in ["domain_type","channel_type","collection_method",
                    "business_key","title","region_code","publish_date","price_kind","period_kind"]},
                actor_type="user", actor_id="admin:ordos_register")
            archive.coverage_period = issue["period_start"]; session.commit()
            print(f"  [OK]  cp={archive.coverage_period}"); print()
            results.append({"issue":issue,"archive_id":archive.archive_id})
    print("="*60)
    print(f"Done. {len(results)}/{len(ISSUES)}. cost_info total: ~936")
    for r in results: print(f"  {r['issue']['file_name']} → {r['archive_id']}")

if __name__ == "__main__": main()
