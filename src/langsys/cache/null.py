"""A cache that stores nothing — every read misses. Useful in tests or read-through-only setups."""

from __future__ import annotations

from typing import Any, Optional


class NullCache:
    def get(self, key: str) -> Optional[Any]:
        return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        return None

    def delete(self, key: str) -> None:
        return None

    def clear(self) -> None:
        return None
