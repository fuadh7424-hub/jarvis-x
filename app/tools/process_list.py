"""A deterministic, read-only process inspection tool."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

import psutil
from pydantic import BaseModel, Field

from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class ProcessListInput(BaseModel):
    filter_name: str | None = Field(default=None, max_length=100, description="Optional case-insensitive process name filter")
    limit: int = Field(default=50, ge=1, le=200, description="Maximum number of processes to return (1-200)")
    sort_by: Literal["memory", "cpu", "name", "pid"] = Field(default="memory", description="Sort order: 'memory', 'cpu', 'name', or 'pid'")


class ProcessListTool(Tool[ProcessListInput]):
    """Return a sanitized, read-only list of running processes without sensitive command arguments."""

    name = "process_list"
    description = "Inspect running process names, PIDs, memory usage, and CPU usage safely without terminating or launching processes."
    input_model = ProcessListInput
    permission_level = PermissionLevel.SAFE

    async def execute(self, input_data: ProcessListInput) -> dict[str, object]:
        processes: list[dict[str, Any]] = []
        name_filter = input_data.filter_name.lower().strip() if input_data.filter_name else None

        # Inspect processes strictly using safe attributes (no cmdline, no environ)
        for proc in psutil.process_iter(["pid", "name", "status", "memory_percent", "cpu_percent", "create_time"]):
            try:
                info = proc.info
                p_name = info.get("name") or "unknown"
                if name_filter and name_filter not in p_name.lower():
                    continue

                created_raw = info.get("create_time")
                created_iso = (
                    datetime.fromtimestamp(created_raw, tz=timezone.utc).isoformat()
                    if created_raw
                    else None
                )

                processes.append({
                    "pid": info.get("pid"),
                    "name": p_name,
                    "status": info.get("status") or "unknown",
                    "cpu_percent": round(float(info.get("cpu_percent") or 0.0), 1),
                    "memory_percent": round(float(info.get("memory_percent") or 0.0), 3),
                    "created_at": created_iso,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue

        # Sort based on requested criteria
        if input_data.sort_by == "name":
            processes.sort(key=lambda x: str(x.get("name", "")).lower())
        elif input_data.sort_by == "pid":
            processes.sort(key=lambda x: int(x.get("pid") or 0))
        elif input_data.sort_by == "cpu":
            processes.sort(key=lambda x: float(x.get("cpu_percent") or 0.0), reverse=True)
        else:  # default "memory"
            processes.sort(key=lambda x: float(x.get("memory_percent") or 0.0), reverse=True)

        total_matched = len(processes)
        bounded_processes = processes[:input_data.limit]

        return {
            "total_matched": total_matched,
            "returned_count": len(bounded_processes),
            "processes": bounded_processes,
        }
