"""Explicit default-deny permission policy."""
from enum import Enum


class PermissionLevel(str, Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


class PermissionPolicy:
    @staticmethod
    def allows(level: PermissionLevel, approved: bool = False) -> bool:
        if level is PermissionLevel.BLOCKED:
            return False
        if level is PermissionLevel.CONFIRM:
            return approved
        return True

    @staticmethod
    def requires_approval(level: PermissionLevel) -> bool:
        return level is PermissionLevel.CONFIRM
