import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.storage import FakeObjectStore


@pytest.fixture(autouse=True)
def configured_shared_services(monkeypatch):
    """Keep unit tests explicit about the production-only service topology.

    Tests inject SQLite sessions and FakeObjectStore instances themselves, but
    application helpers still read settings for bucket names. Supply inert NAS
    shaped endpoints here so no test depends on a local runtime fallback.
    """
    monkeypatch.setenv("FILE_ASSET_DATABASE_URL", "postgresql+psycopg://file_asset:test@nas.example:15432/file_asset")
    monkeypatch.setenv("FILE_ASSET_S3_ENDPOINT_URL", "http://nas.example:9000")
    monkeypatch.setenv("FILE_ASSET_S3_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("FILE_ASSET_S3_SECRET_ACCESS_KEY", "test-secret-key")


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with Session() as session:
        yield session


@pytest.fixture()
def fake_storage():
    return FakeObjectStore()
