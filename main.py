"""JARVIS-X application entry point."""
from fastapi import FastAPI

from app.agent.agent import Agent
from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.storage.sqlite import SQLiteRepository
from app.security.authentication import BearerTokenAuthenticator
from app.tools.application_list import ApplicationListTool
from app.tools.browser_active_tab import ActiveTabTool
from app.tools.browser_click import BrowserClickTool
from app.tools.browser_navigate import BrowserNavigateTool
from app.tools.browser_new_tab import BrowserNewTabTool
from app.tools.browser_page_metadata import PageMetadataTool
from app.tools.browser_status import BrowserStatusTool
from app.tools.browser_tabs import BrowserTabsTool
from app.tools.browser_type import BrowserTypeTool
from app.tools.desktop_active_window import DesktopActiveWindowTool
from app.tools.desktop_displays import DesktopDisplaysTool
from app.tools.desktop_hotkey import DesktopHotkeyTool
from app.tools.desktop_keyboard_type import DesktopKeyboardTypeTool
from app.tools.desktop_mouse_click import DesktopMouseClickTool
from app.tools.desktop_mouse_move import DesktopMouseMoveTool
from app.tools.desktop_window_focus import DesktopWindowFocusTool
from app.tools.desktop_window_move_resize import DesktopWindowMoveResizeTool
from app.tools.desktop_window_state import DesktopWindowStateTool
from app.tools.desktop_windows import DesktopWindowsTool
from app.tools.executor import Executor
from app.tools.process_list import ProcessListTool
from app.tools.registry import ToolRegistry
from app.tools.resource_metrics import ResourceMetricsTool
from app.tools.system_info import SystemInfoTool
from app.tools.system_time import SystemTimeTool
from app.tools.text_statistics import TextStatisticsTool


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)
    app = FastAPI(title="JARVIS-X", version="0.10.0")
    registry = ToolRegistry()
    registry.register(TextStatisticsTool())
    registry.register(SystemInfoTool())
    registry.register(SystemTimeTool())
    registry.register(ResourceMetricsTool())
    registry.register(ProcessListTool())
    registry.register(ApplicationListTool())
    registry.register(BrowserStatusTool())
    registry.register(BrowserTabsTool())
    registry.register(ActiveTabTool())
    registry.register(PageMetadataTool())
    registry.register(BrowserNavigateTool())
    registry.register(BrowserClickTool())
    registry.register(BrowserTypeTool())
    registry.register(BrowserNewTabTool())
    registry.register(DesktopWindowsTool())
    registry.register(DesktopActiveWindowTool())
    registry.register(DesktopDisplaysTool())
    registry.register(DesktopWindowFocusTool())
    registry.register(DesktopWindowStateTool())
    registry.register(DesktopWindowMoveResizeTool())
    registry.register(DesktopMouseMoveTool())
    registry.register(DesktopMouseClickTool())
    registry.register(DesktopKeyboardTypeTool())
    registry.register(DesktopHotkeyTool())
    app.state.executor = Executor(registry, SQLiteRepository(settings.database_path), approval_ttl_seconds=settings.approval_ttl_seconds)
    app.state.authenticator = BearerTokenAuthenticator(settings.auth_tokens)
    app.state.agent = Agent(app.state.executor)
    app.include_router(router)
    return app


app = create_app()
