"""The public entry point: :class:`LangsysClient`."""

from __future__ import annotations

import atexit
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Union

from ._log import logger
from .cache.backend import CacheBackend
from .cache.file import FileCache
from .catalog import CatalogStore
from .config import Config
from .exceptions import (
    ApiError,
    AuthorizationError,
    ConfigurationError,
    NetworkError,
    NotConfiguredError,
)
from .html.attributes import DEFAULT_TRANSLATABLE_ATTRIBUTES
from .http import HttpClient, encode_segment
from .interpolate import interpolate
from .locale import canonicalize_locale, detect_preferred_locale, normalize_locale
from .messages import DEFAULT_MESSAGE_CATEGORY, Entry, fill_template
from .migrate import SKIP_PLURAL_FORMS, LegacyFile, LegacyKeys, LegacyTranslations
from .observable import LocaleSource, Signal
from .registration import PhraseInput, Registrar, generate_custom_id
from .request_locale import LocaleChoice, resolve_request_locale
from .scope import RequestScope, begin_request_scope, current_scope, end_request_scope
from .text import strip_c0
from .translate import (
    Lookup,
    Miss,
    Resolved,
    block_translated,
    lookup_block,
    previous_block,
    previous_entry,
    resolve,
)
from .translate import displayable as _displayable
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
from .values import ValueSets

#: REG-2 — a burst from one render becomes one request. An interval-only flush delays
#: every registration by up to its full period, and lazy-loaded/streamed content is the
#: common case discovery targets.
DEFAULT_DEBOUNCE_SECONDS = 0.4

#: REF-1 - a display locale, in wire form, that may be written into a request path.
_LOCALE_SHAPE = re.compile(r"^[a-z]{2,8}(-[a-z0-9]{1,8})*$")

#: WIRE-4 - the unconfigured-client warnings this process has given, so each is given once.
_UNCONFIGURED_NOTICE: set[str] = set()
_UNCONFIGURED_GUARD = threading.Lock()


