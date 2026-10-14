"""A deterministic, read-only resource metrics inspection tool."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import psutil
from pydantic import BaseModel, Field, field_validator

from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class ResourceMetricsInput(BaseModel):
    disk_path: str = Field(default="C:\\", max_length=100, description="Mount path or volume letter to inspect disk usage for")

    @field_validator("disk_path")
    @classmethod
    def validate_disk_path(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("disk_path must not be empty")
        if "\x00" in clean:
            raise ValueError("disk_path must not contain null characters")
        if ".." in Path(clean).parts:
            raise ValueError("disk_path must not contain parent directory traversal")
        return clean


class ResourceMetricsTool(Tool[ResourceMetricsInput]):
    """Return CPU, virtual memory, and volume disk utilization without state modification."""

    name = "resource_metrics"
    description = "Inspect CPU usage, system memory statistics, and volume disk usage safely."
    input_model = ResourceMetricsInput
    permission_level = PermissionLevel.SAFE

    async def execute(self, input_data: ResourceMetricsInput) -> dict[str, object]:
        # 1. CPU Metrics
        try:
            cpu_usage: float | None = psutil.cpu_percent(interval=0.05)
        except Exception:
            cpu_usage = None

        cpu_data: dict[str, Any] = {
            "logical_cpu_count": os.cpu_count(),
            "cpu_usage_percent": cpu_usage,
        }

        # 2. Virtual Memory Metrics
        try:
            mem = psutil.virtual_memory()
            memory_data: dict[str, Any] = {
                "total_mb": round(mem.total / (1024 * 1024), 2),
                "available_mb": round(mem.available / (1024 * 1024), 2),
                "used_mb": round(mem.used / (1024 * 1024), 2),
                "percent_used": mem.percent,
            }
        except Exception as exc:
            memory_data = {"error": "memory_metrics_unavailable", "detail": str(exc.__class__.__name__)}

        # 3. Disk Metrics (using standard library shutil.disk_usage)
        target_path = input_data.disk_path
        try:
            disk = shutil.disk_usage(target_path)
            disk_data: dict[str, Any] = {
                "path": target_path,
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
                "percent_used": round((disk.used / disk.total) * 100, 1) if disk.total > 0 else 0.0,
            }
        except (OSError, ValueError):
            disk_data = {
                "path": target_path,
                "error": "volume_unavailable",
            }

        return {
            "cpu": cpu_data,
            "memory": memory_data,
            "disk": disk_data,
        }
