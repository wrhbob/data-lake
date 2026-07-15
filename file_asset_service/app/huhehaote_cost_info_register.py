"""One-shot: register 呼和浩特造价信息 PDFs from zfcxjsj.huhhot.gov.cn

Uses register_asset() + create_archive_from_ingest_event_with_flag() pipeline.

Before ingesting a new city, read:
  .claude/skills/cost-info-ingestion/SKILL.md
  .claude/skills/cost-info-ingestion/references/metadata-field-checklist.md
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
    r"D:\大匠通\新指标云\data_lake_handoff\data_lake_handoff\quota\内蒙古\造价信息"
)

ISSUES: list[dict] = [
    {
        "file_name": "2026年信息价第1期.pdf",
        "title": "呼和浩特市2026年信息价第1期",
        "period_raw": "2026年第1期",
        "period_kind": "issue_based",
        "period_start": None,       # issue_based — no month
        "period_year": "2026",
        "period_issue_no": "1",
        "publish_date": date(2026, 3, 31),
        "source_url": "http://zfcxjsj.huhhot.gov.cn/bsfw_91/xzzx/zjxx/202603/t20260331_1987221.html",
    },
    {
        "file_name": "2025年信息价第6期(11-12月).pdf",
        "title": "呼和浩特市2025年第六期（11-12月）建设工程造价信息",
        "period_raw": "2025年第6期(11-12月)",
        "period_kind": "bimonthly",
        "period_start": "2025-11",
        "period_year": "2025",
        "period_issue_no": None,
        "publish_date": date(2026, 1, 15),
        "source_url": "http://zfcxjsj.huhhot.gov.cn/bsfw_91/xzzx/zjxx/202601/t20260115_1968824.html",
    },
    {
        "file_name": "2025年信息价第5期(9-10月).pdf",
        "title": "呼和浩特市2025年第五期（9-10月）建设工程造价信息",
        "period_raw": "2025年第5期(9-10月)",
        "period_kind": "bimonthly",
        "period_start": "2025-09",
        "period_year": "2025",
        "period_issue_no": None,
        "publish_date": date(2025, 11, 13),
        "source_url": "http://zfcxjsj.huhhot.gov.cn/bsfw_91/xzzx/zjxx/202511/t20251113_1949891.html",
    },
    {
        "file_name": "2025年信息价第4期(7-8月).pdf",
        "title": "呼和浩特市2025年第4期（7-8月）建设工程造价信息",
        "period_raw": "2025年第4期(7-8月)",
        "period_kind": "bimonthly",
        "period_start": "2025-07",
        "period_year": "2025",
        "period_issue_no": None,
        "publish_date": date(2025, 9, 15),
        "source_url": "http://zfcxjsj.huhhot.gov.cn/bsfw_91/xzzx/zjxx/202509/t20250915_1931882.html",
    },
    {
        "file_name": "2025年信息价第3期(5-6月).pdf",
        "title": "呼和浩特市2025年第3期（5-6月）建设工程造价信息",
        "period_raw": "2025年第3期(5-6月)",
        "period_kind": "bimonthly",
        "period_start": "2025-05",
        "period_year": "2025",
        "period_issue_no": None,
        "publish_date": date(2025, 7, 21),
        "source_url": "http://zfcxjsj.huhhot.gov.cn/bsfw_91/xzzx/zjxx/202507/t20250721_1913377.html",
    },
    {
        "file_name": "2025年信息价第2期(3-4月).pdf",
        "title": "呼和浩特市2025年第2期（3-4月）建设工程造价信息",
        "period_raw": "2025年第2期(3-4月)",
        "period_kind": "bimonthly",
        "period_start": "2025-03",
        "period_year": "2025",
        "period_issue_no": None,
        "publish_date": date(2025, 5, 19),
        "source_url": "http://zfcxjsj.huhhot.gov.cn/bsfw_91/xzzx/zjxx/202505/t20250519_1891347.html",
    },
]

REGION_CODE = "150100"  # 呼和浩特市 6-digit GB/T 2260 (must match regionTree city code)
COVERAGE_REGION_CODE = "150100"  # 呼和浩特市本级
SOURCE_NAME = "呼和浩特市住建局-造价信息-人工补录"
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
        source_id=_now_iso()[:19].replace("-", "").replace(":", ""),
        source_scope="platform_public",
        tenant_code=None,
        asset_tenant_code=TENANT_CODE,
        managed_by="platform",
        source_type=SOURCE_TYPE,
        connector_type="manual_upload",
        name=SOURCE_NAME,
        base_url="http://zfcxjsj.huhhot.gov.cn/bsfw_91/xzzx/zjxx/",
        region_code=REGION_CODE,
        data_domain=DOMAIN_TYPE,
        format="pdf",
        downloadable=True,
        bucket="cost-raw",
        frequency="bimonthly",
        status="active",
        created_by="admin:huhehaote_register",
        config={
            "stable": {
                "site_id": f"cost_info.manual.{REGION_CODE}",
                "domain_type": DOMAIN_TYPE,
                "region_code": COVERAGE_REGION_CODE,
                "coverage_region_code": COVERAGE_REGION_CODE,
                "publisher_scope": "city",
                "publisher_region_code": COVERAGE_REGION_CODE,
                "publisher_name": "呼和浩特市住房和城乡建设局",
            },
            "ops": {"source_audit_status": "人工补录"},
        },
    )
    session.add(ds)
    session.flush()
    print(f"[OK] DataSource created: {ds.source_id}")
    return ds


def mk_field_source() -> dict:
    return {"source_level": "manual", "tagged_by": "script:huhehaote_register", "tagged_at": _now_iso()}


def main() -> None:
    # 从仓库根 .env 加载 DB/S3 凭据；源码不硬编码任何密钥。
    try:
        from dotenv import load_dotenv

        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        load_dotenv(os.path.join(_root, ".env"), override=False)
    except Exception:
        pass
    print(f"DB: {get_settings().database_url}")
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

            # Step 1: register_asset (MinIO + Blob + FileAsset + IngestEvent)
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
                    "download_site": "zfcxjsj.huhhot.gov.cn",
                },
                channel_type="manual_upload",
            )
            session.commit()
            dupe = "DUP" if reg.duplicated else "NEW"
            print(f"  [{dupe}] {reg.file_id}  sha256={reg.sha256[:12]}")

            # Step 2: Archive
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
                    "period_start": {"value": issue.get("period_start")} | mk_field_source(),
                    "period_year": {"value": issue.get("period_year")} | mk_field_source(),
                    "period_issue_no": {"value": issue.get("period_issue_no")} | mk_field_source(),
                    "coverage_region_code": {"value": COVERAGE_REGION_CODE} | mk_field_source(),
                    "price_source_type": {"value": "info_price"} | mk_field_source(),
                    "publisher": {"value": "呼和浩特市住房和城乡建设局"} | mk_field_source(),
                    "publisher_scope": {"value": "city"} | mk_field_source(),
                    "publisher_region_code": {"value": COVERAGE_REGION_CODE} | mk_field_source(),
                },
                field_sources={k: mk_field_source() for k in [
                    "domain_type", "channel_type", "collection_method",
                    "business_key", "title", "region_code", "publish_date",
                    "price_kind", "period_kind",
                ]},
                actor_type="user",
                actor_id="admin:huhehaote_register",
            )
            session.commit()
            print(f"  [OK]  Archive: {archive.archive_id}")
            print()

            results.append({"issue": issue, "archive_id": archive.archive_id, "sha256": reg.sha256})

    print("=" * 60)
    print(f"Done. {len(results)} of {len(ISSUES)} registered.")
    for r in results:
        print(f"  {r['issue']['file_name']}  →  {r['archive_id']}")


if __name__ == "__main__":
    main()