def _notice_unconfigured(message: str) -> None:
    with _UNCONFIGURED_GUARD:
        if message in _UNCONFIGURED_NOTICE:
            return
        _UNCONFIGURED_NOTICE.add(message)
    logger.warning(
        "%s Phrases render as source text, and nothing is fetched or registered until the "
        "client is configured.",
        message,
    )

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
        message_previous_category: Optional[str] = None,
        previous_categories: Optional[Mapping[str, Optional[str]]] = None,
        legacy_files: Optional[Sequence[Any]] = None,
        require_credentials: bool = False,
        miss_fallback: Optional[Callable[[Miss], Optional[str]]] = None,
        value_sets: Optional[Iterable[Any]] = None,
        runtime_registration: bool = True,
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
        # WIRE-4 - constructing without credentials never throws. The client is unconfigured:
        # reads render source, nothing is sent, an explicit write throws, and
        # `require_credentials` makes the first use throw instead.
        missing = self._config.missing()
        self._not_configured: Optional[str] = (
            "Langsys is not configured: it has no " + " and no ".join(missing) + "."
            if missing else None
        )
        self._require_credentials = require_credentials
        self._http = HttpClient(
            self._config.api_url, self._config.api_key, timeout=self._config.timeout,
            refuse=self._not_configured,
        )
        self._cache: CacheBackend = cache if cache is not None else FileCache()
        self._catalog = CatalogStore(
            self._http, self._config.project_id, self._cache, ttl=self._config.cache_ttl,
            configured=self._not_configured is None,
        )
        if self._not_configured is not None:
            _notice_unconfigured(self._not_configured)

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
        #: Each queued phrase with the previous category its registration carries (MOVE-3), or
        #: None when it did not move.
        self._pending: dict[tuple[str, str], Optional[str]] = {}
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
        #: MOVE-1 - the category the message category's templates were registered under before
        #: a rename; it wins over `previous_categories` for the message category.
        self.message_previous_category = message_previous_category
        #: MOVE-1 - `{current: previous}`, the move of a whole category at once: every item
        #: registered under `current` whose call names no previous category carries it. `''` is
        #: "no category before"; a category not in the map, or a None value, did not move.
        self._previous_categories: dict[str, Optional[str]] = dict(previous_categories or {})
        #: MOVE-1 - with debug on, a phrase moved out of a category while another call still
        #: renders it there is reported once: (category, phrase) pairs of each kind.
        self._moved_out: set[tuple[str, str]] = set()
        self._rendered_in: set[tuple[str, str]] = set()
        self._warned_left_behind: set[tuple[str, str]] = set()
        #: MIG-1 - legacy-key mode runs only when files are configured. Unset, no file is read
        #: and no key lookup happens. Loaded here, so a file this SDK cannot read (a `.mo`, an
        #: unsupported format) fails at configuration, naming the file.
        self._legacy = LegacyKeys(legacy_files) if legacy_files else None
        #: FRM-3 - the framework's own language-file line for a phrase the catalog does not
        #: translate, asked after the catalog and before the source. Its answer is never registered
        #: or cached.
        self._miss_fallback = miss_fallback
        #: FRM-7 - the declared value sets: a call whose param is a declared value of its
        #: placeholder looks up the sentence with that value written in.
        self.value_sets = ValueSets(value_sets or ())
        #: FRM-2 - off, the translate function and message emission register nothing at runtime;
        #: a framework binding that registers through its sync command turns it off. FRM-7's
        #: one exception still registers: a declared value whose sentence the catalog lacks while
        #: it holds another of the template's sentences. The page walk still collects.
        self.runtime_registration = runtime_registration
        #: SNAP-2 - a loaded snapshot's catalogs, per wire locale. Consulted before the fetch;
        #: whatever it does not hold falls through to the normal catalog path.
        self._preloaded: dict[str, Catalog] = {}
        #: SRV-6 - the served locales a loaded snapshot vouches for, used only while
        #: authorization is unavailable: (base_locale, [base, *locales]).
        self._snapshot_locales: Optional[tuple[str, list[str]]] = None
        #: MSG-11 - one warning per (template, marker) whose value is a catalogued phrase.
        self._warned_marker_values: set[tuple[str, str]] = set()
        self._utils = Utilities(
            self._http, self._config.project_id, self._cache, ttl=self._config.cache_ttl
        )
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

    # -- configuration (WIRE-4) -------------------------------------------------

    def is_configured(self) -> bool:
        """Whether the client has an API key and a project id. A configured client whose key the
        server refuses is still configured; that is authorization's question."""
        return self._not_configured is None

    def _assert_usable(self) -> None:
        """Under ``require_credentials``, an unconfigured client fails at its first use."""
        if self._not_configured is not None and self._require_credentials:
            raise NotConfiguredError(self._not_configured)

    def _refuse_write(self) -> None:
        """An explicit write from an unconfigured client throws rather than sending."""
        if self._not_configured is not None:
            raise NotConfiguredError(
                f"{self._not_configured} Nothing was registered, since nothing can be sent."
            )

    # -- authorization --------------------------------------------------------

    def authorize(self, force: bool = False) -> Project:
        """Validate the key against the project and return its metadata (cached).

        Project metadata is cacheable; the **write decision is not** and is never
        part of what this returns — see :meth:`_resolve_write_enabled`.
        """
        if self._not_configured is not None:
            raise NotConfiguredError(self._not_configured)
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
        # the live one was never recorded. A missing flag is an answer of false (GATE-8).
        self._observe_decision(live.get("write_enabled") is True)

        data = _without_write_decision(live)
        # Stripped before it reaches EITHER store. `Project` is held for the life of
        # the client, so letting the flag ride along in `raw` would latch the decision
        # in memory just as surely as caching it would (GATE-3).
        self._cache.set(cache_key, data, self._config.cache_ttl)
        self._project = Project.from_response(data)
        return self._project

    def _observe_decision(self, value: Optional[bool]) -> None:
        """Record a server-computed ``write_enabled``, from **either** endpoint shape.
        ``None`` is no answer at all (a catalog served from the cache) and changes nothing.

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
        """Already-known project metadata, if any. Never carries ``write_enabled``, so it never
        decides whether this session may write (GATE-8)."""
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
        """GATE-1 — the server decides whether this session may write: True, False, or None for
        unknown, which the send site holds on (GATE-2).

        The decision is the API's newest answer in this session, from either place it arrives:
        inside ``data`` on ``authorize-project``, or on the envelope of a live catalog read
        (:meth:`_observe_decision`). With no answer held, the API is asked now. A failure to ask
        is unknown, never permission.

        GATE-8 — an answer without ``write_enabled`` is not write-enabled, exactly as if it said
        false, whatever the key type: only a boolean ``true`` grants. A cached payload is not an
        answer: GATE-4 strips the flag before anything is cached, so the cached metadata, and a
        catalog served from the cache, decide nothing, and the decision stays unknown until the
        API answers. ``key_type`` never stands in for the flag.
        """
        if self._not_configured is not None:
            return False  # settled: nothing can be sent, so nothing waits on the answer
        observed = self._observed_decision
        if observed is not None:
            if observed[1] is False:
                self._notice_held_refusal()
            return observed[1]
        data = self._live_authorize_payload()
        if data is None:
            return None
        return self._decide(data)

    def _live_authorize_payload(self) -> Optional[dict[str, Any]]:
        try:
            return self._authorize_data()
        except (NetworkError, ApiError, ConfigurationError) as exc:
            # Never infer permission from a failure to ask.
            logger.warning("langsys: could not resolve write capability (%s).", exc)
            return None

    def _decide(self, data: dict[str, Any]) -> bool:
        """One live authorization answer's decision: only a boolean true is write-enabled."""
        decision = data.get("write_enabled") is True
        if "write_enabled" not in data:
            logger.debug("langsys: the authorization carried no write_enabled; not write-enabled.")
        self._observe_decision(decision)
        self._notice_unusable_capability(decision, data.get("key_type"))
        return decision

    def _notice_held_refusal(self) -> None:
        """OBS-1 for a "no" that arrived on a catalog envelope, which names no key type: the key
        type comes from the project metadata, held, cached or asked for once."""
        try:
            key_type = self.authorize().key_type
        except (NetworkError, ApiError, ConfigurationError):
            return
        self._notice_unusable_capability(False, key_type)

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
        """Whether this session may write: the API's newest answer in this session, asked for
        now when none is held. Never cached; a request boundary clears it — see GATE-3.

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
        self._assert_usable()
        loc = explicit or self._locale_source.get() or self._config.base_locale
        if not loc and self._not_configured is None:
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
        previous_category: Optional[str] = None,
    ) -> str:
        """Translate ``phrase`` (falling back to the phrase itself if untranslated),
        then interpolate ``params`` with locale-aware CLDR formatting.

        ``previous_category`` names the category the phrase was registered under before it moved
        (``""`` for none): its registration carries it, and until the new category has a
        translation the previous category's renders (MOVE-1, MOVE-4)."""
        return self._translate(phrase, category, params, locale, content_block_id,
                               previous_category, fallback=True).text

    def resolve(
        self,
        phrase: str,
        *,
        category: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        locale: Optional[str] = None,
        previous_category: Optional[str] = None,
    ) -> Resolved:
        """`translate()`, saying who wrote the text: `"catalog"`, `"fallback"` (the miss fallback's
        language-file line) or `"source"` (FRM-3). A binding whose framework prints a translation
        raw escapes catalog text only, and prints language-file text as the framework does (FRM-8).
        Never raises for a catalog it cannot have."""
        found = self._translate(phrase, category, params, locale, None, previous_category,
                                fallback=True)
        return Resolved(found.text, found.origin)

    def use_miss_fallback(
        self, fallback: Optional[Callable[[Miss], Optional[str]]]
    ) -> "LangsysClient":
        """Set the miss fallback (FRM-3): asked for the framework's own line when the catalog has
        no translation, given the phrase, the locale, the category and the argument as written."""
        self._miss_fallback = fallback
        return self

    def _translate(
        self,
        phrase: str,
        category: Optional[str],
        params: Optional[dict[str, Any]],
        locale: Optional[str],
        content_block_id: Optional[str],
        previous_category: Optional[str],
        *,
        fallback: bool = False,
    ) -> Lookup:
        """`translate()`, whether the text is the current category's translation, the previous
        category it came from instead, if it did (MOVE-5), and who wrote it. With `fallback`, the
        call is the translate function's, and a miss asks the miss fallback (FRM-3)."""
        argument = phrase
        # MIG-2 - in legacy-key mode the argument is a key first: a hit becomes its source value
        # (never the key) under its namespace, a miss stays literal source text.
        if self._legacy is not None and content_block_id is None:
            phrase, category, _ = self._legacy.resolve(phrase, category)
        return self._resolve_phrase(phrase, category, params, locale, content_block_id,
                                    previous_category, argument if fallback else None)

    #: Short alias mirroring ``t()`` across the other SDKs.
    t = translate

    def translate_legacy(
        self,
        text: str,
        *,
        entry_point: str = "gettext",
        plural: Optional[str] = None,
        category: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        locale: Optional[str] = None,
        previous_category: Optional[str] = None,
    ) -> str:
        """A framework translation function's call, through the same resolver as `t()` (MIG-8).

        For a binding's `gettext`/`_()`/`pgettext` (`entry_point="gettext"`, a `pgettext` context
        passed as `category`), `ngettext`/`npgettext` (`entry_point="ngettext"`, with `plural`),
        and `{% blocktranslate %}` (`entry_point="blocktranslate"`). With legacy files configured
        the text is a key first, exactly as `t()` resolves it, so both entry points register one
        phrase under one category for one key. A miss is literal source text in that framework's
        syntax, converted only where the call passes a value (MIG-2).
        """
        from .migrate import convert_literal, gettext_plural

        values = params or {}
        hit = False
        if self._legacy is not None:
            phrase, resolved, hit = self._legacy.resolve(text, category)
        if hit:
            category = resolved
        elif entry_point == "ngettext":
            if plural is None:
                raise ValueError("an ngettext call needs its plural form")
            phrase = gettext_plural(text, plural, literal=True, passed=values)
        else:
            phrase = convert_literal(text, entry_point, values)
        return self._resolve_phrase(phrase, category, params, locale, None, previous_category,
                                    text).text

    def _move_of(self, category: Optional[str], named: Optional[str]) -> Optional[str]:
        """MOVE-1 - the previous category an item under `category` carries: the one its call
        names, else the configured map's (the message category's own first). None is no move,
        and so is a previous category equal to the current one."""
        current = "" if category in (None, UNCATEGORIZED) else str(category)
        if named is not None and named != current:
            return named
        if current == self.message_category and self.message_previous_category is not None:
            moved: Optional[str] = self.message_previous_category
        else:
            moved = self._previous_categories.get(current)
        return None if moved is None or moved == current else moved

    def _resolve_phrase(
        self,
        phrase: str,
        category: Optional[str],
        params: Optional[dict[str, Any]],
        locale: Optional[str],
        content_block_id: Optional[str],
        previous_category: Optional[str] = None,
        argument: Optional[str] = None,
    ) -> Lookup:
        """One phrase lookup. `argument`, the call's argument as written, is given only on the
        translate function's path, where a miss asks the miss fallback (FRM-3)."""
        # TOK-2 - a code-registered key is stripped of C0 controls on lookup and on register
        # alike, so a phrase carrying one resolves to the same entry as the markup that holds it.
        phrase = strip_c0(phrase)
        loc = self._effective_locale(locale)
        call_params = params
        written: Optional[tuple[str, list[str]]] = None
        if argument is not None and params and self.value_sets:
            # FRM-7 - a declared value is written into its placeholder, and the sentence it makes
            # is the phrase; the template is never looked up for it.
            filled = self.value_sets.write_in(phrase, params)
            if filled is not None:
                written = (phrase, filled[2])
                phrase, params = filled[0], filled[1]
        moved = self._move_of(category, previous_category) if content_block_id is None else None
        self._note_use(phrase, category, moved)
        if content_block_id is None and self._preloaded:
            held, value = self._preloaded_entry(loc, category or UNCATEGORIZED, phrase)
            if held:
                current = _displayable(value)
                text, prior = (value if current else phrase), None
                if not current and moved is not None:
                    # MOVE-4 - the snapshot's entry under the previous category, by the same rule.
                    found, earlier = self._preloaded_entry(loc, moved or UNCATEGORIZED, phrase)
                    if found and _displayable(earlier):
                        text, prior = earlier, moved
                return self._finish(text, current, prior, phrase, category, params, loc, argument,
                                    call_params)
        fetch = self._catalog.get(loc)
        self._observe_decision(fetch.write_enabled)
        result = resolve(fetch.catalog, phrase, category, content_block_id)
        text, prior = result.text, None
        if moved is not None and not result.translated:
            # MOVE-4 - no translation under the current category: the previous category's entry
            # for the same text renders, until the current one has its own.
            earlier = previous_entry(fetch.catalog, moved, phrase)
            if earlier is not None:
                text, prior = earlier, moved
        # WIRE-4 — without a catalog a miss is indistinguishable from a hit, so an
        # outage would re-register everything that already exists. Record nothing.
        # CAT-1 - registration is decided under the current category alone.
        entries = fetch.catalog.get(category or UNCATEGORIZED)
        if (result.missing and content_block_id is None and fetch.ok
                and self._registers_at_runtime(argument, phrase, written, entries)):
            self._queue_missing(phrase, category, entries, previous_category=moved)
        if not fetch.ok and argument is not None:
            _notice_catalog_unavailable(self._not_configured)
        return self._finish(text, result.translated, prior, phrase, category, params, loc,
                            argument, call_params)

    def _registers_at_runtime(
        self,
        argument: Optional[str],
        sentence: str,
        written: Optional[tuple[str, list[str]]],
        entries: Any,
    ) -> bool:
        """FRM-2 - whether a miss on this path registers. The page walk always collects; the
        translate function only with runtime registration on, or for FRM-7's one exception: a
        declared value's sentence the catalog lacks while it holds another of the same template's
        sentences, which marks the template as synced and the value as added since."""
        if argument is None or self.runtime_registration:
            return True
        if written is None or not isinstance(entries, dict):
            return False
        siblings = self.value_sets.sentences(*written) or []
        return any(other != sentence and other in entries for other in siblings)

    def _finish(
        self,
        text: str,
        current: bool,
        prior: Optional[str],
        phrase: str,
        category: Optional[str],
        params: Optional[dict[str, Any]],
        loc: str,
        argument: Optional[str],
        call_params: Optional[dict[str, Any]] = None,
    ) -> Lookup:
        """FRM-3 - the catalog's translation; failing that, on the translate function's path, the
        miss fallback's line; failing that, the source. Whichever it is, filled with the params:
        the fallback's line, being the framework's own template, with every param of the call."""
        origin = "catalog" if current or prior is not None else "source"
        if origin == "source" and argument is not None and self._miss_fallback is not None:
            line = self._ask_fallback(Miss(phrase, loc, category, argument))
            if line is not None:
                text, origin, params = line, "fallback", call_params
        if params:
            text = interpolate(text, params, loc)
        return Lookup(text, current, prior, origin)

    def _ask_fallback(self, miss: Miss) -> Optional[str]:
        """The miss fallback's line, or None. A fallback that throws answers nothing (FRM-3: the
        chain never throws)."""
        assert self._miss_fallback is not None
        try:
            line = self._miss_fallback(miss)
        except Exception as exc:  # noqa: BLE001 - the chain never throws into a render
            logger.debug("langsys: the miss fallback raised %r for %r; rendering the source.",
                         exc, miss.argument)
            return None
        return line if isinstance(line, str) and line != "" else None

    def _note_use(self, phrase: str, category: Optional[str], moved: Optional[str]) -> None:
        """MOVE-1's SHOULD, with debug on: a phrase moved out of a category while another call
        still renders it there leaves that call behind, which is reported once per pair."""
        if not self._config.debug:
            return
        current = "" if category in (None, UNCATEGORIZED) else str(category)
        with self._lock:
            if moved is not None:
                self._moved_out.add((moved, phrase))
            else:
                self._rendered_in.add((current, phrase))
            pair = (moved, phrase) if moved is not None else (current, phrase)
            if (pair in self._moved_out and pair in self._rendered_in
                    and pair not in self._warned_left_behind):
                self._warned_left_behind.add(pair)
                logger.debug(
                    "langsys: %r is moved out of category %r by one call while another still "
                    "renders it there; the item stands for every use, so that call's text drops "
                    "out of the Translation Manager. Move every use, or give it its own text.",
                    phrase, pair[0],
                )

    # -- content blocks (server-side HTML) ------------------------------------

    def translate_content_block(
        self, html: str, category: Optional[str] = None, *, previous_category: Optional[str] = None
    ) -> str:
        """Translate a fragment of HTML as one unit (TOK-6). A fragment whose one token is its one
        text node is a phrase; anything else is a content block, returned stamped with its id
        (MARK-1). Untranslated content returns as authored and is queued for registration.
        ``previous_category`` is the category it was registered under before it moved (MOVE-1).
        Requires ``pip install langsys[html]``."""
        from .html.page import translate_fragment

        self._assert_usable()
        if not html or self._not_configured is not None:
            return html
        return translate_fragment(self, html, category, previous_category)

    def _render_block(
        self,
        html: str,
        category: Optional[str],
        phrases: list[str],
        previous_category: Optional[str] = None,
    ) -> str:
        """Look a content block up under its id, render it, and stamp it; queue it on a miss."""
        from .html.parser import apply_block_translations, placeholder_html, stamp_content_block

        loc = self._effective_locale(None)
        attrs = self._translatable_attributes
        cat_name = category or UNCATEGORIZED
        moved = self._move_of(category, previous_category)
        # CID-2 — the hash takes the *raw* category, `''` when there is none.
        # `__uncategorized__` is a cache-lookup namespace and must never reach the id.
        # MOVE-2 - the current category alone: a previous category is never part of identity.
        custom_id = generate_custom_id(category, phrases)
        held, preloaded = self._preloaded_entry(loc, cat_name, custom_id)
        if held and isinstance(preloaded, dict):
            render: dict[str, Any] = preloaded
            prior_id: Optional[str] = None
            if moved is not None and not block_translated(preloaded):
                prior_key = generate_custom_id(moved, phrases)
                found, earlier = self._preloaded_entry(loc, moved or UNCATEGORIZED, prior_key)
                if found and block_translated(earlier):
                    render, prior_id = earlier, prior_key
            translated = apply_block_translations(html, render, attrs, locale=loc)
            return stamp_content_block(translated, custom_id, previous=prior_id)
        fetch = self._catalog.get(loc)
        self._observe_decision(fetch.write_enabled)
        cat = fetch.catalog.get(cat_name)
        block = lookup_block(cat, category, custom_id, phrases)
        found_block, prior_id = previous_block(fetch.catalog, block, moved, phrases)
        if not isinstance(block, dict) and fetch.ok:
            # WIRE-4 — see translate(): never queue off a catalog we could not read.
            # VAR-3 - stored content holds each marked value as its placeholder.
            self._queue_content_block(placeholder_html(html), cat_name, custom_id, phrases,
                                      previous_category=moved)
        if found_block is None:
            # MARK-1 — the id is what the block IS, not what the catalog happened to
            # hold. An unstamped miss is the case you most need to inspect.
            return stamp_content_block(html, custom_id)
        translated = apply_block_translations(html, found_block, attrs, locale=loc)
        return stamp_content_block(translated, custom_id, previous=prior_id)

    def translate_page(
        self,
        html: str,
        category: Optional[str] = None,
        selector_categories: Optional[dict[str, Any]] = None,
        *,
        previous_category: Optional[str] = None,
    ) -> str:
        """Translate a whole HTML document (head + body) in place, classifying each
        block as a phrase or a content block. ``previous_category`` is the previous category
        of ``category``, the page's default (MOVE-1); a selector's config and an element's
        ``data-ls-previous-category`` name their own. Requires ``pip install langsys[html]``."""
        from .html.page import translate_page

        self._assert_usable()
        if self._not_configured is not None:
            return html  # WIRE-4: the page as given, as when the catalog failed to load
        return translate_page(self, html, category, selector_categories, previous_category)

    def _queue_content_block(
        self,
        html: str,
        category: str,
        custom_id: str,
        phrases: list[str],
        *,
        previous_category: Optional[str] = None,
    ) -> None:
        with self._lock:
            existed = custom_id in self._pending_blocks
            entry = self._pending_blocks.setdefault(
                custom_id,
                {"content": html, "category": category, "custom_id": custom_id, "phrases": phrases},
            )
            # MOVE-3 - the first previous category named is kept; a later call naming none
            # never clears it, so an unmoved use rendered first cannot drop a move.
            if previous_category is not None and entry.get("previous_category") is None:
                entry["previous_category"] = previous_category
            self._tag(self._block_scopes, custom_id, existed)
        self._schedule_flush()

    # -- preloaded catalog (SNAP-2) ------------------------------------------------

    def load_snapshot(self, snapshot: Any) -> None:
        """Seed this client from a catalog snapshot, synchronously, with no network call.

        `snapshot` is a `Snapshot`, a path, or the snapshot's JSON. Lookups it answers need no
        fetch; a phrase or block it does not hold falls back to the normal catalog fetch, and to
        source text when the API cannot be reached. The snapshot is a cache: `Snapshot.load`
        refuses one edited after export, and one exported for another project is refused here.
        """
        from .snapshot import Snapshot, SnapshotError

        loaded = snapshot if isinstance(snapshot, Snapshot) else Snapshot.load(snapshot)
        if loaded.project_id != self._config.project_id:
            raise SnapshotError(
                f"This snapshot is for project {loaded.project_id!r}, not "
                f"{self._config.project_id!r}; export one for this project."
            )
        for locale in loaded.locales:
            held = loaded.catalog(locale)
            if held is not None:
                self._preloaded[normalize_locale(locale)] = held
        self._snapshot_locales = (loaded.base_locale, [loaded.base_locale, *loaded.locales])

    def _preloaded_entry(self, locale: str, category: str, key: str) -> tuple[bool, Any]:
        """(held, value) for one entry of a loaded snapshot - only until the live catalog for the
        locale is held. The live catalog outranks the snapshot once fetched, and whether a phrase
        is registered is decided against it alone: a snapshot hit decides nothing (SNAP-2)."""
        wire = normalize_locale(locale)
        if wire not in self._preloaded or self._catalog._held(wire) is not None:
            return False, None
        entries = self._preloaded[wire].get(category)
        if isinstance(entries, dict) and key in entries:
            return True, entries[key]
        return False, None

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
            return self._snapshot_locales[0] if self._snapshot_locales is not None else ""

    # -- request locale (SRV-6) ------------------------------------------------

    def resolve_request_locale(
        self,
        *,
        framework: Optional[str] = None,
        url: Optional[str] = None,
        cookie: Optional[str] = None,
        accept_language: Optional[str] = None,
        uses_cookie: bool = True,
    ) -> LocaleChoice:
        """The locale to serve this request in (SRV-6), in the project's lowercase form.

        `framework` is the locale the framework or app already resolved: it decides, mapped and
        validated (a bare `es` is authorization's default Spanish locale; an unsupported value is
        the base), with no `Vary`. With nothing resolved, the SDK takes the URL, then the cookie
        or session, then `Accept-Language`, then the base, and the result names the `Vary`
        headers the response must carry. See `langsys.request_locale`.

        If the project's locales cannot be read (WIRE-4), a loaded snapshot's locales are served;
        without one, only the configured base locale.
        """
        try:
            project = self.authorize()
            supported = [project.base_locale, *project.target_locales]
            base = project.base_locale
            default_locales = dict(project.default_locales)
        except (NetworkError, ApiError, ConfigurationError) as exc:
            if self._not_configured is None:  # an unconfigured client has said so once already
                logger.warning("langsys: could not read the project's locales (%s).", exc)
            if self._snapshot_locales is not None:
                # A loaded snapshot vouches for the locales it holds and the project's base,
                # until authorization answers and replaces them.
                base, supported = self._snapshot_locales[0], list(self._snapshot_locales[1])
            else:
                base = self._config.base_locale or ""
                supported = [base] if base else []
            default_locales = {}
        return resolve_request_locale(
            supported, base, framework=framework, url=url, cookie=cookie,
            accept_language=accept_language, uses_cookie=uses_cookie,
            default_locales=default_locales,
        )

    # -- server messages (MSG) -------------------------------------------------

    def server_message(
        self,
        template: str,
        params: Optional[dict[str, Any]] = None,
        *,
        field: Any = None,
        code: Any = None,
    ) -> Entry:
        """Build the entry a server sends for a failure (MSG-1, MSG-4), and act on it.

        `template` is the framework's own unfilled sentence with the label written in (MSG-3);
        `field` and `code` are the framework's own, passed through (MSG-1, MSG-2).

        MSG-8 - a template the catalog does not list yet is queued for registration on the ordinary
        flush path, so it is sent after the response (inside a request scope) and never blocks the
        request; a session that cannot write discards it like any other miss.
        MSG-11 - a marker filled with a string that is itself a phrase in the catalog warns once
        per `(template, marker)`: a translatable value in a marker is never translated.
        """
        from .messages import server_message, template_markers, warn_translatable_marker_value

        entry = server_message(template, params, field=field, code=code)
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
        # MSG-6 - registered or not is decided under the message category alone. FRM-2 - with
        # runtime registration off, the sync command registers templates and emission does not.
        if self.runtime_registration and not (isinstance(known, dict) and template in known):
            self._queue_missing(template, category, known,
                                previous_category=self._move_of(category, None))
        return entry

    def render_server_message(
        self, entry: Entry, category: Optional[str] = None, locale: Optional[str] = None
    ) -> str:
        """Render a received entry: `t(template, category, params)` when the catalog translates
        the template, `message` otherwise. `message` is filled, possibly localised text and is
        never a lookup key (MSG-5). With the message category's previous category configured, a
        template the category does not translate yet renders the previous category's entry
        before falling back to `message` (MSG-6, MOVE-4)."""
        category = category or self.message_category
        fetch = self._catalog.get(self._effective_locale(locale))
        translations = fetch.catalog.get(category) if fetch.ok else None
        template = entry.get("template")
        value = (
            translations.get(template)
            if isinstance(translations, dict) and isinstance(template, str)
            else None
        )
        moved = self._move_of(category, None)
        if not _displayable(value) and moved is not None and fetch.ok and isinstance(template, str):
            value = previous_entry(fetch.catalog, moved, template)
        if not isinstance(value, str) or not value:
            message = entry.get("message")
            if isinstance(message, str):
                return message
            return fill_template(template, entry.get("params")) if isinstance(template, str) else ""
        params = entry.get("params") or {}
        return interpolate(value, params, self._effective_locale(locale)) if params else value

    def import_legacy_translations(
        self, targets: Mapping[str, Sequence[Union[str, Path, LegacyFile]]]
    ) -> dict[str, Any]:
        """Import the translations an app's legacy files already hold (MIG-9), once, before its
        target-language files are deleted.

        `targets` maps each target locale to its files, given as `legacy_files` gives the source
        language's. Every key of the source files registers its phrase as a lookup of that key
        would, together with each locale's value for the same key, converted the same way; the
        API stores those as human translations, so work already done shows at once and is not
        machine translated again. A value that is missing, empty, fuzzy, does not convert, or is
        a gettext plural whose forms cannot be mapped to CLDR categories is not a translation:
        the phrase still registers, that locale is left for machine translation, and the key is
        listed in `not_imported` with its reason; `plural_entries_unmapped` counts the plurals.
        A session that may not write imports nothing and says why (`skipped`, `reason`), and a
        refused send is `reason: "registration-failed"`, never success.

        Raises `ConfigurationError` when no source files are configured or a locale is not a
        target of the project, before anything is read or sent.
        """
        self._refuse_write()
        if self._legacy is None:
            raise ConfigurationError(
                "Langsys: nothing to import: no source-language files are configured "
                "(pass legacy_files=)."
            )
        result: dict[str, Any] = {
            "success": False, "reason": None, "phrases": 0, "translations": 0,
            "human_translations_saved": 0, "human_translations_skipped": 0,
            "plural_entries_unmapped": 0, "not_imported": [],
        }
        decision = self._resolve_write_enabled()
        if decision is not True:
            result.update(skipped=True, reason="not-write-enabled" if decision is False
                          else "capability-unknown")
            return result
        project = self.authorize()
        targeted = {normalize_locale(t) for t in project.target_locales}
        base = normalize_locale(project.base_locale) if project.base_locale else None
        for locale in targets:
            wire = normalize_locale(locale)
            if wire not in targeted or wire == base:
                raise ConfigurationError(
                    f"Langsys: {locale} is not a target locale of this project, so its "
                    f"translations cannot be imported (targets: {', '.join(sorted(targeted))})."
                )
        readers = [LegacyTranslations(normalize_locale(locale), files)
                   for locale, files in targets.items()]

        items: dict[tuple[str, str], dict[str, Any]] = {}
        for key, entry in self._legacy.entries.items():
            phrase = strip_c0(entry.phrase)
            if not phrase.strip():
                continue
            category = entry.category or UNCATEGORIZED
            item = items.setdefault((category, phrase), {
                "phrase": phrase, "category": None if category == UNCATEGORIZED else category,
                "translations": {}})
            for reader in readers:
                value = reader.get(key)
                if value.translation is None:
                    listed = {"key": key[1], "context": key[0], "locale": reader.locale,
                              "reason": value.reason}
                    if value.detail:
                        listed["detail"] = value.detail
                    result["not_imported"].append(listed)
                    if value.reason == SKIP_PLURAL_FORMS:
                        result["plural_entries_unmapped"] += 1
                    continue
                item["translations"].setdefault(reader.locale, value.translation)
        for reader in readers:
            for key in reader.values:
                if key not in self._legacy.entries:
                    result["not_imported"].append({"key": key[1], "context": key[0],
                                                   "locale": reader.locale,
                                                   "reason": "no-source-key"})

        try:
            responses = self._reg.register_phrases([self._with_move(i) for i in items.values()])
        except (NetworkError, ApiError) as exc:
            logger.warning("langsys: the import was refused (%s); nothing is marked imported.",
                           exc)
            result.update(reason="registration-failed", error=str(exc))
            return result
        for response in responses:
            data = response.get("data") if isinstance(response, dict) else None
            for field_name in ("human_translations_saved", "human_translations_skipped"):
                count = data.get(field_name) if isinstance(data, dict) else None
                result[field_name] += count if isinstance(count, int) else 0
        result.update(success=True, phrases=len(items),
                      translations=sum(len(i["translations"]) for i in items.values()))
        self._catalog.clear()  # the next read serves what was imported
        logger.info("langsys: imported %d phrase(s) with %d translation(s): %d saved as human "
                    "translations, %d left for machine translation by the plan's quota, %d "
                    "plural entr(ies) whose forms could not be mapped.", result["phrases"],
                    result["translations"], result["human_translations_saved"],
                    result["human_translations_skipped"], result["plural_entries_unmapped"])
        return result

    def register_templates(
        self, templates: Iterable[str], *, category: Optional[str] = None
    ) -> int:
        """MSG-7 - register every listed template the catalog does not hold yet, under the message
        category. Idempotent: a second run registers nothing. Returns how many were registered."""
        self._refuse_write()
        category = category or self.message_category
        fetch = self._catalog.get(self._effective_locale(None), use_cache=False)
        if not fetch.ok:
            raise NetworkError("Langsys: the catalog could not be read, so nothing was registered.")
        known = fetch.catalog.get(category)
        new = [t for t in templates if not (isinstance(known, dict) and t in known)]
        if new:
            # MOVE-1 - each carries the message category's previous category, when configured.
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
        self,
        phrase: str,
        category: Optional[str],
        catalog_category: Any = None,
        *,
        previous_category: Optional[str] = None,
    ) -> None:
        key = (category or UNCATEGORIZED, phrase)
        if self._ellipsis_suppresses(phrase, key, catalog_category):
            return
        with self._lock:
            existed = key in self._pending
            # MOVE-3 - the first previous category named is kept, and none never clears it.
            if not existed or self._pending[key] is None:
                self._pending[key] = previous_category
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
                {"phrase": phrase, "category": category,
                 **({"previous_category": moved} if moved is not None else {})}
                for (category, phrase), moved in self._pending.items()
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
        if self._not_configured is not None:
            self.clear_pending()
            return {"phrases": 0, "content_blocks": 0, "success": False, "skipped": True,
                    "reason": "not-configured"}
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
            if self._catalog.unavailable():
                # REG-10 - nothing was queued because a catalog could not be read (WIRE-4), so
                # misses went undecided: a skipped write, and it says so.
                return {"phrases": 0, "content_blocks": 0, "success": False, "skipped": True,
                        "reason": "catalog-unavailable"}
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
        # A session holding no answer asks at the send site, so without this any network
        # wobble would discard everything.
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
                _queued_phrase(phrase, category, self._pending[(category, phrase)])
                for (category, phrase) in phrase_keys
            ]
            sent_moves = {key: self._pending[key] for key in phrase_keys}
            blocks = [dict(self._pending_blocks[b]) for b in block_ids]
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
                "reason": "registration-failed",
                "error": str(exc),
                "queued_phrases": phrase_count,
                "queued_content_blocks": block_count,
                "retry_in_seconds": round(delay, 3),
            }

        self._reset_backoff()
        with self._lock:
            # MOVE-3 - an item that named its previous category only while this send was out is
            # kept, so its move is sent: the API copies and retires once, and a repeat does nothing.
            for key in phrase_keys:
                if key in self._pending and self._pending[key] != sent_moves[key]:
                    continue
                self._pending.pop(key, None)
                self._phrase_scopes.pop(key, None)
            for block in blocks:
                block_id = block["custom_id"]
                queued = self._pending_blocks.get(block_id)
                if queued is not None and (queued.get("previous_category")
                                           != block.get("previous_category")):
                    continue
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
        """Register phrases (strings, or ``{"phrase", "category"?, "previous_category"?,
        "translatable"?}``). An item naming no previous category takes the configured map's
        (MOVE-1)."""
        self._require_write()
        return self._reg.register_phrases([self._with_move(p) for p in phrases])

    def _with_move(self, phrase: PhraseInput) -> PhraseInput:
        """MOVE-1 - an explicit item with the previous category its call or the map gives it."""
        text = phrase if isinstance(phrase, str) else phrase["phrase"]
        category = None if isinstance(phrase, str) else phrase.get("category")
        named = None if isinstance(phrase, str) else phrase.get("previous_category")
        moved = self._move_of(category, named)
        if isinstance(phrase, str):
            return phrase if moved is None else {"phrase": text, "previous_category": moved}
        item = {k: v for k, v in phrase.items() if k != "previous_category"}
        if moved is not None:
            item["previous_category"] = moved
        return item

    def register_content_block(
        self,
        content: str,
        phrases: Sequence[str],
        *,
        category: Optional[str] = None,
        custom_id: Optional[str] = None,
        label: Optional[str] = None,
        previous_category: Optional[str] = None,
    ) -> dict[str, Any]:
        """Register a content block. ``phrases`` are its child phrases (auto-extraction
        from HTML arrives with page translation in phase 3). ``previous_category`` is the
        category it was registered under before it moved (MOVE-1); its id is the current
        category's (MOVE-2)."""
        self._require_write()
        return self._reg.register_content_block(
            content, phrases, category=category, custom_id=custom_id, label=label,
            previous_category=self._move_of(category, previous_category),
        )

    def sync(
        self, local_phrases: Sequence[PhraseInput], locale: Optional[str] = None
    ) -> dict[str, Any]:
        """Register any of ``local_phrases`` not already in the catalog, then refetch."""
        self._refuse_write()
        loc = self._effective_locale(locale)
        fetch = self._catalog.get(loc, use_cache=False)
        self._observe_decision(fetch.write_enabled)
        if not fetch.ok:
            # WIRE-4 — without a catalog every phrase looks new; registering them
            # all is the write storm this guard exists to prevent.
            logger.warning("langsys: sync skipped — the catalog could not be read.")
            return {"new_phrases": [], "synced": False, "success": False, "skipped": True,
                    "reason": "catalog-unavailable"}
        existing = _existing_keys(fetch.catalog)

        # MOVE-1 - one item stands for every entry naming its text and category: the first
        # previous category an entry names is the one sent, and entries that disagree are reported.
        new_items: list[PhraseInput] = []
        by_key: dict[str, int] = {}
        conflicts: list[dict[str, Any]] = []
        for phrase in local_phrases:
            text = phrase if isinstance(phrase, str) else phrase["phrase"]
            category = None if isinstance(phrase, str) else phrase.get("category")
            key = f"{category or UNCATEGORIZED}::{text}"
            if key in existing:
                continue
            named = None if isinstance(phrase, str) else phrase.get("previous_category")
            current = "" if category in (None, UNCATEGORIZED) else category
            named = None if named == current else named
            if key not in by_key:
                by_key[key] = len(new_items)
                new_items.append(phrase)
                continue
            first = new_items[by_key[key]]
            earlier = None if isinstance(first, str) else first.get("previous_category")
            earlier = None if earlier == current else earlier
            if named is None:
                continue
            if earlier is None:
                new_items[by_key[key]] = {**({"phrase": first} if isinstance(first, str)
                                             else first), "previous_category": named}
            elif earlier != named:
                conflicts.append({"phrase": text, "category": category,
                                  "previous_categories": [earlier, named]})
                logger.warning(
                    "langsys: %r under %r names two previous categories, %r and %r; sending the "
                    "first.", text, current, earlier, named)
        new_items = [self._with_move(p) for p in new_items]

        names = [p if isinstance(p, str) else p["phrase"] for p in new_items]
        synced = False
        if new_items:
            decision = self._resolve_write_enabled()
            if decision is not True:
                # REG-10 - a skipped write says why, and is never success-shaped.
                return {"new_phrases": names, "synced": False, "success": False, "skipped": True,
                        "reason": "not-write-enabled" if decision is False
                        else "capability-unknown", "move_conflicts": conflicts}
            self._reg.register_phrases(new_items)
            self._catalog.clear(loc)
            self._catalog.get(loc, use_cache=False)
            synced = True

        return {"new_phrases": names, "synced": synced, "success": True,
                "move_conflicts": conflicts}

    def _require_write(self) -> None:
        """Gate an explicit registration call, distinguishing denial from ignorance.

        Both refuse, but a caller debugging "why did nothing register" is served very
        differently by "the server says this session may not write" and "we could not
        reach the server to ask".
        """
        self._refuse_write()
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
                self._http, self._config.project_id, batch_limit=self.authorize().batch_limit,
                credentials=(self._config.api_url, self._config.api_key),
            )
        return self._registrar

    # -- reference lists (REF-1) ------------------------------------------------

    def _display_locale(self, in_locale: Optional[str]) -> Optional[str]:
        """The display locale a reference list is named in: the one given, else the current one,
        normalised as the current locale is, in wire form. None when the core is unconfigured or
        the result is not shaped as a locale, which is then never written into a request path."""
        self._assert_usable()
        if self._not_configured is not None:
            return None
        display = normalize_locale(self._effective_locale(in_locale))
        if not _LOCALE_SHAPE.match(display):
            logger.warning(
                "langsys: %r is not shaped as a locale, so it was not sent as a display locale; "
                "answering the list empty.",
                in_locale if in_locale is not None else display,
            )
            return None
        return display

    def countries(self, in_locale: Optional[str] = None) -> list[Country]:
        """The countries, named in the display locale (`code`, `label`)."""
        return self._utils.countries(self._display_locale(in_locale))

    def dial_codes(self, in_locale: Optional[str] = None) -> list[DialCode]:
        """The countries' dial codes, named in the display locale."""
        return self._utils.dial_codes(self._display_locale(in_locale))

    def currencies(self, in_locale: Optional[str] = None) -> list[Currency]:
        """The currencies, named in the display locale."""
        return self._utils.currencies(self._display_locale(in_locale))

    def country_name(self, code: str, in_locale: Optional[str] = None) -> str:
        return self._utils.country_name(code, self._display_locale(in_locale))

    def currency_name(self, code: str, in_locale: Optional[str] = None) -> str:
        return self._utils.currency_name(code, self._display_locale(in_locale))

    def locales(self, in_locale: Optional[str] = None) -> dict[str, list[LocaleFlat]]:
        """The locales grouped by language name, named in the display locale."""
        return self._utils.locales(self._display_locale(in_locale))

    def locales_flat(self, in_locale: Optional[str] = None) -> list[LocaleFlat]:
        """The locales as `code` and `name`, named in the display locale."""
        return self._utils.locales_flat(self._display_locale(in_locale))

    def locales_data(self, in_locale: Optional[str] = None) -> list[LocaleInfo]:
        """The locales as `code`, `locale_name` and `lang_name`, named in the display locale."""
        return self._utils.locales_data(self._display_locale(in_locale))

    def locale_name(
        self, for_locale: str, short: bool = False, in_locale: Optional[str] = None
    ) -> str:
        return self._utils.locale_name(for_locale, short, self._display_locale(in_locale))

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


#: FRM-3 - why the translate function had no catalog, reported once per process, at debug.
_CATALOG_NOTICE: set[str] = set()


def _notice_catalog_unavailable(not_configured: Optional[str]) -> None:
    cause = not_configured or "the catalog could not be loaded"
    if _CATALOG_NOTICE:
        return
    _CATALOG_NOTICE.add(cause)
    logger.debug(
        "langsys: %s The translate function renders the miss fallback's line, or the source, "
        "until a catalog can be read; this is reported once.", cause,
    )


def _queued_phrase(phrase: str, category: str, moved: Optional[str]) -> dict[str, Any]:
    """A queued miss as the item it registers: its previous category only when it moved."""
    item: dict[str, Any] = {"phrase": phrase,
                            "category": None if category == UNCATEGORIZED else category}
    if moved is not None:
        item["previous_category"] = moved
    return item


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
