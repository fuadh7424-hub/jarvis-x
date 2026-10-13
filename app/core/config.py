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

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            environment=os.getenv("JARVIS_ENVIRONMENT", "development"),
            log_level=os.getenv("JARVIS_LOG_LEVEL", "INFO").upper(),
            log_dir=Path(os.getenv("JARVIS_LOG_DIR", "logs")),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()

