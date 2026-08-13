"""The public entry point: :class:`LangsysClient`."""

from __future__ import annotations

import atexit
from typing import Any, Optional, Sequence

from ._log import logger
from .cache.backend import CacheBackend
from .cache.file import FileCache
from .catalog import CatalogStore
from .config import Config
from .exceptions import AuthorizationError, ConfigurationError
from .html.attributes import DEFAULT_TRANSLATABLE_ATTRIBUTES
from .http import HttpClient, encode_segment
from .interpolate import interpolate
from .locale import canonicalize_locale, detect_preferred_locale
from .observable import LocaleSource, Signal
from .registration import PhraseInput, Registrar, generate_custom_id
from .translate import resolve
from .types import (
    UNCATEGORIZED,
    Catalog,
    Country,
    Currency,
    DialCode,
    KeyType,
    LocaleFlat,
    LocaleInfo,
    Project,
)
from .utilities import Utilities


class LangsysClient:
    """Talk to Langsys: fetch translations and render phrases.

    The phrase in your code is the lookup key **and** the base-language default —
    there is no keys file. Untranslated phrases render as the source phrase.

    ::

        client = LangsysClient(api_key="…", project_id="…")
        client.set_locale("es-ES")
        client.translate("Hello, {name}!", category="Greetings", params={"name": "Sarah"})

    A framework wrapper can pass its own ``locale_source`` (anything satisfying
    :class:`~langsys.observable.LocaleSource`); the client only reads it.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        *,
        api_url: Optional[str] = None,
        base_locale: Optional[str] = None,
        locale: Optional[str] = None,
        locale_source: Optional[LocaleSource] = None,
        cache: Optional[CacheBackend] = None,
        cache_ttl: Optional[int] = None,
        timeout: Optional[float] = None,
        auto_flush: bool = False,
        debug: bool = False,
    ) -> None:
        self._config = Config.resolve(
            api_key,
            project_id,
            api_url=api_url,
            base_locale=base_locale,
            cache_ttl=cache_ttl,
            timeout=timeout,
            debug=debug,
        )
        self._http = HttpClient(
            self._config.api_url, self._config.api_key, timeout=self._config.timeout
        )
        self._cache: CacheBackend = cache if cache is not None else FileCache()
        self._catalog = CatalogStore(
            self._http, self._config.project_id, self._cache, ttl=self._config.cache_ttl
        )

        # Locale: either the wrapper's source (read-only) or an internally owned signal.
        seed = canonicalize_locale(locale or self._config.base_locale or "")
        self._owned_locale: Optional[Signal[str]] = None
        if locale_source is not None:
            self._locale_source: LocaleSource = locale_source
        else:
            self._owned_locale = Signal(seed)
            self._locale_source = self._owned_locale

        self._project: Optional[Project] = None
        self._pending: dict[tuple[str, str], None] = {}
        self._pending_blocks: dict[str, dict[str, Any]] = {}
        self._translatable_attributes: list[str] = list(DEFAULT_TRANSLATABLE_ATTRIBUTES)
        self._utils = Utilities(self._http, self._config.project_id)
        self._registrar: Optional[Registrar] = None

        if auto_flush:
            atexit.register(self._auto_flush)

    def _auto_flush(self) -> None:
        try:
            if self.has_pending:
                self.flush_pending()
        except Exception as exc:  # never raise from an atexit handler
            logger.warning("langsys auto-flush failed: %s", exc)

    # -- authorization --------------------------------------------------------

    def authorize(self, force: bool = False) -> Project:
        """Validate the key against the project and return its metadata (cached)."""
        if self._project is not None and not force:
            return self._project

        cache_key = f"auth_{self._config.project_id}"
        if not force:
            cached = self._cache.get(cache_key)
            if isinstance(cached, dict):
                self._project = Project.from_response(cached)
                return self._project

        path = f"authorize-project/{encode_segment(self._config.project_id)}"
        response = self._http.get(path)
        data = response.get("data")
        if not isinstance(data, dict):
            raise ConfigurationError("Langsys: unexpected authorize-project response.")
        self._cache.set(cache_key, data, self._config.cache_ttl)
        self._project = Project.from_response(data)
        return self._project

    @property
    def project(self) -> Project:
        return self.authorize()

    @property
    def key_type(self) -> KeyType:
        return self.authorize().key_type

    @property
    def can_write(self) -> bool:
        return self.key_type == "write"

    # -- locale ---------------------------------------------------------------

    @property
    def locale(self) -> str:
        """The current user locale (canonical), or ``""`` if none is set yet."""
        return canonicalize_locale(self._locale_source.get())

    def set_locale(self, locale: str) -> None:
        """Change the user locale. Only valid when the client owns the locale source
        (i.e. no external ``locale_source`` was supplied)."""
        if self._owned_locale is None:
            raise ConfigurationError(
                "Langsys: locale is driven by the supplied locale_source; set it there."
            )
        self._owned_locale.set(canonicalize_locale(locale))

    def _effective_locale(self, explicit: Optional[str]) -> str:
        loc = explicit or self._locale_source.get() or self._config.base_locale
        if not loc:
            loc = self.authorize().base_locale
        return canonicalize_locale(loc)

    # -- translation ----------------------------------------------------------

    def get_translations(self, locale: Optional[str] = None, *, use_cache: bool = True) -> Catalog:
        """Return the whole ``category -> phrase -> translation`` catalog for a locale."""
        return self._catalog.get(self._effective_locale(locale), use_cache=use_cache)

    def translate(
        self,
        phrase: str,
        *,
        category: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        locale: Optional[str] = None,
        content_block_id: Optional[str] = None,
    ) -> str:
        """Translate ``phrase`` (falling back to the phrase itself if untranslated),
        then interpolate ``params`` with locale-aware CLDR formatting."""
        loc = self._effective_locale(locale)
        catalog = self._catalog.get(loc)
        result = resolve(catalog, phrase, category, content_block_id)
        if result.missing and content_block_id is None:
            self._queue_missing(phrase, category)
        if params:
            return interpolate(result.text, params, loc)
        return result.text

    #: Short alias mirroring ``t()`` across the other SDKs.
    t = translate

    # -- content blocks (server-side HTML) ------------------------------------

    def translate_content_block(self, html: str, category: Optional[str] = None) -> str:
        """Translate a block of HTML as one unit. Untranslated/unknown blocks return the
        original HTML (and are queued for registration). Requires ``pip install langsys[html]``."""
        from .html.parser import apply_block_translations, extract_phrases

        if not html:
            return html
        loc = self._effective_locale(None)
        cat_name = category or UNCATEGORIZED
        phrases = extract_phrases(html, self._translatable_attributes)
        if not phrases:
            return html
        # The stored id uses the resolved category token (``__uncategorized__`` when
        # none), matching how the server-side SDKs register blocks.
        custom_id = generate_custom_id(cat_name, phrases)
        catalog = self._catalog.get(loc)
        cat = catalog.get(cat_name)
        block = cat.get(custom_id) if isinstance(cat, dict) else None
        if not isinstance(block, dict):
            self._queue_content_block(html, cat_name, custom_id, phrases)
            return html
        return apply_block_translations(html, block, self._translatable_attributes)

    def translate_page(
        self,
        html: str,
        category: Optional[str] = None,
        selector_categories: Optional[dict[str, Any]] = None,
    ) -> str:
        """Translate a whole HTML document (head + body) in place, classifying each
        block as a phrase or a content block. Requires ``pip install langsys[html]``."""
        from .html.page import translate_page

        return translate_page(self, html, category, selector_categories)

    def _queue_content_block(
        self, html: str, category: str, custom_id: str, phrases: list[str]
    ) -> None:
        self._pending_blocks.setdefault(
            custom_id,
            {"content": html, "category": category, "custom_id": custom_id, "phrases": phrases},
        )

    # -- translatable-attribute configuration ---------------------------------

    def get_translatable_attributes(self) -> list[str]:
        return list(self._translatable_attributes)

    def set_translatable_attributes(self, attributes: Sequence[str]) -> "LangsysClient":
        self._translatable_attributes = list(attributes)
        return self

    def add_translatable_attributes(self, attributes: Sequence[str]) -> "LangsysClient":
        for attr in attributes:
            if attr not in self._translatable_attributes:
                self._translatable_attributes.append(attr)
        return self

    def reset_translatable_attributes(self) -> "LangsysClient":
        self._translatable_attributes = list(DEFAULT_TRANSLATABLE_ATTRIBUTES)
        return self

    # -- discovery queue ------------------------------------------------------

    def _queue_missing(self, phrase: str, category: Optional[str]) -> None:
        self._pending[(category or UNCATEGORIZED, phrase)] = None

    @property
    def has_pending(self) -> bool:
        return bool(self._pending or self._pending_blocks)

    @property
    def pending_phrases(self) -> list[dict[str, str]]:
        """Phrases seen during rendering that aren't registered yet."""
        return [
            {"phrase": phrase, "category": category}
            for (category, phrase) in self._pending
        ]

    @property
    def pending_content_blocks(self) -> list[dict[str, Any]]:
        """Content blocks seen during rendering that aren't registered yet."""
        return list(self._pending_blocks.values())

    def clear_pending(self) -> None:
        self._pending.clear()
        self._pending_blocks.clear()

    def flush_pending(self) -> dict[str, Any]:
        """Register queued (discovered) phrases and content blocks. No-op with nothing
        pending; a read key logs a warning and clears the queue without writing."""
        if not self.has_pending:
            return {"phrases": 0, "content_blocks": 0, "success": True}
        if not self.can_write:
            logger.warning(
                "langsys: read key cannot register %d phrase(s) / %d content block(s)",
                len(self._pending),
                len(self._pending_blocks),
            )
            self.clear_pending()
            return {"phrases": 0, "content_blocks": 0, "success": True, "skipped": True}

        items: list[PhraseInput] = [
            {"phrase": phrase, "category": None if category == UNCATEGORIZED else category}
            for (category, phrase) in self._pending
        ]
        phrase_count = len(items)
        if items:
            self._reg.register_phrases(items)

        block_count = len(self._pending_blocks)
        for block in self._pending_blocks.values():
            category = None if block["category"] == UNCATEGORIZED else block["category"]
            self._reg.register_content_block(
                block["content"], block["phrases"], category=category, custom_id=block["custom_id"]
            )

        self.clear_pending()
        self._catalog.clear()  # new items exist server-side now; refetch next time
        return {"phrases": phrase_count, "content_blocks": block_count, "success": True}

    # -- registration (write key) ---------------------------------------------

    def register_phrases(self, phrases: Sequence[PhraseInput]) -> list[dict[str, Any]]:
        """Register phrases (strings, or ``{"phrase", "category"?, "translatable"?}``)."""
        self._require_write()
        return self._reg.register_phrases(phrases)

    def register_content_block(
        self,
        content: str,
        phrases: Sequence[str],
        *,
        category: Optional[str] = None,
        custom_id: Optional[str] = None,
        label: Optional[str] = None,
    ) -> dict[str, Any]:
        """Register a content block. ``phrases`` are its child phrases (auto-extraction
        from HTML arrives with page translation in phase 3)."""
        self._require_write()
        return self._reg.register_content_block(
            content, phrases, category=category, custom_id=custom_id, label=label
        )

    def sync(
        self, local_phrases: Sequence[PhraseInput], locale: Optional[str] = None
    ) -> dict[str, Any]:
        """Register any of ``local_phrases`` not already in the catalog, then refetch."""
        loc = self._effective_locale(locale)
        catalog = self._catalog.get(loc, use_cache=False)
        existing = _existing_keys(catalog)

        new_items: list[PhraseInput] = []
        for phrase in local_phrases:
            text = phrase if isinstance(phrase, str) else phrase["phrase"]
            category = None if isinstance(phrase, str) else phrase.get("category")
            key = f"{category or UNCATEGORIZED}::{text}"
            if key not in existing:
                new_items.append(phrase)

        synced = False
        if new_items and self.can_write:
            self._reg.register_phrases(new_items)
            self._catalog.clear(loc)
            self._catalog.get(loc, use_cache=False)
            synced = True

        return {
            "new_phrases": [p if isinstance(p, str) else p["phrase"] for p in new_items],
            "synced": synced,
        }

    def _require_write(self) -> None:
        if not self.can_write:
            raise AuthorizationError(
                "Langsys: a write key is required to register phrases.", status_code=403
            )

    @property
    def _reg(self) -> Registrar:
        if self._registrar is None:
            self._registrar = Registrar(
                self._http, self._config.project_id, batch_limit=self.authorize().batch_limit
            )
        return self._registrar

    # -- reference data (utilities) -------------------------------------------

    def countries(self, in_locale: Optional[str] = None) -> list[Country]:
        return self._utils.countries(self._effective_locale(in_locale))

    def dial_codes(self, in_locale: Optional[str] = None) -> list[DialCode]:
        return self._utils.dial_codes(self._effective_locale(in_locale))

    def currencies(self, in_locale: Optional[str] = None) -> list[Currency]:
        return self._utils.currencies(self._effective_locale(in_locale))

    def country_name(self, code: str, in_locale: Optional[str] = None) -> str:
        return self._utils.country_name(code, self._effective_locale(in_locale))

    def currency_name(self, code: str, in_locale: Optional[str] = None) -> str:
        return self._utils.currency_name(code, self._effective_locale(in_locale))

    def locales(self, in_locale: Optional[str] = None) -> dict[str, list[LocaleFlat]]:
        return self._utils.locales(self._effective_locale(in_locale))

    def locales_flat(self, in_locale: Optional[str] = None) -> list[LocaleFlat]:
        return self._utils.locales_flat(self._effective_locale(in_locale))

    def locales_data(self, in_locale: Optional[str] = None) -> list[LocaleInfo]:
        return self._utils.locales_data(self._effective_locale(in_locale))

    def locale_name(
        self, for_locale: str, short: bool = False, in_locale: Optional[str] = None
    ) -> str:
        return self._utils.locale_name(for_locale, short, self._effective_locale(in_locale))

    #: Alias for parity with the JS SDK naming.
    locale_name_with_lookup = locale_name

    def detect_preferred_locale(
        self, accept_language: Optional[str] = None, supported: Optional[list[str]] = None
    ) -> Optional[str]:
        """Best locale for an ``Accept-Language`` header (see the module function)."""
        return detect_preferred_locale(accept_language, supported)

    def refresh(self) -> bool:
        """Drop cached catalogs and reference data so the next call refetches."""
        self._catalog.clear()
        self._utils.clear()
        return True

    # -- cache / lifecycle ----------------------------------------------------

    def clear_cache(self, locale: Optional[str] = None) -> None:
        self._catalog.clear(locale)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LangsysClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _existing_keys(catalog: Catalog) -> set[str]:
    """Flatten a catalog into ``category::phrase`` keys (content-block children too)."""
    keys: set[str] = set()
    for category, entries in catalog.items():
        if not isinstance(entries, dict):
            continue
        for phrase, value in entries.items():
            if phrase.startswith("__") and phrase.endswith("__"):
                continue
            if isinstance(value, dict):
                for child in value:
                    keys.add(f"{category}::{child}")
            else:
                keys.add(f"{category}::{phrase}")
    return keys
