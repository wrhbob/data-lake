from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db_session
from app.main import create_app
from app.models import Base


def build_client(tmp_path: Path):
    regions = tmp_path / "regions.csv"
    audit = tmp_path / "audit.csv"
    regions.write_text(
        "admin_division_version,province_code,province_name,target_region_code,target_region_name,target_level,is_required,source_note\n"
        "mca_geoname_2026_snapshot,510000,四川省,510300,自贡市,prefecture,1,地级市\n",
        encoding="utf-8",
    )
    audit.write_text(
        "admin_division_version,province_code,province_name,target_region_code,target_region_name,target_level,"
        "source_status,site_id,source_name,publisher_name,publisher_scope,entry_url,evidence_url,"
        "source_attachment_mode,publication_mode,adapter_kind,crawl_pattern,price_kind,period_kind,"
        "blocked_reason,manual_path,audit_note,review_status\n"
        "mca_geoname_2026_snapshot,510000,四川省,510300,自贡市,prefecture,"
        "auto_crawl_ready,cost_info.sc.zigong,自贡市信息价,自贡市住建局,city,"
        "https://example.com/zigong,https://example.com/zigong,"
        "zip_package,DIRECT_WEB,sichuan_pdf,li,guidance,monthly,,,公开附件,accepted\n",
        encoding="utf-8",
    )
    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    app = create_app(init_schema=False)
    app.state.national_cost_info_regions_path = regions
    app.state.national_info_price_audit_path = audit

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def test_national_completeness_api_returns_summary(tmp_path):
    client = build_client(tmp_path)

    response = client.get("/api/info-price/national-completeness")

    assert response.status_code == 200
    body = response.json()
    assert body["target_count"] == 1
    assert body["audited_target_count"] == 1
    assert body["is_complete"] is True
    assert body["by_source_status"] == {"auto_crawl_ready": 1}
