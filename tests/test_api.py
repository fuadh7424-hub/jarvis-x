from fastapi.testclient import TestClient

from app.core.config import get_settings
from main import create_app


def create_test_app(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(tmp_path / "jarvis.sqlite3"))
    get_settings.cache_clear()
    return create_app()


def test_health_endpoint(tmp_path, monkeypatch):
    response = TestClient(create_test_app(tmp_path, monkeypatch)).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "jarvis-x"}


def test_read_only_tool_execution_endpoint(tmp_path, monkeypatch):
    client = TestClient(create_test_app(tmp_path, monkeypatch))

    response = client.post("/actions/text_statistics/execute", json={"input": {"text": "one two\nthree"}})

    assert response.status_code == 200
    assert response.json() == {"character_count": 13, "word_count": 3, "line_count": 2}
