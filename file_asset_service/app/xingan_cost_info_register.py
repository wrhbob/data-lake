"""兴安盟 bimonthly/quarterly material price info from zjj.xam.gov.cn"""
from __future__ import annotations
import os, sys; from datetime import date, datetime, timezone; from pathlib import Path
def _p(): h=Path(__file__).resolve().parent.parent; str(h) not in sys.path and sys.path.insert(0,str(h))
_p()
from sqlalchemy import select
from app.assets import register_asset
from app.archive_rules import build_cost_info_business_key
from app.archive_service import create_archive_from_ingest_event_with_flag
from app.database import get_session_factory, init_db
from app.models import DataSource; from app.storage import get_object_store

PDF_DIR = Path(r"D:\大匠通\新指标云\data_lake_handoff\data_lake_handoff\quota\内蒙古\兴安盟\造价信息")
ISSUES = [
    {"fn":"2026年3-4月兴安盟材料信息价格.pdf","title":"兴安盟各价区2026年3、4月份主要材料信息价格",
     "pr":"2026年3-4月","ps":"2026-03","py":"2026","pk":"monthly","pd":date(2026,5,28),
     "su":"http://zjj.xam.gov.cn/jsj/2026-05/29/article_2026052911021730070.html"},
    {"fn":"2026年1-2月兴安盟材料信息价格.pdf","title":"兴安盟各价区2026年1、2月份主要材料信息价格",
     "pr":"2026年1-2月","ps":"2026-01","py":"2026","pk":"monthly","pd":date(2026,3,11),
     "su":"http://zjj.xam.gov.cn/jsj/2026-03/12/article_2026031216174789428.html"},
    {"fn":"2025年11-12月兴安盟材料信息价格.pdf","title":"兴安盟各价区2025年11、12月份主要材料信息价格",
     "pr":"2025年11-12月","ps":"2025-11","py":"2025","pk":"monthly","pd":date(2026,1,7),
     "su":"http://zjj.xam.gov.cn/jsj/2026-01/07/article_2026010715040518759.html"},
    {"fn":"2025年9-10月兴安盟材料信息价格.pdf","title":"兴安盟各价区2025年9、10月份主要材料信息价格",
     "pr":"2025年9-10月","ps":"2025-09","py":"2025","pk":"monthly","pd":date(2025,11,10),
     "su":"http://zjj.xam.gov.cn/jsj/2025-11/18/article_2025111811460732641.html"},
    {"fn":"2025年7-8月兴安盟材料信息价格.pdf","title":"兴安盟各价区2025年7、8月份主要材料信息价格",
     "pr":"2025年7-8月","ps":"2025-07","py":"2025","pk":"monthly","pd":date(2025,9,4),
     "su":"http://zjj.xam.gov.cn/jsj/2025-09/09/article_2025090911304135844.html"},
    {"fn":"2025年第二季度兴安盟材料信息价格.pdf","title":"兴安盟各价区2025年第二季度主要材料信息价格",
     "pr":"2025年第二季度","ps":"2025-04","py":"2025","pk":"monthly","pd":date(2025,8,12),
     "su":"http://zjj.xam.gov.cn/jsj/2025-08/12/article_2025081214412451991.html"},
]
RC="152200"; CRC=RC; SN="兴安盟住建局-造价信息-人工补录"
ST="info_price"; DT="cost_info"; TC="platform_public"
_now=lambda:datetime.now(timezone.utc)
_mk=lambda:{"source_level":"manual","tagged_by":"script:xingan_register","tagged_at":_now().isoformat()}
def ensure(s):
    d=s.scalar(select(DataSource).where(DataSource.source_type==ST,DataSource.region_code==RC,DataSource.name==SN))
    if d:print(f"[OK] DS: {d.source_id}");return d
    d=DataSource(source_id=f"ds_{RC}_{int(_now().timestamp())}",source_scope="platform_public",tenant_code=None,asset_tenant_code=TC,managed_by="platform",source_type=ST,connector_type="manual_upload",name=SN,base_url="http://zjj.xam.gov.cn/",region_code=RC,data_domain=DT,format="pdf",downloadable=True,bucket="cost-raw",frequency="bimonthly",status="active",created_by="admin:xingan_register",config={"stable":{"site_id":f"cost_info.manual.{RC}","domain_type":DT,"region_code":CRC,"coverage_region_code":CRC,"publisher_scope":"city","publisher_region_code":CRC,"publisher_name":"兴安盟住房和城乡建设局"},"ops":{"source_audit_status":"人工补录"}})
    s.add(d);s.flush();print(f"[OK] DS: {d.source_id}");return d
def main():
    os.environ.setdefault("FILE_ASSET_DATABASE_URL","postgresql+psycopg://file_asset:file_asset@127.0.0.1:15432/file_asset")
    for k,v in[("FILE_ASSET_S3_ENDPOINT_URL","http://djtsoft.x3322.net:9000"),("FILE_ASSET_S3_ACCESS_KEY_ID","admin"),("FILE_ASSET_S3_SECRET_ACCESS_KEY","Admin@123456")]:os.environ.setdefault(k,v)
    init_db();store=get_object_store();factory=get_session_factory()
    with factory() as s:
        ds=ensure(s);s.commit();print()
        for i,iss in enumerate(ISSUES,1):
            print(f"--- [{i}/6] {iss['fn']} ---")
            fp=PDF_DIR/iss["fn"]
            if not fp.exists():print("  [SKIP]");continue
            reg=register_asset(s,storage=store,tenant_code=TC,source_type=ST,source_id=ds.source_id,batch_id=None,file_name=iss["fn"],content=fp.read_bytes(),source_url=iss["su"],source_item_key=f"manual:{CRC}:{iss['pr']}:{iss['fn']}",source_metadata={"channel_type":"manual_upload","collection_method":"manual_denovo","region_code":CRC,"period":iss["pr"],"download_site":"zjj.xam.gov.cn"},channel_type="manual_upload")
            s.commit();print(f"  [{('DUP' if reg.duplicated else 'NEW')}] {reg.file_id}")
            bk=build_cost_info_business_key(source_id=ds.source_id,region_code=CRC,period=iss["pr"],title=iss["title"])
            a,_=create_archive_from_ingest_event_with_flag(s,event_id=reg.ingest_event_id,domain_type=DT,channel_type="manual_upload",collection_method="manual_denovo",price_kind="guidance",period_kind=iss["pk"],title=iss["title"],visibility_scope="public",status="collected",business_key=bk,region_code=RC,publish_date=iss["pd"],metadata={k:{"value":iss[k]}|_mk() for k in["pr","ps","py"]}|{"coverage_region_code":{"value":CRC}|_mk(),"price_source_type":{"value":"info_price"}|_mk(),"publisher":{"value":"兴安盟住房和城乡建设局"}|_mk(),"publisher_scope":{"value":"city"}|_mk(),"publisher_region_code":{"value":CRC}|_mk()},field_sources={k:_mk() for k in["domain_type","channel_type","collection_method","business_key","title","region_code","publish_date","price_kind","period_kind"]},actor_type="user",actor_id="admin:xingan_register")
            a.coverage_period=iss["ps"];s.commit();print(f"  [OK] cp={a.coverage_period}");print()
    print("Done. 6/6")
if __name__=="__main__":main()
