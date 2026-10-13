"""JARVIS-X application entry point."""
from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.security.approvals import ApprovalManager
from app.tools.executor import Executor
from app.tools.registry import ToolRegistry
from app.tools.text_statistics import TextStatisticsTool


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)
    app = FastAPI(title="JARVIS-X", version="0.1.0")
    registry = ToolRegistry()
    registry.register(TextStatisticsTool())
    app.state.executor = Executor(registry, ApprovalManager())
    app.include_router(router)
    return app


app = create_app()
