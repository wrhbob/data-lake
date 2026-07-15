"""乌兰察布 bimonthly info price XLS from zj.wulanchabu.gov.cn"""
from __future__ import annotations
import os, sys; from datetime import date, datetime, timezone; from pathlib import Path
def _p(): h=Path(__file__).resolve().parent.parent; str(h) not in sys.path and sys.path.insert(0,str(h))
_p()
from sqlalchemy import select; from sqlalchemy.orm import Session
from app.assets import register_asset
from app.archive_rules import build_cost_info_business_key
from app.archive_service import create_archive_from_ingest_event_with_flag
from app.database import get_session_factory, init_db
from app.models import DataSource; from app.storage import get_object_store

PDF_DIR = Path(r"D:\大匠通\新指标云\data_lake_handoff\data_lake_handoff\quota\内蒙古\乌兰察布\造价信息")
ISSUES = [
    {"fn":"2026年3-4月乌兰察布材料信息价.xls","title":"乌兰察布市2026年三、四月份材料信息价",
     "pr":"2026年3-4月","ps":"2026-03","py":"2026","pk":"monthly","pd":date(2026,5,20),
     "su":"http://zj.wulanchabu.gov.cn/information/zjxxw11726/msg1788158633119.html"},
    {"fn":"2026年1-2月乌兰察布材料信息价.xls","title":"乌兰察布市2026年一、二月份材料信息价",
     "pr":"2026年1-2月","ps":"2026-01","py":"2026","pk":"monthly","pd":date(2026,3,25),
     "su":"http://zj.wulanchabu.gov.cn/information/zjxxw11726/msg1788158633116.html"},
    {"fn":"2025年11-12月乌兰察布材料信息价.xls","title":"乌兰察布市2025年十一、十二月份材料信息价",
     "pr":"2025年11-12月","ps":"2025-11","py":"2025","pk":"monthly","pd":date(2026,1,7),
     "su":"http://zj.wulanchabu.gov.cn/information/zjxxw11726/msg1788158633081.html"},
    {"fn":"2025年9-10月乌兰察布材料信息价.xls","title":"乌兰察布市2025年九、十月份材料信息价",
     "pr":"2025年9-10月","ps":"2025-09","py":"2025","pk":"monthly","pd":date(2025,12,2),
     "su":"http://zj.wulanchabu.gov.cn/information/zjxxw11726/msg1788158633078.html"},
    {"fn":"2025年7-8月乌兰察布材料信息价.xls","title":"乌兰察布市2025年七、八月份材料信息价",
     "pr":"2025年7-8月","ps":"2025-07","py":"2025","pk":"monthly","pd":date(2025,9,12),
     "su":"http://zj.wulanchabu.gov.cn/information/zjxxw11726/msg1788158633068.html"},
    {"fn":"2025年5-6月乌兰察布材料信息价.xls","title":"乌兰察布市2025年五、六月份材料信息价",
     "pr":"2025年5-6月","ps":"2025-05","py":"2025","pk":"monthly","pd":date(2025,7,11),
     "su":"http://zj.wulanchabu.gov.cn/information/zjxxw11726/msg1788158633061.html"},
]
RC="150900"; CRC=RC; SN="乌兰察布市住建局-造价信息-人工补录"
ST="info_price"; DT="cost_info"; TC="platform_public"
_now=lambda:datetime.now(timezone.utc)
_mk=lambda:{"source_level":"manual","tagged_by":"script:wulanchabu_register","tagged_at":_now().isoformat()}

def ensure(s):
    d=s.scalar(select(DataSource).where(DataSource.source_type==ST,DataSource.region_code==RC,DataSource.name==SN))
    if d:print(f"[OK] DS: {d.source_id}");return d
    d=DataSource(source_id=f"ds_{RC}_{int(_now().timestamp())}",source_scope="platform_public",tenant_code=None,asset_tenant_code=TC,managed_by="platform",source_type=ST,connector_type="manual_upload",name=SN,base_url="http://zj.wulanchabu.gov.cn/",region_code=RC,data_domain=DT,format="xls",downloadable=True,bucket="cost-raw",frequency="bimonthly",status="active",created_by="admin:wulanchabu_register",config={"stable":{"site_id":f"cost_info.manual.{RC}","domain_type":DT,"region_code":CRC,"coverage_region_code":CRC,"publisher_scope":"city","publisher_region_code":CRC,"publisher_name":"乌兰察布市住房和城乡建设局"},"ops":{"source_audit_status":"人工补录"}})
    s.add(d);s.flush();print(f"[OK] DS: {d.source_id}");return d

def main():
    init_db();store=get_object_store();factory=get_session_factory()
    with factory() as s:
        ds=ensure(s);s.commit();print()
        for i,iss in enumerate(ISSUES,1):
            print(f"--- [{i}/6] {iss['fn']} ---")
            fp=PDF_DIR/iss["fn"]
            if not fp.exists():print("  [SKIP]");continue
            reg=register_asset(s,storage=store,tenant_code=TC,source_type=ST,source_id=ds.source_id,batch_id=None,file_name=iss["fn"],content=fp.read_bytes(),source_url=iss["su"],source_item_key=f"manual:{CRC}:{iss['pr']}:{iss['fn']}",source_metadata={"channel_type":"manual_upload","collection_method":"manual_denovo","region_code":CRC,"period":iss["pr"],"download_site":"zj.wulanchabu.gov.cn"},channel_type="manual_upload")
            s.commit();print(f"  [{('DUP' if reg.duplicated else 'NEW')}] {reg.file_id}")
            bk=build_cost_info_business_key(source_id=ds.source_id,region_code=CRC,period=iss["pr"],title=iss["title"])
            a,_=create_archive_from_ingest_event_with_flag(s,event_id=reg.ingest_event_id,domain_type=DT,channel_type="manual_upload",collection_method="manual_denovo",price_kind="guidance",period_kind=iss["pk"],title=iss["title"],visibility_scope="public",status="collected",business_key=bk,region_code=RC,publish_date=iss["pd"],metadata={k:{"value":iss[k]}|_mk() for k in["pr","ps","py"]}|{"coverage_region_code":{"value":CRC}|_mk(),"price_source_type":{"value":"info_price"}|_mk(),"publisher":{"value":"乌兰察布市住房和城乡建设局"}|_mk(),"publisher_scope":{"value":"city"}|_mk(),"publisher_region_code":{"value":CRC}|_mk()},field_sources={k:_mk() for k in["domain_type","channel_type","collection_method","business_key","title","region_code","publish_date","price_kind","period_kind"]},actor_type="user",actor_id="admin:wulanchabu_register")
            a.coverage_period=iss["ps"];s.commit();print(f"  [OK] cp={a.coverage_period}");print()
    print("Done. 6/6")

if __name__=="__main__":main()
