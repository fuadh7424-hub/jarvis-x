"""A deterministic, read-only system inspection tool."""
from __future__ import annotations

import os
import platform
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class SystemInfoInput(BaseModel):
    include_resources: bool = Field(default=True, description="Whether to include safe resource counts")


class SystemInfoTool(Tool[SystemInfoInput]):
    """Return non-sensitive system and platform metadata without external state modification."""

    name = "system_info"
    description = "Inspect local system time, basic platform information, and CPU count safely."
    input_model = SystemInfoInput
    permission_level = PermissionLevel.SAFE

    async def execute(self, input_data: SystemInfoInput) -> dict[str, object]:
        info: dict[str, object] = {
            "current_time_utc": datetime.now(timezone.utc).isoformat(),
            "os_name": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "architecture": platform.architecture()[0],
            "processor": platform.processor(),
            "hostname": platform.node(),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        }
        if input_data.include_resources:
            info["cpu_count"] = os.cpu_count()
        return info
