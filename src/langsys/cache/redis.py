"""Redis cache backend (``pip install langsys[redis]``).

Shares the catalog across processes/hosts. Values are JSON-encoded. Pass an existing
``redis.Redis`` client, or connection options to build one.
"""

from __future__ import annotations

import json
from typing import Any, Optional


class RedisCache:
    def __init__(
        self,
        client: Optional[Any] = None,
        *,
        prefix: str = "langsys:",
        **options: Any,
    ) -> None:
        if client is None:
            try:
                import redis  # noqa: PLC0415 - optional dependency, imported lazily
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "RedisCache requires the 'redis' package. "
                    "Install with: pip install langsys[redis]"
                ) from exc
            client = redis.Redis(**options)
        self._redis = client
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> Optional[Any]:
        raw = self._redis.get(self._key(key))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        if ttl > 0:
            self._redis.setex(self._key(key), ttl, payload)
        else:
            self._redis.set(self._key(key), payload)

    def delete(self, key: str) -> None:
        self._redis.delete(self._key(key))

    def clear(self) -> None:
        keys = self._redis.keys(f"{self._prefix}*")
        if keys:
            self._redis.delete(*keys)
