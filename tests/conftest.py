"""grayroll-05 测试夹具：每个用例使用独立临时 SQLite 库，保证红/绿可复现、不污染 data/app.db。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def grayroll_db(tmp_path, monkeypatch):
    """指向全新播种的临时库，并返回已进入生命周期的 TestClient。"""
    import db

    tmp_data = tmp_path / "data"
    tmp_data.mkdir(parents=True, exist_ok=True)
    tmp_db = tmp_data / "grayroll05.db"
    monkeypatch.setattr(db, "DATA", tmp_data)
    monkeypatch.setattr(db, "DB_PATH", tmp_db)
    db.init_db()

    from app import app

    with TestClient(app) as client:
        yield client
