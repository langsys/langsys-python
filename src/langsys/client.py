"""The public entry point: :class:`LangsysClient`."""

from __future__ import annotations

import atexit
import threading
import time
from typing import Any, Iterable, Optional, Sequence

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
from .messages import DEFAULT_MESSAGE_CATEGORY, Entry
from .migrate import LegacyKeys
from .observable import LocaleSource, Signal
from .registration import PhraseInput, Registrar, generate_custom_id
from .request_locale import LocaleChoice, resolve_request_locale
from .scope import RequestScope, begin_request_scope, current_scope, end_request_scope
from .text import strip_c0
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

#: REG-2 — a burst from one render becomes one request. An interval-only flush delays
#: every registration by up to its full period, and lazy-loaded/streamed content is the
#: common case discovery targets.
DEFAULT_DEBOUNCE_SECONDS = 0.4

#: REG-8 — 3s, doubling, ceiling ~5min. Without backoff a failing endpoint gets a
#: request every interval for as long as the process lives, and the payload *grows*,
#: because new misses keep joining a queue that never drains.
BACKOFF_INITIAL_SECONDS = 3.0
BACKOFF_MAX_SECONDS = 300.0


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
        auto_flush: bool = True,
        debounce: Optional[float] = DEFAULT_DEBOUNCE_SECONDS,
        debug: bool = False,
        message_category: str = DEFAULT_MESSAGE_CATEGORY,
        legacy_files: Optional[Sequence[Any]] = None,
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
        #: OBS-1 fires once per client session, not once per miss; a request-boundary
        #: reset re-arms it. See reset_write_decision().
        self._warned_unusable = False
        #: The most recently *observed* server answer, as ``(stamp, value)``.
        #: Precedence is by **recency, never by source** — see :meth:`_observe_decision`.
        self._observed_decision: Optional[tuple[int, bool]] = None
        self._decision_stamp = 0
        # The queue is reachable from the debounce timer thread as well as the caller.
        self._lock = threading.RLock()
        self._pending: dict[tuple[str, str], None] = {}
        self._pending_blocks: dict[str, dict[str, Any]] = {}
        #: SRV-3 - which open request scopes recorded each queued item. An item absent from
        #: these maps was recorded outside any scope and is sendable now; an item present is
        #: held until one of its scopes has ended (see `langsys.scope`).
        self._phrase_scopes: dict[tuple[str, str], set[RequestScope]] = {}
        self._block_scopes: dict[str, set[RequestScope]] = {}
        self._debounce = debounce if debounce and debounce > 0 else None
        self._timer: Optional[threading.Timer] = None
        #: REG-7 — one send in flight. The debounce timer and a caller's explicit flush
        #: are different threads and can arrive together.
        self._sending = threading.Lock()
        #: REG-8 backoff state. `_backoff_until` is a monotonic deadline, not a clock
        #: time, so a system clock change cannot strand the queue.
        self._backoff_until = 0.0
        self._backoff_seconds = 0.0
        #: REG-11 — one warning per (category, phrase); the check runs on every render.
        self._warned_ellipsis: set[tuple[str, str]] = set()
        self._translatable_attributes: list[str] = list(DEFAULT_TRANSLATABLE_ATTRIBUTES)
        #: MSG-6 - the one category server-message templates are registered and rendered under.
        self.message_category = message_category
        #: MIG-1 - legacy-key mode runs only when files are configured. Unset, no file is read
        #: and no key lookup happens. Loaded here, so a file this SDK cannot read (a `.mo`, an
        #: unsupported format) fails at configuration, naming the file.
        self._legacy = LegacyKeys(legacy_files) if legacy_files else None
        #: MSG-11 - one warning per (template, marker) whose value is a catalogued phrase.
        self._warned_marker_values: set[tuple[str, str]] = set()
        self._utils = Utilities(self._http, self._config.project_id)
        self._registrar: Optional[Registrar] = None

        if auto_flush:
            atexit.register(self._auto_flush)

    def _auto_flush(self) -> None:
        """REG-3 — best-effort flush as the process ends.

        Deliberately `force=True`: this is the last attempt, not a retry loop, and a
        queue discarded here is discarded for good — unlike a browser there is no later
        page in the same session to recover on. Best-effort by nature: a shutdown hook
        does not run on an OOM kill or a hard timeout, which is exactly why the rule
        also requires a public manual flush and forbids relying on this path.
        """
        try:
            self._cancel_timer()
            if self.has_pending:
                self.flush_pending(force=True)
        except Exception as exc:  # never raise from an atexit handler
            logger.warning("langsys auto-flush failed: %s", exc)

    # -- debounce (REG-2) -----------------------------------------------------
    #
    # CONCURRENCY NOTE — read this before touching anything below.
    #
    # This SDK is synchronous, and three rules were filed `n/a (synchronous)` on that
    # basis: GATE-2 (no unknown window for the write decision), REG-6 (no await across
    # which a queue could be cleared) and REG-7 (no concurrent senders). The expiry
    # condition recorded against them was "an async twin lands".
    #
    # REG-2's debounce is that twin arriving through a side door. It is a timer thread,
    # so from here on:
    #
    #   * REG-6 and REG-7 are LIVE and implemented below — the send releases `_lock`
    #     across a slow POST, so a render on the caller's thread overlaps it. `_lock`
    #     guards queue mutation; `_sending` guarantees one send at a time; the batch is
    #     snapshotted by key and only the sent keys are removed afterwards.
    #   * GATE-2 is LIVE and implemented — it is no longer `n/a` at all. An earlier
    #     revision argued it stayed vacuous because the decision resolves synchronously
    #     inside the flush, so "no window exists in which the decision is unknown".
    #     That was wrong, and review caught it: a resolution that FAILS is unknown, and
    #     collapsing that to False is the letter of what GATE-2 forbids. It cost the
    #     whole queue on a transient authorize blip. `_resolve_write_enabled` now
    #     returns True / False / None, and the flush HOLDS on None.
    #
    # Anything that adds a second concurrent path here must re-check all three.

    def _cancel_timer(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _schedule_flush(self) -> None:
        """(Re)start the debounce so a burst of misses coalesces into one request."""
        if self._debounce is None:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            delay = self._debounce
            # REG-8 — while backing off, wake when the backoff expires rather than
            # re-attempting on the debounce and turning the backoff into a busy loop.
            remaining = self._backoff_until - time.monotonic()
            if remaining > 0:
                delay = max(delay, remaining)
            self._timer = threading.Timer(delay, self._debounced_flush)
            self._timer.daemon = True
            self._timer.start()

    def _debounced_flush(self) -> None:
        try:
            with self._lock:
                self._timer = None
            if self.has_pending:
                self.flush_pending()
        except Exception as exc:  # a timer thread must never raise
            logger.warning("langsys: debounced flush failed: %s", exc)

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

    def reset_write_decision(self) -> None:
        """Forget the observed write decision. **Call this at every request boundary.**

        GATE-3 — the decision must not survive a single request. A client held for the
        life of a process (a module-level singleton, a Django app config, a FastAPI
        dependency with ``lru_cache``) outlives the request by construction, so
        instance state documented as "request-scoped" is not, and process death is not
        a boundary. The same shape as PHP under Octane/Swoole/RoadRunner.

        Capability is address-dependent, and it fails in **both** directions: one
        allow-listed request would otherwise write-enable every anonymous visitor on
        the host, and one anonymous request would make the allow-listed origin
        silently register nothing.

        A framework wrapper owns calling this — see ``CONFORMANCE.md``. A short-lived
        script or worker that builds a client per run needs no reset.
        """
        self._observed_decision = None
        self._warned_unusable = False

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

    def _resolve_write_enabled(self) -> Optional[bool]:
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
                return None
            return self._decide(data, allow_fallback=True)

        key_type = warm.get("key_type")
        if key_type == "ip_write":
            # Never inferred. Warm metadata cannot answer an address-dependent
            # question, so pay the round-trip.
            data = self._live_authorize_payload()
            if data is None:
                return None
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
        """OBS-1 — a misconfigured integration is otherwise completely
        silent: no request, no error, nothing in the catalog. One line, once per client
        session — re-armed at a request boundary by reset_write_decision()."""
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
        """Whether this session may write, resolved now. Not cached — see GATE-3.

        Collapses *unknown* to ``False``: never infer permission from a failure to ask.
        Callers that must distinguish "the server said no" from "we could not reach the
        server" — the flush lane does, because only the first justifies discarding a
        queue — use :meth:`_resolve_write_enabled` and handle ``None``.
        """
        return self._resolve_write_enabled() is True

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
        # MIG-2 - in legacy-key mode the argument is a key first: a hit becomes its source value
        # (never the key) under its namespace, a miss stays literal source text.
        if self._legacy is not None and content_block_id is None:
            phrase, category, _ = self._legacy.resolve(phrase, category)
        # TOK-2 - a code-registered key is stripped of C0 controls on lookup and on register
        # alike, so a phrase carrying one resolves to the same entry as the markup that holds it.
        phrase = strip_c0(phrase)
        loc = self._effective_locale(locale)
        fetch = self._catalog.get(loc)
        self._observe_decision(fetch.write_enabled)
        result = resolve(fetch.catalog, phrase, category, content_block_id)
        # WIRE-4 — without a catalog a miss is indistinguishable from a hit, so an
        # outage would re-register everything that already exists. Record nothing.
        if result.missing and content_block_id is None and fetch.ok:
            self._queue_missing(phrase, category, fetch.catalog.get(category or UNCATEGORIZED))
        if params:
            return interpolate(result.text, params, loc)
        return result.text

    #: Short alias mirroring ``t()`` across the other SDKs.
    t = translate

    # -- content blocks (server-side HTML) ------------------------------------

    def translate_content_block(self, html: str, category: Optional[str] = None) -> str:
        """Translate a fragment of HTML as one unit (TOK-6). A fragment whose one token is its one
        text node is a phrase; anything else is a content block, returned stamped with its id
        (MARK-1). Untranslated content returns as authored and is queued for registration.
        Requires ``pip install langsys[html]``."""
        from .html.page import translate_fragment

        if not html:
            return html
        return translate_fragment(self, html, category)

    def _render_block(self, html: str, category: Optional[str], phrases: list[str]) -> str:
        """Look a content block up under its id, render it, and stamp it; queue it on a miss."""
        from .html.parser import apply_block_translations, stamp_content_block

        loc = self._effective_locale(None)
        cat_name = category or UNCATEGORIZED
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
            # MARK-1 — the id is what the block IS, not what the catalog happened to
            # hold. An unstamped miss is the case you most need to inspect.
            return stamp_content_block(html, custom_id)
        translated = apply_block_translations(html, block, self._translatable_attributes)
        return stamp_content_block(translated, custom_id)

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
        with self._lock:
            existed = custom_id in self._pending_blocks
            self._pending_blocks.setdefault(
                custom_id,
                {"content": html, "category": category, "custom_id": custom_id, "phrases": phrases},
            )
            self._tag(self._block_scopes, custom_id, existed)
        self._schedule_flush()

    # -- request scopes (SRV-3) -------------------------------------------------

    def begin_request_scope(self) -> RequestScope:
        """Same as `langsys.begin_request_scope()`; scopes are not per client."""
        return begin_request_scope()

    def end_request_scope(self, scope: RequestScope) -> None:
        """Same as `langsys.end_request_scope(scope)`."""
        end_request_scope(scope)

    def request_scope(self) -> Any:
        """Same as `langsys.request_scope()`: `with client.request_scope(): ...`."""
        from .scope import request_scope

        return request_scope()

    def _tag(self, scopes: dict[Any, set[RequestScope]], key: Any, existed: bool) -> None:
        """Record which request scope, if any, this miss was recorded in. Caller holds the lock.

        A miss recorded outside any scope is sendable at once, and stays so if a scope records it
        again. One recorded inside a scope is held until a scope that recorded it has ended.
        """
        scope = current_scope()
        if scope is None:
            scopes.pop(key, None)
            return
        if existed and key not in scopes:
            return
        scopes.setdefault(key, set()).add(scope)
        scope._joined_by(self)

    @staticmethod
    def _released(scopes: dict[Any, set[RequestScope]], key: Any) -> bool:
        held = scopes.get(key)
        return held is None or any(scope.ended for scope in held)

    def _sendable(self) -> tuple[list[tuple[str, str]], list[str]]:
        """The queued items whose request, if any, has already been answered."""
        with self._lock:
            return (
                [k for k in self._pending if self._released(self._phrase_scopes, k)],
                [b for b in self._pending_blocks if self._released(self._block_scopes, b)],
            )

    def _scope_released(self) -> None:
        """A scope this client queued work under has ended: its misses may go now."""
        phrases, blocks = self._sendable()
        if phrases or blocks:
            self._schedule_flush()

    def _project_base_locale(self) -> str:
        """The project's base locale: from project metadata already held, else the configured
        base locale, else one authorize round-trip. "" when none of them can say."""
        warm = self._warm_authorize_payload()
        if warm is not None and warm.get("base_locale"):
            return str(warm["base_locale"])
        if self._config.base_locale:
            return self._config.base_locale
        try:
            return self.authorize().base_locale or ""
        except (NetworkError, ApiError, ConfigurationError):
            return ""

    # -- request locale (SRV-6) ------------------------------------------------

    def resolve_request_locale(
        self,
        *,
        url: Optional[str] = None,
        cookie: Optional[str] = None,
        accept_language: Optional[str] = None,
        uses_cookie: bool = True,
    ) -> LocaleChoice:
        """The locale to serve this request in: URL, then cookie or session, then
        `Accept-Language`, then the project's base locale, each validated against the locales
        the project serves. The result names the `Vary` headers the response must carry. See
        `langsys.request_locale`.

        If the project's locales cannot be read (WIRE-4), no candidate can be validated, so the
        request is served in the configured base locale rather than in an unchecked one.
        """
        try:
            project = self.authorize()
            supported = [project.base_locale, *project.target_locales]
            base = project.base_locale
        except (NetworkError, ApiError, ConfigurationError) as exc:
            logger.warning("langsys: could not read the project's locales (%s).", exc)
            base = self._config.base_locale or ""
            supported = [base] if base else []
        return resolve_request_locale(
            supported, base, url=url, cookie=cookie,
            accept_language=accept_language, uses_cookie=uses_cookie,
        )

    # -- server messages (MSG) -------------------------------------------------

    def server_message(
        self,
        code: str,
        template: str,
        params: Optional[dict[str, Any]] = None,
        field: Optional[str] = None,
    ) -> Entry:
        """Build the entry a server sends for a failure (MSG-1, MSG-4), and act on it.

        MSG-8 - a template the catalog does not list yet is queued for registration on the ordinary
        flush path, so it is sent after the response (inside a request scope) and never blocks the
        request; a session that cannot write discards it like any other miss.
        MSG-11 - a marker filled with a string that is itself a phrase in the catalog warns once
        per `(template, marker)`: a translatable value in a marker is never translated.
        """
        from .messages import server_message, template_markers, warn_translatable_marker_value

        entry = server_message(code, template, params, field)
        category = self.message_category
        fetch = self._catalog.get(self._effective_locale(None))
        self._observe_decision(fetch.write_enabled)
        if not fetch.ok:
            return entry  # WIRE-4: no catalog, no decisions
        phrases = _catalog_phrases(fetch.catalog)
        for marker in template_markers(template):
            value = (params or {}).get(marker)
            key = (template, marker)
            warned = key in self._warned_marker_values
            if isinstance(value, str) and value in phrases and not warned:
                self._warned_marker_values.add(key)
                warn_translatable_marker_value(template, marker, value)
        known = fetch.catalog.get(category)
        if not (isinstance(known, dict) and template in known):
            self._queue_missing(template, category, known)
        return entry

    def render_server_message(
        self, entry: Entry, category: Optional[str] = None, locale: Optional[str] = None
    ) -> str:
        """Render a received entry: `t(template, category, params)` when the catalog translates
        the template, `message` otherwise. `message` is filled, possibly localised text and is
        never a lookup key (MSG-5)."""
        category = category or self.message_category
        fetch = self._catalog.get(self._effective_locale(locale))
        translations = fetch.catalog.get(category) if fetch.ok else None
        template = entry.get("template")
        value = (
            translations.get(template)
            if isinstance(translations, dict) and isinstance(template, str)
            else None
        )
        if not isinstance(value, str) or not value:
            return str(entry.get("message", ""))
        params = entry.get("params") or {}
        return interpolate(value, params, self._effective_locale(locale)) if params else value

    def register_templates(
        self, templates: Iterable[str], *, category: Optional[str] = None
    ) -> int:
        """MSG-7 - register every listed template the catalog does not hold yet, under the message
        category. Idempotent: a second run registers nothing. Returns how many were registered."""
        category = category or self.message_category
        fetch = self._catalog.get(self._effective_locale(None), use_cache=False)
        if not fetch.ok:
            raise NetworkError("Langsys: the catalog could not be read, so nothing was registered.")
        known = fetch.catalog.get(category)
        new = [t for t in templates if not (isinstance(known, dict) and t in known)]
        if new:
            self.register_phrases([{"phrase": t, "category": category} for t in new])
            self._catalog.clear()
        return len(new)

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

    def _queue_missing(
        self, phrase: str, category: Optional[str], catalog_category: Any = None
    ) -> None:
        key = (category or UNCATEGORIZED, phrase)
        if self._ellipsis_suppresses(phrase, key, catalog_category):
            return
        with self._lock:
            existed = key in self._pending
            self._pending[key] = None
            self._tag(self._phrase_scopes, key, existed)
        self._schedule_flush()

    # -- REG-11: ellipsis-terminated text -------------------------------------

    def _ellipsis_suppresses(
        self, phrase: str, key: tuple[str, str], catalog_category: Any
    ) -> bool:
        """Warn on ellipsis-terminated text; suppress only on a **second** signal.

        Upstream truncation puts the ellipsis in the string itself, so the truncated
        form gets translated and stored and never matches the full paragraph, which
        later registers as a second phrase — catalog pollution plus double translation
        spend. But a blanket skip has real false positives: ``Loading…``, ``Saving…``
        and ``Please wait…`` are legitimate phrases, and silently refusing to register
        them would create a *new* silent failure, which is the class this spec exists
        to remove.

        So the warning is unconditional and the suppression is not: it needs a longer
        catalog entry sharing the prefix, which is the actual harm condition and fires
        only once the pollution has already occurred.
        """
        prefix = _ellipsis_prefix(phrase)
        if prefix is None:
            return False

        longer = _longer_entry_sharing_prefix(catalog_category, phrase, prefix)
        if key not in self._warned_ellipsis:
            self._warned_ellipsis.add(key)
            if longer is None:
                logger.warning(
                    "langsys: the phrase %r ends in an ellipsis. If that is upstream "
                    "truncation, the truncated form will be translated and stored and "
                    "will never match the full text. Registering it anyway — a phrase "
                    "like 'Loading…' is legitimate.",
                    phrase,
                )
            else:
                logger.warning(
                    "langsys: not registering %r — the catalog already holds a longer "
                    "phrase with the same prefix (%r), so this is upstream truncation "
                    "rather than a phrase that genuinely ends in an ellipsis.",
                    phrase,
                    longer,
                )
        return longer is not None

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending or self._pending_blocks)

    @property
    def pending_phrases(self) -> list[dict[str, str]]:
        """Phrases seen during rendering that aren't registered yet."""
        with self._lock:
            return [
                {"phrase": phrase, "category": category}
                for (category, phrase) in self._pending
            ]

    @property
    def pending_content_blocks(self) -> list[dict[str, Any]]:
        """Content blocks seen during rendering that aren't registered yet."""
        with self._lock:
            return list(self._pending_blocks.values())

    def clear_pending(self) -> None:
        with self._lock:
            self._pending.clear()
            self._pending_blocks.clear()
            self._phrase_scopes.clear()
            self._block_scopes.clear()

    def flush_pending(self, *, force: bool = False) -> dict[str, Any]:
        """Register queued (discovered) phrases and content blocks.

        REG-10 — one behaviour across every path: never throw into a render path,
        always log, and **never return a success-shaped result for work that did not
        happen**, a skipped write included. A caller that checks ``success`` is
        entitled to believe it.
        """
        try:
            return self._flush_outer(force=force)
        finally:
            # REG-2 — a flush that ends with work still queued MUST re-arm the timer.
            # Every path cancels it on the way in, so without this the tail of a burst
            # that arrived during a slow POST — or anything a *declining* flush left
            # behind — waits for an unrelated miss or process exit. That is the debounce
            # quietly ceasing to be a send path in exactly the case it exists for.
            #
            # In the outermost `finally` on purpose: an earlier revision put it inside
            # the send-lock's try, which the decline path returns before ever reaching,
            # so the one case this was written for was the one it missed.
            phrases, blocks = self._sendable()
            if phrases or blocks:
                self._schedule_flush()

    def _flush_outer(self, *, force: bool) -> dict[str, Any]:
        self._cancel_timer()
        if not self.has_pending:
            return {"phrases": 0, "content_blocks": 0, "success": True}
        # SRV-3 - work recorded under a request whose response is not out yet is not this
        # flush's to send, whoever is flushing. Checked before anything costly. The shutdown
        # flush (force) is the one path that releases it regardless.
        if not force and self._sendable() == ([], []):
            return {
                "phrases": 0,
                "content_blocks": 0,
                "success": False,
                "skipped": True,
                "reason": "request-in-progress",
                "queued_phrases": len(self._pending),
                "queued_content_blocks": len(self._pending_blocks),
            }

        # REG-7 — never two sends at once, checked before anything costly. Declining is
        # correct rather than queueing behind the in-flight send: whatever this call
        # would have sent is still in the queue, and the running send will take it or
        # the next flush will. Checked here rather than at the send site so a declining
        # flush does not pay an authorize round-trip to discover it is declining.
        if not self._sending.acquire(blocking=False):
            return {
                "phrases": 0,
                "content_blocks": 0,
                "success": False,
                "skipped": True,
                "reason": "send-in-flight",
                "queued_phrases": len(self._pending),
                "queued_content_blocks": len(self._pending_blocks),
            }
        try:
            return self._flush_locked(force=force)
        finally:
            self._sending.release()

    def _flush_locked(self, *, force: bool) -> dict[str, Any]:
        # REG-8 — while backing off, decline without sending. The queue is retained,
        # so nothing is lost; retrying now is what turns a failing endpoint into a
        # request per interval against a payload that only grows.
        remaining = self._backoff_until - time.monotonic()
        if remaining > 0 and not force:
            return {
                "phrases": 0,
                "content_blocks": 0,
                "success": False,
                "skipped": True,
                "reason": "backoff",
                "retry_in_seconds": round(remaining, 3),
                "queued_phrases": len(self._pending),
                "queued_content_blocks": len(self._pending_blocks),
            }

        # GATE-2 — collect always, choose the lane at the send site; model the answer
        # as true / false / **unknown**, and HOLD on unknown.
        #
        # The distinction is the whole rule. Discarding a queue the server has just
        # told us we may not write is correct. Discarding it because we could not ASK
        # loses every phrase permanently, on a transient blip, with nothing logged that
        # names the real cause — the exact class of loss this spec exists to remove.
        # An `ip_write` session pays a live authorize on every flush, so without this
        # any network wobble discards everything.
        decision = self._resolve_write_enabled()
        if decision is None:
            delay = self._note_failure()
            logger.warning(
                "langsys: could not determine write capability; keeping %d phrase(s) and "
                "%d content block(s) queued and retrying in %.0fs.",
                len(self._pending),
                len(self._pending_blocks),
                delay,
            )
            return {
                "phrases": 0,
                "content_blocks": 0,
                "success": False,
                "skipped": True,
                "reason": "capability-unknown",
                "retry_in_seconds": round(delay, 3),
                "queued_phrases": len(self._pending),
                "queued_content_blocks": len(self._pending_blocks),
            }

        if decision is False:
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

        # REG-6 — snapshot the batch by KEY, and afterwards remove only what was sent.
        # The POST below is slow and the lock is released across it, so a debounced
        # flush on the timer thread and a render on the caller's thread overlap here:
        # `clear_pending()` would drop every miss recorded during the send.
        with self._lock:
            if force:
                phrase_keys = list(self._pending.keys())
                block_ids = list(self._pending_blocks.keys())
            else:
                phrase_keys, block_ids = self._sendable()
            items: list[PhraseInput] = [
                {"phrase": phrase, "category": None if category == UNCATEGORIZED else category}
                for (category, phrase) in phrase_keys
            ]
            blocks = [self._pending_blocks[b] for b in block_ids]
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
            delay = self._note_failure()
            logger.warning(
                "langsys: registration failed (%s); keeping the queue and backing off "
                "for %.0fs.",
                exc,
                delay,
            )
            self._schedule_flush()
            return {
                "phrases": 0,
                "content_blocks": 0,
                "success": False,
                "error": str(exc),
                "queued_phrases": phrase_count,
                "queued_content_blocks": block_count,
                "retry_in_seconds": round(delay, 3),
            }

        self._reset_backoff()
        with self._lock:
            for key in phrase_keys:
                self._pending.pop(key, None)
                self._phrase_scopes.pop(key, None)
            for block_id in block_ids:
                self._pending_blocks.pop(block_id, None)
                self._block_scopes.pop(block_id, None)
        self._catalog.clear()  # new items exist server-side now; refetch next time
        return {"phrases": phrase_count, "content_blocks": block_count, "success": True}

    def _note_failure(self) -> float:
        """REG-8 — 3s, doubling, ceiling ~5min. Returns the new delay."""
        with self._lock:
            if not self._backoff_seconds:
                nxt = BACKOFF_INITIAL_SECONDS
            else:
                nxt = self._backoff_seconds * 2
            self._backoff_seconds = min(nxt, BACKOFF_MAX_SECONDS)
            self._backoff_until = time.monotonic() + self._backoff_seconds
            return self._backoff_seconds

    def _reset_backoff(self) -> None:
        """Reset on first success — not gradually. A recovered endpoint is recovered."""
        with self._lock:
            self._backoff_seconds = 0.0
            self._backoff_until = 0.0

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
        if new_items and self._resolve_write_enabled() is True:
            self._reg.register_phrases(new_items)
            self._catalog.clear(loc)
            self._catalog.get(loc, use_cache=False)
            synced = True

        return {
            "new_phrases": [p if isinstance(p, str) else p["phrase"] for p in new_items],
            "synced": synced,
        }

    def _require_write(self) -> None:
        """Gate an explicit registration call, distinguishing denial from ignorance.

        Both refuse, but a caller debugging "why did nothing register" is served very
        differently by "the server says this session may not write" and "we could not
        reach the server to ask".
        """
        decision = self._resolve_write_enabled()
        if decision is True:
            return
        if decision is None:
            raise NetworkError(
                "Langsys: could not reach the API to determine write capability, so "
                "registration was not attempted. Nothing was lost; retry."
            )
        raise AuthorizationError(
            "Langsys: this session is not write-enabled, so phrases cannot be "
            "registered.",
            status_code=403,
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
        self._cancel_timer()
        self._http.close()

    def __enter__(self) -> LangsysClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


#: REG-11 — both spellings. CSS truncation needs no handling: `text-overflow: ellipsis`
#: clips visually while the DOM text stays complete, so there is nothing to detect.
_ELLIPSIS_SUFFIXES = ("\u2026", "...")


def _ellipsis_prefix(phrase: str) -> Optional[str]:
    """The text before a trailing ellipsis, or ``None`` when there is not one."""
    for suffix in _ELLIPSIS_SUFFIXES:
        if phrase.endswith(suffix):
            prefix = phrase[: -len(suffix)].rstrip()
            return prefix or None
    return None


def _longer_entry_sharing_prefix(
    catalog_category: Any, phrase: str, prefix: str
) -> Optional[str]:
    """A catalog entry that starts with ``prefix`` and continues past it.

    That is the actual harm condition — the full paragraph is already registered, so
    this truncated form is pollution rather than a legitimate ``Loading…``. It has no
    false positives because it can only fire once the pollution has occurred.
    """
    if not isinstance(catalog_category, dict):
        return None
    for key in catalog_category:
        if not isinstance(key, str) or key == phrase:
            continue
        if key.startswith(prefix) and len(key) > len(prefix):
            return key
    return None


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
            # REG-12 - presence decides, exactly as it does in translate(). A block's own key is
            # known too: text equal to a block id is not new, or it re-registers on every sync.
            keys.add(f"{category}::{phrase}")
            if isinstance(value, dict):
                for child in value:
                    keys.add(f"{category}::{child}")
    return keys


def _catalog_phrases(catalog: Catalog) -> set[str]:
    """Every source phrase a catalog holds, content-block children included."""
    phrases: set[str] = set()
    for entries in catalog.values():
        if not isinstance(entries, dict):
            continue
        for phrase, value in entries.items():
            if isinstance(value, dict):
                phrases.update(k for k in value if isinstance(k, str))
            elif not (phrase.startswith("__") and phrase.endswith("__")):
                phrases.add(phrase)
    return phrases
