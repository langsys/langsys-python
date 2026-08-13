"""The cache backend contract. Any object implementing this can be passed to the client."""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class CacheBackend(Protocol):
    def get(self, key: str) -> Optional[Any]:
        """Return the cached value, or ``None`` on miss/expiry."""
        ...

    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        """Store ``value`` under ``key`` for ``ttl`` seconds (``0`` = no expiry)."""
        ...

    def delete(self, key: str) -> None:
        """Remove ``key`` (no error if absent)."""
        ...

    def clear(self) -> None:
        """Drop everything this backend owns."""
        ...
