"""In-process cache. Cleared when the process exits; ideal as the fast first tier."""

from __future__ import annotations

import time
from typing import Any, Optional


class MemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires and expires < time.time():
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        expires = time.time() + ttl if ttl > 0 else 0.0
        self._store[key] = (expires, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()
