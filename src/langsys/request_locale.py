"""SRV-6 - which locale a server request is served in.

Where the framework or the app has already resolved the request's locale - Django's
`LocaleMiddleware`, an app's own middleware - that locale is served, mapped to the project's form
and validated, and the SDK adds no `Vary`. Only where nothing has resolved it does the SDK resolve
it itself, taking the first usable candidate, in this order:

1. the URL - a path segment, subdomain or query parameter the app routes by;
2. a cookie or session value the app set;
3. `Accept-Language`, negotiated against the project's locales;

and otherwise the project's base locale. Every candidate is validated against the locales the
project serves (its base and target locales): an unsupported one is skipped and resolution falls
through to the next source. It is never served, and this module never writes a cookie.

`Vary` names every request header the choice depended on, so a cache in front of the site keys on
them. A locale taken from the URL needs none - the URL is already the cache key. Otherwise the
choice depended on each source consulted to reach it: a cookie winner on `Cookie`; a header winner
on `Accept-Language`, and on `Cookie` too when the app keeps a locale cookie, because a visitor
holding one would have been served differently; the base locale on both.

Where the framework keeps these values - the query parameter's name, the cookie's name, an
app's own `locale(request)` resolver standing in for steps 1 and 2 - is the binding's wiring.
The order and the validation are not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional, Sequence

from .locale import (
    canonicalize_locale,
    find_best_locale_match,
    normalize_locale,
    parse_accept_language,
)

__all__ = ["LocaleChoice", "resolve_request_locale"]


@dataclass(frozen=True)
class LocaleChoice:
    """The locale to serve, in the project's lowercase form, where it came from, and the `Vary`
    headers the SDK's own choice obliges the response to carry."""

    locale: str
    source: str  # "framework" | "url" | "cookie" | "accept-language" | "base"
    vary: tuple[str, ...]


def _supported(candidate: Optional[str], supported: Sequence[str]) -> Optional[str]:
    if not candidate or not str(candidate).strip():
        return None
    found = find_best_locale_match([str(candidate).strip().replace("_", "-")], list(supported))
    return normalize_locale(found) if found else None


def _framework_locale(
    candidate: str, supported: Sequence[str], default_locales: Mapping[str, str]
) -> Optional[str]:
    """The framework's locale in the project's form: `es-ES`/`es_ES` -> `es-es`; a bare language
    -> the project's default locale for it (authorization's `default_locales`); None when the
    project does not serve it."""
    wanted = normalize_locale(str(candidate))
    served = {normalize_locale(loc) for loc in supported}
    if wanted in served:
        return wanted
    if "-" not in wanted:
        default = default_locales.get(wanted)
        if default and normalize_locale(default) in served:
            return normalize_locale(default)
    return None


def resolve_request_locale(
    supported: Sequence[str],
    base: str,
    *,
    framework: Optional[str] = None,
    url: Optional[str] = None,
    cookie: Optional[str] = None,
    accept_language: Optional[str] = None,
    uses_cookie: bool = True,
    default_locales: Optional[Mapping[str, str]] = None,
) -> LocaleChoice:
    """Resolve one request's locale (SRV-6). `supported` is the project's base and target locales.

    `framework` is the locale the framework or the app already resolved (Django's
    `request.LANGUAGE_CODE`, an app middleware's value): when given it decides, mapped to the
    project's form and validated - an unsupported one is served as the base - and the SDK adds no
    `Vary`, because that choice is the framework's to vary on. Only when it is None does the SDK
    resolve the locale itself, from `url` then `cookie` then `accept_language`.
    `uses_cookie=False` says the app keeps no locale cookie or session at all.
    """
    locales = [loc for loc in supported if loc]
    project_base = normalize_locale(canonicalize_locale(base))
    if framework is not None and str(framework).strip():
        chosen = _framework_locale(framework, locales, default_locales or {})
        return LocaleChoice(chosen or project_base, "framework", ())

    url_locale = _supported(url, locales)
    if url_locale:
        return LocaleChoice(url_locale, "url", ())

    consulted: list[str] = ["Cookie"] if uses_cookie else []
    cookie_locale = _supported(cookie, locales) if uses_cookie else None
    if cookie_locale:
        return LocaleChoice(cookie_locale, "cookie", ("Cookie",))

    consulted.append("Accept-Language")
    preferences = parse_accept_language(accept_language)
    header_locale = (
        find_best_locale_match(preferences, locales) if preferences and locales else None
    )
    if header_locale:
        return LocaleChoice(normalize_locale(header_locale), "accept-language", tuple(consulted))

    return LocaleChoice(project_base, "base", tuple(consulted))
