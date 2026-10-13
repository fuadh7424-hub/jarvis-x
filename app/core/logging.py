"""Structured application logging."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.security.approvals import AuditEvent


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        return json.dumps(payload, default=str)


def configure_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    """Configure the dedicated JARVIS-X logger once and return it."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jarvis_x")
    logger.setLevel(level)
    logger.propagate = False
    log_path = log_dir / "jarvis-x.log"
    has_target_handler = any(
        Path(getattr(handler, "baseFilename", "")).resolve() == log_path.resolve()
        for handler in logger.handlers
    )
    if not has_target_handler:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def log_audit_event(event: AuditEvent) -> None:
    """Write a typed audit event through the structured application logger."""
    logging.getLogger("jarvis_x").info("audit_event", extra={"event": event.model_dump(mode="json")})
