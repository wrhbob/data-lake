import os

import pytest

import serve
from app import database


def test_startup_stops_immediately_when_nas_database_is_unreachable(monkeypatch):
    def unavailable():
        raise OSError("connection refused")

    monkeypatch.setattr(database, "init_db", unavailable)

    with pytest.raises(SystemExit, match="database unavailable: connection refused"):
        serve._check_database()


def test_startup_marks_schema_ready_after_successful_database_initialization(monkeypatch):
    monkeypatch.delenv("FILE_ASSET_SCHEMA_READY", raising=False)
    monkeypatch.setattr(database, "init_db", lambda: None)

    serve._check_database()

    assert os.environ["FILE_ASSET_SCHEMA_READY"] == "1"
