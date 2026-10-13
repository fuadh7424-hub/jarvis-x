"""Environment-based application configuration."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Non-secret runtime settings loaded from environment variables."""

    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    log_dir: Path = Field(default=Path("logs"))
    database_path: Path = Field(default=Path("data/jarvis-x.sqlite3"))
    approval_ttl_seconds: int = Field(default=300, ge=1, le=86_400)

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            environment=os.getenv("JARVIS_ENVIRONMENT", "development"),
            log_level=os.getenv("JARVIS_LOG_LEVEL", "INFO").upper(),
            log_dir=Path(os.getenv("JARVIS_LOG_DIR", "logs")),
            database_path=_database_path(os.getenv("JARVIS_DATABASE_PATH", "data/jarvis-x.sqlite3")),
            approval_ttl_seconds=int(os.getenv("JARVIS_APPROVAL_TTL_SECONDS", "300")),
        )


def _database_path(value: str) -> Path:
    """Reject relative traversal; deployment configuration remains the trust boundary."""
    path = Path(value)
    if not path.is_absolute() and ".." in path.parts:
        raise ValueError("JARVIS_DATABASE_PATH must not contain parent traversal")
    return path


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()

