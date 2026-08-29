"""The public entry point: :class:`LangsysClient`."""

from __future__ import annotations

import atexit
from typing import Any, Optional, Sequence

from ._log import logger
from .cache.backend import CacheBackend
from .cache.file import FileCache
from .catalog import CatalogStore
from .config import Config
from .exceptions import ApiError, AuthorizationError, ConfigurationError, NetworkError
from .html.attributes import DEFAULT_TRANSLATABLE_ATTRIBUTES
from .http import HttpClient, encode_segment
from .interpolate import interpolate
from .locale import canonicalize_locale, detect_preferred_locale
from .observable import LocaleSource, Signal
from .registration import PhraseInput, Registrar, generate_custom_id
from .translate import lookup_block, resolve
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
        #: OBS-1 is once per process, not once per miss.
        self._warned_unusable = False
        #: The most recently *observed* server answer, as ``(stamp, value)``.
        #: Precedence is by **recency, never by source** — see :meth:`_observe_decision`.
        self._observed_decision: Optional[tuple[int, bool]] = None
        self._decision_stamp = 0
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
        """Validate the key against the project and return its metadata (cached).

        Project metadata is cacheable; the **write decision is not** and is never
        part of what this returns — see :meth:`_resolve_write_enabled`.
        """
        if self._project is not None and not force:
            return self._project

        cache_key = f"auth_{self._config.project_id}"
        if not force:
            cached = self._cache.get(cache_key)
            if isinstance(cached, dict):
                self._project = Project.from_response(cached)
                return self._project

        live = self._authorize_data()
        # Observe BEFORE stripping. This response is the freshest answer the server
        # has given us, and dropping it on the floor here is the same latch-shaped
        # failure from the other end: a stale slot outranking a live answer because
        # the live one was never recorded.
        flag = live.get("write_enabled")
        self._observe_decision(flag if isinstance(flag, bool) else None)

        data = _without_write_decision(live)
        # Stripped before it reaches EITHER store. `Project` is held for the life of
        # the client, so letting the flag ride along in `raw` would latch the decision
        # in memory just as surely as caching it would (GATE-3).
        self._cache.set(cache_key, data, self._config.cache_ttl)
        self._project = Project.from_response(data)
        return self._project

    def _observe_decision(self, value: Optional[bool]) -> None:
        """Record a server-computed ``write_enabled``, from **either** endpoint shape.

        Precedence is by **recency, not by source**. Both shapes are equally
        authoritative — they are the same server answering the same question — so the
        only sound tiebreak is which answer is newer. Letting one source outrank the
        other by construction is the latch-shaped failure this family keeps producing:
        a stale or empty slot outranking a live answer, and failing open when it does.
        """
        if value is None:
            return
        self._decision_stamp += 1
        self._observed_decision = (self._decision_stamp, value)

    def _warm_authorize_payload(self) -> Optional[dict[str, Any]]:
        """Already-known project metadata, if any. Never carries ``write_enabled``."""
        if self._project is not None:
            return self._project.raw
        cached = self._cache.get(f"auth_{self._config.project_id}")
        return cached if isinstance(cached, dict) else None

    def _authorize_data(self) -> dict[str, Any]:
        """One raw ``authorize-project`` response body. Never cached by this method."""
        path = f"authorize-project/{encode_segment(self._config.project_id)}"
        response = self._http.get(path)
        data = response.get("data")
        if not isinstance(data, dict):
            raise ConfigurationError("Langsys: unexpected authorize-project response.")
        return data

    @property
    def project(self) -> Project:
        return self.authorize()

    @property
    def key_type(self) -> KeyType:
        return self.authorize().key_type

    def _resolve_write_enabled(self) -> bool:
        """GATE-1 — the server decides, per response, whether this session may write.

        Resolved fresh at the **send site** and returned, never stored: capability is
        per-session and address-dependent, so the same key legitimately answers `true`
        from an allow-listed address and `false` from another. A decision that outlives
        the call would write-enable every anonymous visitor on the host (GATE-3), and
        on a shared Redis, the fleet.

        GATE-8 — a response with no ``write_enabled`` predates the capability. Only
        then may ``key_type`` stand in, and only for the plain ``write`` arm: for
        ``ip_write`` the answer is address-dependent and the absence of a positive
        signal *is* the answer.

        The flag is read from the payload that is **in hand at that moment**, and the
        fallback is reached only when *that* payload genuinely lacked it. Order is the
        whole rule here: reading a decision slot before authorize has populated it
        makes a live ``write_enabled: false`` look like absence, and the fallback then
        answers ``true`` — a closed gate reported open, which is the precise inversion
        this family exists to prevent. (Found by the Ruby lane; nothing about the code
        read wrong, only the order, and only execution caught it.)

        On a **cache hit** the payload lacks the flag by construction, because
        :meth:`authorize` strips it (GATE-4). Absence from any source is treated the
        same way for plain ``read``/``write`` keys — sound because the server
        guarantees ``write_enabled ≡ key_type`` for them — and never for ``ip_write``,
        whose answer is address-dependent and which therefore pays a live authorize
        rather than being inferred.
        """
        warm = self._warm_authorize_payload()
        if warm is None:
            # First call: nothing warm exists, so this payload is live and carries
            # the flag if the server sends one at all.
            data = self._live_authorize_payload()
            if data is None:
                return False
            return self._decide(data, allow_fallback=True)

        key_type = warm.get("key_type")
        if key_type == "ip_write":
            # Never inferred. Warm metadata cannot answer an address-dependent
            # question, so pay the round-trip.
            data = self._live_authorize_payload()
            if data is None:
                return False
            return self._decide(data, allow_fallback=False)

        # Plain read/write on a warm cache. The payload lacks the flag by construction
        # (our own GATE-4 strip), so the ruled key_type fallback applies — but a flag
        # this session has actually *observed*, from either endpoint shape, is real
        # evidence where key_type is only an inference, so it wins when we have one.
        observed = self._observed_decision
        if observed is not None:
            return observed[1]
        return self._decide(warm, allow_fallback=True)

    def _live_authorize_payload(self) -> Optional[dict[str, Any]]:
        try:
            return self._authorize_data()
        except (NetworkError, ApiError, ConfigurationError) as exc:
            # Never infer permission from a failure to ask.
            logger.warning("langsys: could not resolve write capability (%s).", exc)
            return None

    def _decide(self, data: dict[str, Any], *, allow_fallback: bool) -> bool:
        flag = data.get("write_enabled")
        key_type = data.get("key_type")
        if isinstance(flag, bool):
            self._observe_decision(flag)
            self._notice_unusable_capability(flag, key_type)
            return flag

        if not allow_fallback:
            logger.debug(
                "langsys: no write_enabled for key_type %r; the absence of a positive "
                "signal is the answer.",
                key_type,
            )
            return False

        if key_type == "write":
            logger.debug(
                "langsys: no write_enabled in this payload; falling back to key_type "
                "for the plain write arm only."
            )
            return True
        logger.debug(
            "langsys: no write_enabled in this payload and key_type is %r; treating as "
            "not write-enabled.",
            key_type,
        )
        return False

    def _notice_unusable_capability(self, write_enabled: bool, key_type: Any) -> None:
        """OBS-1 — a misconfigured integration is otherwise completely silent: no
        request, no error, nothing in the catalog. One line, once per process."""
        if write_enabled or key_type not in ("write", "ip_write"):
            return
        if self._warned_unusable:
            return
        self._warned_unusable = True
        logger.warning(
            "langsys: this session is NOT write-enabled although the key type is %r, so "
            "nothing will be registered. For an ip_write key this usually means the "
            "server's address is not allow-listed for this project.",
            key_type,
        )

    @property
    def can_write(self) -> bool:
        """Whether this session may write, resolved now. Not cached — see GATE-3."""
        return self._resolve_write_enabled()

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
            try:
                loc = self.authorize().base_locale
            except (NetworkError, ApiError, ConfigurationError) as exc:
                # WIRE-4 — resolving the locale is part of the render path.
                logger.warning(
                    "langsys: could not resolve the base locale from the API (%s).", exc
                )
                loc = ""
        return canonicalize_locale(loc)

    # -- translation ----------------------------------------------------------

    def get_translations(self, locale: Optional[str] = None, *, use_cache: bool = True) -> Catalog:
        """Return the whole ``category -> phrase -> translation`` catalog for a locale.

        Empty when the API could not be reached — this never raises (WIRE-4)."""
        fetch = self._catalog.get(self._effective_locale(locale), use_cache=use_cache)
        self._observe_decision(fetch.write_enabled)
        return fetch.catalog

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
        fetch = self._catalog.get(loc)
        self._observe_decision(fetch.write_enabled)
        result = resolve(fetch.catalog, phrase, category, content_block_id)
        # WIRE-4 — without a catalog a miss is indistinguishable from a hit, so an
        # outage would re-register everything that already exists. Record nothing.
        if result.missing and content_block_id is None and fetch.ok:
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
        # CID-2 — the hash takes the *raw* category, `''` when there is none.
        # `__uncategorized__` is a cache-lookup namespace and must never reach the id.
        custom_id = generate_custom_id(category, phrases)
        fetch = self._catalog.get(loc)
        self._observe_decision(fetch.write_enabled)
        cat = fetch.catalog.get(cat_name)
        block = lookup_block(cat, category, custom_id, phrases)
        if not isinstance(block, dict):
            # WIRE-4 — see translate(): never queue off a catalog we could not read.
            if fetch.ok:
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
        """Register queued (discovered) phrases and content blocks.

        REG-10 — one behaviour across every path: never throw into a render path,
        always log, and **never return a success-shaped result for work that did not
        happen**, a skipped write included. A caller that checks ``success`` is
        entitled to believe it.
        """
        if not self.has_pending:
            return {"phrases": 0, "content_blocks": 0, "success": True}

        # GATE-2 — collect always, choose the lane at the send site. Resolved here,
        # once per flush, and never stored.
        if not self._resolve_write_enabled():
            phrase_count, block_count = len(self._pending), len(self._pending_blocks)
            logger.warning(
                "langsys: this session is not write-enabled; discarding %d phrase(s) and "
                "%d content block(s) without registering them.",
                phrase_count,
                block_count,
            )
            # Discarding a queue we have just been told we may not write is correct;
            # reporting it as success is the defect.
            self.clear_pending()
            return {
                "phrases": 0,
                "content_blocks": 0,
                "success": False,
                "skipped": True,
                "reason": "not-write-enabled",
                "discarded_phrases": phrase_count,
                "discarded_content_blocks": block_count,
            }

        items: list[PhraseInput] = [
            {"phrase": phrase, "category": None if category == UNCATEGORIZED else category}
            for (category, phrase) in self._pending
        ]
        blocks = list(self._pending_blocks.values())
        phrase_count, block_count = len(items), len(blocks)

        try:
            if items:
                self._reg.register_phrases(items)
            if blocks:
                # REG-9 — one batched POST per chunk, not one per block.
                self._reg.register_content_blocks(blocks)
        except (NetworkError, ApiError) as exc:
            # REG-8/GATE-5 — the queue stays, and nothing is marked as done. Retrying
            # a phrase the server already accepted is harmless; suppressing one it
            # never saw is not.
            logger.warning("langsys: registration failed (%s); keeping the queue.", exc)
            return {
                "phrases": 0,
                "content_blocks": 0,
                "success": False,
                "error": str(exc),
                "queued_phrases": phrase_count,
                "queued_content_blocks": block_count,
            }

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
        fetch = self._catalog.get(loc, use_cache=False)
        self._observe_decision(fetch.write_enabled)
        if not fetch.ok:
            # WIRE-4 — without a catalog every phrase looks new; registering them
            # all is the write storm this guard exists to prevent.
            logger.warning("langsys: sync skipped — the catalog could not be read.")
            return {"new_phrases": [], "synced": False, "success": False}
        existing = _existing_keys(fetch.catalog)

        new_items: list[PhraseInput] = []
        for phrase in local_phrases:
            text = phrase if isinstance(phrase, str) else phrase["phrase"]
            category = None if isinstance(phrase, str) else phrase.get("category")
            key = f"{category or UNCATEGORIZED}::{text}"
            if key not in existing:
                new_items.append(phrase)

        synced = False
        if new_items and self._resolve_write_enabled():
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


def _without_write_decision(data: dict[str, Any]) -> dict[str, Any]:
    """GATE-4 — strip the write decision from anything about to be cached.

    ``key_type`` is a property of the key and may be cached; ``write_enabled`` is a
    property of the *session* and must not outlive it. The hazard is any store that
    is process-external or shared by default: one request from an allow-listed office
    address would otherwise write-enable every anonymous visitor on the host for the
    TTL, and fleet-wide on a shared Redis.
    """
    return {k: v for k, v in data.items() if k != "write_enabled"}


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
