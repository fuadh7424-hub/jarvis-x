import pytest

from app.core.config import Settings


def test_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_ENVIRONMENT", "test")
    monkeypatch.setenv("JARVIS_LOG_LEVEL", "debug")
    settings = Settings.from_environment()
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"


def test_database_path_rejects_relative_parent_traversal(monkeypatch):
    monkeypatch.setenv("JARVIS_DATABASE_PATH", "../outside.sqlite3")
    with pytest.raises(ValueError, match="parent traversal"):
        Settings.from_environment()
