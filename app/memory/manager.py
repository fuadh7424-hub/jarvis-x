"""Non-persistent memory interface for future implementations."""
from typing import Protocol


class MemoryManager(Protocol):
    def remember(self, key: str, value: str) -> None: ...
    def recall(self, key: str) -> str | None: ...


class InMemoryManager:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def remember(self, key: str, value: str) -> None:
        self._values[key] = value

    def recall(self, key: str) -> str | None:
        return self._values.get(key)
