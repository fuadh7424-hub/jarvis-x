from fastapi.testclient import TestClient

from main import create_app


def test_health_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "jarvis-x"}


def test_read_only_tool_execution_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))
    client = TestClient(create_app())

    response = client.post("/actions/text_statistics/execute", json={"input": {"text": "one two\nthree"}})

    assert response.status_code == 200
    assert response.json() == {"character_count": 13, "word_count": 3, "line_count": 2}
