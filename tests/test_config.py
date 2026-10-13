from app.core.config import Settings


def test_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_ENVIRONMENT", "test")
    monkeypatch.setenv("JARVIS_LOG_LEVEL", "debug")
    settings = Settings.from_environment()
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
