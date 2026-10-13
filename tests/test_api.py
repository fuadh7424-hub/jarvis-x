from fastapi.testclient import TestClient

from main import create_app


def test_health_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "jarvis-x"}
