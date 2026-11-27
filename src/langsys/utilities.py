"""Reference lists: locales, currencies, countries and dial codes, named in a display locale.

REF-1 - each list comes from the API (nothing is bundled) and is held per display locale: in memory
for the life of the client, and in the client's cache backend under a project-namespaced key with
the catalog's TTL, so a client built for a later request answers it without a request. Concurrent
reads of one list and display locale share one request.

A locale list asks for its one display locale with `append_target_locales=0` and no project id,
since otherwise the API appends the project's target locales in an order nothing can rely on. It
answers the entry whose key, in the lowercase hyphenated form, is the display locale, whatever form
the organisation writes codes in, with every `code` in that form; so a list depends on its display
locale alone. A locale the API holds no entry for answers an empty list, held like any answer.

Nothing here throws. A failed read, or an answer not of the list's shape, answers the list's empty
value with the cause logged, and is remembered for CACHE-2's window (3s, doubling to 300s, reset by
the first success), inside which the call answers empty without a request. The window is this
client's, so a client under other credentials is not held back by it. A failure is never held as a
list or written to the cache.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional, TypeVar

from . import catalog as _catalog
from .cache import CacheBackend
from .exceptions import ApiError, LangsysError
from .http import HttpClient, encode_segment
from .locale import normalize_locale
from .types import Country, Currency, DialCode, LocaleFlat, LocaleInfo

logger = logging.getLogger("langsys")

T = TypeVar("T")

#: The locale forms, by list name: the route, and whether an entry is a list (flat, data) or a
#: map of language name to list (the grouped index).
_LOCALE_LISTS = {"locales": ("locales", False), "locales-flat": ("locales/flat", True),
                 "locales-data": ("locales/data", True)}


def _country(row: dict[str, Any]) -> Country:
    return Country(code=str(row.get("code", "")), label=str(row.get("label", "")))


def _dial_code(row: dict[str, Any]) -> DialCode:
    return DialCode(country_code=str(row.get("country_code", "")),
                    dial_code=str(row.get("dial_code", "")), name=str(row.get("name", "")))


def _currency(row: dict[str, Any]) -> Currency:
    return Currency(code=str(row.get("code", "")), name=str(row.get("name", "")),
                    symbol=str(row.get("symbol", "")),
                    symbol_native=str(row.get("symbol_native", "")),
                    decimal_digits=int(row.get("decimal_digits", 2) or 0),
                    rounding=float(row.get("rounding", 0) or 0))


def _locale_flat(row: dict[str, Any]) -> LocaleFlat:
    return LocaleFlat(code=str(row.get("code", "")), name=str(row.get("name", "")))


def _locale_info(row: dict[str, Any]) -> LocaleInfo:
    return LocaleInfo(code=str(row.get("code", "")), locale_name=str(row.get("locale_name", "")),
                      lang_name=str(row.get("lang_name", "")))


def _rows(value: Any, build: Callable[[dict[str, Any]], T]) -> list[T]:
    return [build(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


class Utilities:
    """The reference lists one client holds. Display locales arrive already resolved and
    validated by the client (`LangsysClient._display_locale`)."""

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
        self._held: dict[tuple[str, str], Any] = {}
        #: (list, display locale) -> (monotonic time the window ends, its length)
        self._failures: dict[tuple[str, str], tuple[float, float]] = {}
        self._fetching: dict[tuple[str, str], threading.Lock] = {}
        self._guard = threading.Lock()

    # -- the lists ----------------------------------------------------------------------------

    def countries(self, display: Optional[str]) -> list[Country]:
        return _rows(self._list("countries", f"countries/{encode_segment(display or '')}", display),
                     _country)

    def dial_codes(self, display: Optional[str]) -> list[DialCode]:
        path = f"countries/dial-codes/{encode_segment(display or '')}"
        return _rows(self._list("dial-codes", path, display), _dial_code)

    def currencies(self, display: Optional[str]) -> list[Currency]:
        return _rows(self._list("currencies", f"currencies/{encode_segment(display or '')}",
                                display), _currency)

    def locales_flat(self, display: Optional[str]) -> list[LocaleFlat]:
        return _rows(self._list("locales-flat", None, display), _locale_flat)

    def locales_data(self, display: Optional[str]) -> list[LocaleInfo]:
        return _rows(self._list("locales-data", None, display), _locale_info)

    def locales(self, display: Optional[str]) -> dict[str, list[LocaleFlat]]:
        """Locales grouped by language name (the ``/locales`` index form)."""
        grouped = self._list("locales", None, display)
        if not isinstance(grouped, dict):
            return {}
        return {str(lang): _rows(rows, _locale_flat) for lang, rows in grouped.items()}

    # -- helpers built on them ------------------------------------------------------------------

    def country_name(self, code: str, display: Optional[str]) -> str:
        if not code:
            return ""
        for country in self.countries(display):
            if country.code.lower() == code.lower():
                return country.label
        return code

    def currency_name(self, code: str, display: Optional[str]) -> str:
        if not code:
            return ""
        for currency in self.currencies(display):
            if currency.code.lower() == code.lower():
                return currency.name
        return code

    def locale_name(self, for_locale: str, short: bool, display: Optional[str]) -> str:
        if not for_locale:
            return ""
        target = normalize_locale(for_locale)
        for info in self.locales_data(display):
            if normalize_locale(info.code) == target:
                return info.lang_name if short else info.locale_name
        return for_locale

    def clear(self) -> None:
        """Drop every held list, here and in the cache, so the next call reads it again."""
        with self._guard:
            held = list(self._held)
            self._held.clear()
            self._failures.clear()
        for name, display in held:
            try:
                self._cache.delete(self._key(name, display))
            except Exception as exc:  # a cache that fails never fails the call
                logger.debug("langsys: a reference list was not dropped from the cache (%s).", exc)

    # -- internals ------------------------------------------------------------------------------

    def _key(self, name: str, display: str) -> str:
        return f"reference_{self._project_id}_{name}_{display}"

    def _empty(self, name: str) -> Any:
        return {} if name == "locales" else []

    def _list(self, name: str, path: Optional[str], display: Optional[str]) -> Any:
        """A list's raw rows (a map of rows for the grouped index), held or read now. A display
        locale of None (refused, or the client is unconfigured) answers the empty value."""
        if display is None:
            return self._empty(name)
        slot = (name, display)
        held = self._held_list(slot)
        if held is not None:
            return held
        if self._in_window(slot):
            return self._empty(name)
        with self._lock_for(slot):
            held = self._held_list(slot)
            if held is not None:
                return held
            if self._in_window(slot):
                return self._empty(name)
            data = self._read(name, path, display)
            if data is None:
                self._note_failure(slot)
                return self._empty(name)
            with self._guard:
                self._failures.pop(slot, None)
                self._held[slot] = data
            try:
                self._cache.set(self._key(name, display), data, self._ttl)
            except Exception as exc:
                logger.debug("langsys: a reference list was not cached (%s).", exc)
            return data

    def _held_list(self, slot: tuple[str, str]) -> Any:
        held = self._held.get(slot)
        if held is not None:
            return held
        try:
            cached = self._cache.get(self._key(*slot))
        except Exception:
            cached = None
        if isinstance(cached, (list, dict)):
            with self._guard:
                self._held[slot] = cached
            return cached
        return None

    def _read(self, name: str, path: Optional[str], display: str) -> Any:
        """One read of the API: the list's rows, or None when it failed or was not its shape."""
        locale_form = _LOCALE_LISTS.get(name)
        route = locale_form[0] if locale_form else str(path)
        params = {"locales[]": display, "append_target_locales": 0} if locale_form else None
        try:
            response = self._http.get(route, params=params)
        except LangsysError as exc:
            status = f", HTTP {exc.status_code}" if isinstance(exc, ApiError) else ""
            logger.warning("langsys: the %s list for %s could not be read (%s%s); answering it "
                           "empty.", name, display, exc, status)
            return None
        data = response.get("data") if isinstance(response, dict) else None
        rows = self._entry(data, display, locale_form[1]) if locale_form else (
            data if isinstance(data, list) else None)
        if rows is None:
            logger.warning("langsys: the %s list for %s answered something not of the list's "
                           "shape; answering it empty.", name, display)
        return rows

    @staticmethod
    def _entry(data: Any, display: str, flat: bool) -> Any:
        """The display locale's entry of a locale list keyed by locale, every code in wire form;
        an empty list when the API holds none; None when the answer is not of the shape."""
        if not isinstance(data, dict):
            return None
        for key, entry in data.items():
            if normalize_locale(str(key)) != display:
                continue
            if flat:
                return Utilities._wire_codes(entry) if isinstance(entry, list) else None
            if not isinstance(entry, dict):
                return None
            return {str(lang): Utilities._wire_codes(rows) for lang, rows in entry.items()
                    if isinstance(rows, list)}
        return {} if not flat else []

    @staticmethod
    def _wire_codes(rows: list[Any]) -> list[Any]:
        return [{**row, "code": normalize_locale(row["code"])}
                if isinstance(row, dict) and isinstance(row.get("code"), str) else row
                for row in rows]

    def _lock_for(self, slot: tuple[str, str]) -> threading.Lock:
        with self._guard:
            return self._fetching.setdefault(slot, threading.Lock())

    def _in_window(self, slot: tuple[str, str]) -> bool:
        failure = self._failures.get(slot)
        return failure is not None and _catalog._clock() < failure[0]

    def _note_failure(self, slot: tuple[str, str]) -> None:
        with self._guard:
            previous = self._failures.get(slot)
            window = (_catalog.FAILURE_WINDOW_INITIAL if previous is None
                      else min(previous[1] * 2, _catalog.FAILURE_WINDOW_MAX))
            self._failures[slot] = (_catalog._clock() + window, window)
