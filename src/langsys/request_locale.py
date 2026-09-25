"""SRV-6 - which locale a server request is served in.

The first usable candidate wins, in this order:

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

from dataclasses import dataclass
from typing import Optional, Sequence

from .locale import canonicalize_locale, find_best_locale_match, parse_accept_language

__all__ = ["LocaleChoice", "resolve_request_locale"]


@dataclass(frozen=True)
class LocaleChoice:
    """The locale to serve, where it came from, and the `Vary` headers the response must carry."""

    locale: str
    source: str  # "url" | "cookie" | "accept-language" | "base"
    vary: tuple[str, ...]


def _supported(candidate: Optional[str], supported: Sequence[str]) -> Optional[str]:
    if not candidate or not str(candidate).strip():
        return None
    return find_best_locale_match([str(candidate).strip().replace("_", "-")], list(supported))


def resolve_request_locale(
    supported: Sequence[str],
    base: str,
    *,
    url: Optional[str] = None,
    cookie: Optional[str] = None,
    accept_language: Optional[str] = None,
    uses_cookie: bool = True,
) -> LocaleChoice:
    """Resolve one request's locale (SRV-6). `supported` is the project's base and target locales.

    Pass `url` and `cookie` as the raw values the app routes by and stores, or None when absent.
    `uses_cookie=False` says the app keeps no locale cookie or session at all, so no response
    depends on one.
    """
    locales = [loc for loc in supported if loc]
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
        return LocaleChoice(header_locale, "accept-language", tuple(consulted))

    return LocaleChoice(canonicalize_locale(base), "base", tuple(consulted))
