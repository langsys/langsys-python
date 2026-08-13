"""Reference-data helpers: countries, dial codes, currencies, and locale names.

Everything is fetched live from nova (nothing is bundled) and cached per display
locale. Names are returned already localized into the requested locale.
"""

from __future__ import annotations

from typing import Any

from .http import HttpClient, encode_segment
from .locale import normalize_locale
from .types import Country, Currency, DialCode, LocaleFlat, LocaleInfo


class Utilities:
    def __init__(self, http: HttpClient, project_id: str) -> None:
        self._http = http
        self._project_id = project_id
        self._countries: dict[str, list[Country]] = {}
        self._dial_codes: dict[str, list[DialCode]] = {}
        self._currencies: dict[str, list[Currency]] = {}
        self._locales_flat: dict[str, list[LocaleFlat]] = {}
        self._locales_data: dict[str, list[LocaleInfo]] = {}
        self._locales_grouped: dict[str, dict[str, list[LocaleFlat]]] = {}

    # -- countries / dial codes / currencies ----------------------------------

    def countries(self, locale: str) -> list[Country]:
        loc = normalize_locale(locale)
        if loc not in self._countries:
            rows = self._list(f"countries/{encode_segment(loc)}")
            self._countries[loc] = [
                Country(code=str(r.get("code", "")), label=str(r.get("label", ""))) for r in rows
            ]
        return self._countries[loc]

    def dial_codes(self, locale: str) -> list[DialCode]:
        loc = normalize_locale(locale)
        if loc not in self._dial_codes:
            rows = self._list(f"countries/dial-codes/{encode_segment(loc)}")
            self._dial_codes[loc] = [
                DialCode(
                    country_code=str(r.get("country_code", "")),
                    dial_code=str(r.get("dial_code", "")),
                    name=str(r.get("name", "")),
                )
                for r in rows
            ]
        return self._dial_codes[loc]

    def currencies(self, locale: str) -> list[Currency]:
        loc = normalize_locale(locale)
        if loc not in self._currencies:
            rows = self._list(f"currencies/{encode_segment(loc)}")
            self._currencies[loc] = [
                Currency(
                    code=str(r.get("code", "")),
                    name=str(r.get("name", "")),
                    symbol=str(r.get("symbol", "")),
                    symbol_native=str(r.get("symbol_native", "")),
                    decimal_digits=int(r.get("decimal_digits", 2)),
                    rounding=float(r.get("rounding", 0)),
                )
                for r in rows
            ]
        return self._currencies[loc]

    def country_name(self, code: str, locale: str) -> str:
        if not code:
            return ""
        for country in self.countries(locale):
            if country.code.lower() == code.lower():
                return country.label
        return code

    def currency_name(self, code: str, locale: str) -> str:
        if not code:
            return ""
        for currency in self.currencies(locale):
            if currency.code.lower() == code.lower():
                return currency.name
        return code

    # -- locales --------------------------------------------------------------

    def locales_flat(self, locale: str) -> list[LocaleFlat]:
        loc = normalize_locale(locale)
        if loc not in self._locales_flat:
            data = self._locales_object("locales/flat", loc)
            self._locales_flat[loc] = [
                LocaleFlat(code=str(r.get("code", "")), name=str(r.get("name", ""))) for r in data
            ]
        return self._locales_flat[loc]

    def locales_data(self, locale: str) -> list[LocaleInfo]:
        loc = normalize_locale(locale)
        if loc not in self._locales_data:
            data = self._locales_object("locales/data", loc)
            self._locales_data[loc] = [
                LocaleInfo(
                    code=str(r.get("code", "")),
                    locale_name=str(r.get("locale_name", "")),
                    lang_name=str(r.get("lang_name", "")),
                )
                for r in data
            ]
        return self._locales_data[loc]

    def locales(self, locale: str) -> dict[str, list[LocaleFlat]]:
        """Locales grouped by language name (the ``/locales`` index format)."""
        loc = normalize_locale(locale)
        if loc not in self._locales_grouped:
            response = self._http.get("locales", params=self._locale_params(loc))
            group = self._pick_locale_bucket(response.get("data"), loc)
            grouped: dict[str, list[LocaleFlat]] = {}
            if isinstance(group, dict):
                for lang, rows in group.items():
                    grouped[lang] = [
                        LocaleFlat(code=str(r.get("code", "")), name=str(r.get("name", "")))
                        for r in (rows or [])
                    ]
            self._locales_grouped[loc] = grouped
        return self._locales_grouped[loc]

    def locale_name(self, for_locale: str, short: bool = False, locale: str = "en-US") -> str:
        if not for_locale:
            return ""
        target = for_locale.lower()
        for info in self.locales_data(locale):
            if info.code.lower() == target:
                return info.lang_name if short else info.locale_name
        return for_locale

    #: Sync alias — parity with the JS SDK's ``getLocaleNameWithLookup`` (here the sync
    #: helper fetches the dataset on demand, so the distinction is naming-only).
    locale_name_with_lookup = locale_name

    def clear(self) -> None:
        for store in (
            self._countries,
            self._dial_codes,
            self._currencies,
            self._locales_flat,
            self._locales_data,
            self._locales_grouped,
        ):
            store.clear()

    # -- internals ------------------------------------------------------------

    def _list(self, path: str) -> list[dict[str, Any]]:
        response = self._http.get(path)
        data = response.get("data")
        return data if isinstance(data, list) else []

    def _locale_params(self, loc: str) -> dict[str, Any]:
        # nova keys the response by each requested locale; ask for the one we want.
        return {"locales[]": loc, "project_id": self._project_id}

    def _locales_object(self, path: str, loc: str) -> list[dict[str, Any]]:
        response = self._http.get(path, params=self._locale_params(loc))
        bucket = self._pick_locale_bucket(response.get("data"), loc)
        return bucket if isinstance(bucket, list) else []

    @staticmethod
    def _pick_locale_bucket(data: object, loc: str) -> object:
        # ``data`` is an object keyed by the requested locale(s); pick ours
        # (case-insensitively), falling back to the first value.
        if not isinstance(data, dict):
            return data
        for key, value in data.items():
            if key.lower() == loc.lower():
                return value
        return next(iter(data.values()), None)
