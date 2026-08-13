"""Fetching and caching the translation catalog for a locale."""

from __future__ import annotations

from typing import Optional

from .cache.backend import CacheBackend
from .http import HttpClient, encode_segment
from .types import Catalog


class CatalogStore:
    """Loads ``category -> phrase -> translation`` maps, with a two-tier cache.

    Tier 1 is an in-process dict (fast, per-client); tier 2 is the pluggable
    :class:`CacheBackend` (survives processes). A miss falls through to nova and
    populates both tiers.
    """

    def __init__(
        self,
        http: HttpClient,
        project_id: str,
        cache: CacheBackend,
        *,
        ttl: int = 3600,
    ) -> None:
        self._http = http
        self._project_id = project_id
        self._cache = cache
        self._ttl = ttl
        self._memory: dict[str, Catalog] = {}

    def _key(self, locale: str) -> str:
        return f"translations_{self._project_id}_{locale}"

    def get(self, locale: str, *, use_cache: bool = True) -> Catalog:
        if use_cache:
            cached = self._memory.get(locale)
            if cached is not None:
                return cached
            persisted = self._cache.get(self._key(locale))
            if isinstance(persisted, dict):
                self._memory[locale] = persisted
                return persisted

        catalog = self._fetch(locale)
        self._memory[locale] = catalog
        self._cache.set(self._key(locale), catalog, self._ttl)
        return catalog

    def _fetch(self, locale: str) -> Catalog:
        response = self._http.get(
            "translations",
            params={"project_id": self._project_id, "locale": locale, "format": "flat"},
        )
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    def clear(self, locale: Optional[str] = None) -> None:
        if locale is None:
            self._memory.clear()
            self._cache.clear()
        else:
            self._memory.pop(locale, None)
            self._cache.delete(self._key(locale))

    # Kept for symmetry / future use; encodes a locale for a path segment.
    @staticmethod
    def _segment(locale: str) -> str:
        return encode_segment(locale)
