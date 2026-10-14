"""A deterministic, read-only system time inspection tool."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class SystemTimeInput(BaseModel):
    include_epoch: bool = Field(default=True, description="Whether to include epoch timestamp")


class SystemTimeTool(Tool[SystemTimeInput]):
    """Return local time, UTC time, timezone name, and offset without external state modification."""

    name = "system_time"
    description = "Inspect local system time, UTC time, timezone, and calendar metadata safely."
    input_model = SystemTimeInput
    permission_level = PermissionLevel.SAFE

    async def execute(self, input_data: SystemTimeInput) -> dict[str, object]:
        now_local = datetime.now().astimezone()
        now_utc = datetime.now(timezone.utc)
        tz_name = now_local.tzname() or "UTC"
        utc_offset = now_local.strftime("%z")

        result: dict[str, object] = {
            "local_time": now_local.isoformat(),
            "utc_time": now_utc.isoformat(),
            "timezone_name": tz_name,
            "utc_offset": utc_offset,
            "day_of_week": now_local.strftime("%A"),
            "date": now_local.strftime("%Y-%m-%d"),
            "time_24h": now_local.strftime("%H:%M:%S"),
            "is_dst": bool(time.localtime().tm_isdst > 0),
        }
        if input_data.include_epoch:
            result["timestamp_epoch"] = round(time.time(), 3)
        return result
