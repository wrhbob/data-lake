import json
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import database
from app.models import AdministrativeDivision, Base


EXCLUDED_CODES = {"710000", "810000", "820000"}
ROOT = Path(__file__).parents[1]


def test_region_configuration_uses_mainland_scope_only():
    division_seed = json.loads((ROOT / "app" / "data" / "administrative_divisions.json").read_text(encoding="utf-8"))
    upload_regions = json.loads((ROOT / "app" / "ui" / "regions.json").read_text(encoding="utf-8"))
    app_js = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
    quota_ui = (ROOT / "app" / "ui" / "quota-ui.js").read_text(encoding="utf-8")

    assert EXCLUDED_CODES.isdisjoint({item["code"] for item in division_seed["items"]})
    assert EXCLUDED_CODES.isdisjoint({item["code"] for item in upload_regions})
    for code in EXCLUDED_CODES:
        assert f'value: "{code[:2]}"' not in app_js
        assert f'code:"{code}"' not in quota_ui


def test_administrative_division_migration_disables_legacy_excluded_regions():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, future=True)
    with session_factory() as session:
        session.add(
            AdministrativeDivision(
                code="810000",
                name="香港特别行政区",
                level="province",
                parent_code=None,
                enabled=True,
                version="legacy",
                sort_order=33,
            )
        )
        session.commit()

    database.migrate_administrative_division_table(engine)

    with session_factory() as session:
        legacy_region = session.scalar(
            select(AdministrativeDivision).where(AdministrativeDivision.code == "810000")
        )
        assert legacy_region is not None
        assert legacy_region.enabled is False
        assert legacy_region.name == "香港特别行政区"
