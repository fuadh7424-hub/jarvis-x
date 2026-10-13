"""JARVIS-X application entry point."""
from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)
    app = FastAPI(title="JARVIS-X", version="0.1.0")
    app.include_router(router)
    return app


app = create_app()
