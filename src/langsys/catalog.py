"""Fetching and caching the translation catalog for a locale."""

from __future__ import annotations

from typing import Optional

from ._log import logger
from .cache.backend import CacheBackend
from .exceptions import ApiError, NetworkError
from .http import HttpClient, encode_segment
from .locale import normalize_locale
from .types import Catalog


class CatalogFetch:
    """A catalog, plus what the caller needs to know about *how* it was obtained.

    ``ok`` is the WIRE-4 distinction: an empty catalog because the phrase is genuinely
    new looks identical to an empty catalog because the API was unreachable, and
    treating the second as the first turns every outage into a write storm.
    """

    __slots__ = ("catalog", "ok", "write_enabled")

    def __init__(
        self, catalog: Catalog, ok: bool, write_enabled: Optional[bool] = None
    ) -> None:
        #: ``category -> phrase -> translation``. Empty when ``ok`` is False.
        self.catalog = catalog
        #: False when the fetch failed. Never queue registrations off a False.
        self.ok = ok
        #: GATE-1 — the envelope-level flag, when this response carried one.
        #: ``None`` means the value was absent or this came from cache; it is a
        #: per-response value and is deliberately never stored anywhere.
        self.write_enabled = write_enabled


class CatalogStore:
    """Loads ``category -> phrase -> translation`` maps, with a two-tier cache.

    Tier 1 is an in-process dict (fast, per-client); tier 2 is the pluggable
    :class:`CacheBackend` (survives processes). A miss falls through to nova and
    populates both tiers. A *failed* fetch populates neither — caching an outage
    would serve source text for the whole TTL, fleet-wide on a shared store.

    Locales are keyed and sent in the lowercase ``xx-yy`` wire form (WIRE-3), so
    ``en-US`` and ``en-us`` are one cache entry rather than two fetches.
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
        return f"translations_{self._project_id}_{normalize_locale(locale)}"

    def get(self, locale: str, *, use_cache: bool = True) -> CatalogFetch:
        wire_locale = normalize_locale(locale)
        if use_cache:
            cached = self._memory.get(wire_locale)
            if cached is not None:
                return CatalogFetch(cached, ok=True)
            persisted = self._cache.get(self._key(wire_locale))
            if isinstance(persisted, dict):
                self._memory[wire_locale] = persisted
                return CatalogFetch(persisted, ok=True)

        fetched = self._fetch(wire_locale)
        if not fetched.ok:
            return fetched
        self._memory[wire_locale] = fetched.catalog
        self._cache.set(self._key(wire_locale), fetched.catalog, self._ttl)
        return fetched

    def _fetch(self, wire_locale: str) -> CatalogFetch:
        try:
            response = self._http.get(
                "translations",
                params={
                    "project_id": self._project_id,
                    # WIRE-3 — lowercase on the wire. The deprecated translations
                    # route sits outside the normalising middleware, so relying on
                    # the server to fold case is relying on a normalisation we do
                    # not control, on the one route least likely to apply it.
                    "locale": wire_locale,
                    "format": "flat",
                },
            )
        except (NetworkError, ApiError) as exc:
            # WIRE-4 — we sit in the request path; our dependency being down must
            # not convert a working page into a 500.
            logger.warning(
                "langsys: catalog fetch failed for %s (%s) — serving source text and "
                "registering nothing this pass.",
                wire_locale,
                exc,
            )
            return CatalogFetch({}, ok=False)

        data = response.get("data")
        write_enabled = response.get("write_enabled")
        return CatalogFetch(
            data if isinstance(data, dict) else {},
            ok=True,
            write_enabled=write_enabled if isinstance(write_enabled, bool) else None,
        )

    def clear(self, locale: Optional[str] = None) -> None:
        if locale is None:
            self._memory.clear()
            self._cache.clear()
        else:
            wire_locale = normalize_locale(locale)
            self._memory.pop(wire_locale, None)
            self._cache.delete(self._key(wire_locale))

    # Kept for symmetry / future use; encodes a locale for a path segment.
    @staticmethod
    def _segment(locale: str) -> str:
        return encode_segment(locale)
