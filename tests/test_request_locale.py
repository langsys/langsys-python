"""SRV-6 - the request's locale, and GATE-10's producing half - the page it was rendered into.

SRV-6 is decided in-process from what the request carries and the project's locales, so it is
graded `n/a (pure)`; the client-level test reads those locales from the contract double.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from contract import PROJECT, WRITE_KEY, world

from langsys import LangsysClient, resolve_request_locale
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch

SUPPORTED = ["en-us", "it-it", "es-es"]


def resolve(**kw):
    return resolve_request_locale(SUPPORTED, "en-us", **kw)


# -- the spec's four requests ---------------------------------------------------------------------


def test_SRV6_the_url_wins_over_a_conflicting_cookie_and_header_and_adds_no_vary():
    choice = resolve(url="it-it", cookie="es-es", accept_language="es")
    assert (choice.locale.lower(), choice.source, choice.vary) == ("it-it", "url", ())


def test_SRV6_a_cookie_wins_over_the_header_and_varies_on_cookie():
    choice = resolve(cookie="es-es", accept_language="it-IT")
    assert (choice.locale.lower(), choice.source) == ("es-es", "cookie")
    assert choice.vary == ("Cookie",)


def test_SRV6_with_only_a_header_the_negotiated_locale_varies_on_accept_language():
    choice = resolve(accept_language="it-IT,it;q=0.9,en;q=0.5")
    assert (choice.locale.lower(), choice.source) == ("it-it", "accept-language")
    assert "Accept-Language" in choice.vary


def test_SRV6_an_unsupported_cookie_falls_through_to_the_header():
    choice = resolve(cookie="zz-zz", accept_language="es")
    assert (choice.locale.lower(), choice.source) == ("es-es", "accept-language")


# -- validation, fall-through and Vary ------------------------------------------------------------


@pytest.mark.parametrize("url", ["zz-zz", "fr", "", "  ", "../etc"])
def test_SRV6_an_unsupported_url_locale_is_never_served(url):
    choice = resolve(url=url, accept_language="it")
    assert choice.locale.lower() == "it-it" and choice.source == "accept-language"


def test_SRV6_nothing_usable_serves_the_base_and_varies_on_everything_consulted():
    choice = resolve(cookie="zz-zz", accept_language="fr-FR")
    assert (choice.locale.lower(), choice.source) == ("en-us", "base")
    assert set(choice.vary) == {"Cookie", "Accept-Language"}


def test_SRV6_a_header_choice_varies_on_cookie_only_when_the_app_keeps_a_locale_cookie():
    """A visitor holding a locale cookie would have been served differently, so a cache must key on
    it - unless the app has no such cookie at all."""
    assert set(resolve(accept_language="it").vary) == {"Cookie", "Accept-Language"}
    assert resolve(accept_language="it", uses_cookie=False).vary == ("Accept-Language",)


def test_SRV6_a_cookie_the_app_does_not_keep_is_ignored():
    choice = resolve(cookie="es-es", accept_language="it", uses_cookie=False)
    assert choice.locale.lower() == "it-it"


def test_SRV6_a_language_only_candidate_matches_its_supported_locale():
    assert resolve(url="es").locale.lower() == "es-es"


# -- the framework already resolved it ------------------------------------------------------------


@pytest.mark.parametrize("framework", ["es-ES", "es_ES", "es-es"], ids=["bcp47", "posix", "project"])
def test_SRV6_the_frameworks_locale_is_served_whatever_the_request_says_and_adds_no_vary(framework):
    choice = resolve(framework=framework, url="it-it", cookie="it-it", accept_language="it")
    assert (choice.locale, choice.source, choice.vary) == ("es-es", "framework", ())


def test_SRV6_a_bare_framework_language_maps_through_the_projects_default_locales():
    choice = resolve_request_locale(["en-us", "pt-br", "pt-pt"], "en-us", framework="pt",
                                    accept_language="pt-PT", default_locales={"pt": "pt-br"})
    assert (choice.locale, choice.source) == ("pt-br", "framework")


def test_SRV6_a_framework_locale_the_project_does_not_serve_serves_the_base_not_the_request():
    """The framework decided; the SDK validates it and never overrides it with its own negotiation."""
    choice = resolve(framework="fr-FR", url="it-it", accept_language="it")
    assert (choice.locale, choice.source, choice.vary) == ("en-us", "framework", ())


def test_SRV6_control_with_nothing_from_the_framework_the_sdk_negotiates_and_varies():
    for framework in (None, ""):
        choice = resolve(framework=framework, accept_language="it")
        assert (choice.locale, choice.source) == ("it-it", "accept-language")
        assert "Accept-Language" in choice.vary


def test_SRV6_the_client_maps_a_bare_framework_language_through_the_project(double):
    double.seed(world(target_locales=["it-it", "es-es"]))
    c = LangsysClient(WRITE_KEY, PROJECT, api_url=double.base_url, cache=MemoryCache(),
                      debounce=0, auto_flush=False)
    choice = c.resolve_request_locale(framework="es", accept_language="it")
    assert (choice.locale, choice.source, choice.vary) == ("es-es", "framework", ())


# -- the client reads the project's locales -------------------------------------------------------


def test_SRV6_the_client_validates_against_the_projects_own_locales(double):
    double.seed(world(target_locales=["it-it"]))
    c = LangsysClient(WRITE_KEY, PROJECT, api_url=double.base_url, cache=MemoryCache(),
                      debounce=0, auto_flush=False)
    assert c.resolve_request_locale(url="it-it").locale.lower() == "it-it"
    served = c.resolve_request_locale(url="es-es", accept_language="es")
    assert (served.locale.lower(), served.source) == ("en-us", "base"), "es-es is not a project locale"


def test_SRV6_when_the_project_cannot_be_read_only_the_configured_base_is_served():
    c = LangsysClient("k", "p", api_url="http://127.0.0.1:9/api", cache=MemoryCache(),
                      base_locale="en-us", debounce=0, auto_flush=False)
    choice = c.resolve_request_locale(url="it-it", accept_language="it")
    assert (choice.locale.lower(), choice.source) == ("en-us", "base")


# -- GATE-10, producing ---------------------------------------------------------------------------

PAGE = "<html><head><title>Hi</title></head><body><p>Hello</p></body></html>"


def render(locale: str, base_locale="en-us") -> str:
    pytest.importorskip("lxml")
    c = LangsysClient("k", "p", api_url="https://api.test/api", cache=MemoryCache(),
                      base_locale=base_locale, debounce=0, auto_flush=False)
    c.set_locale(locale)
    with patch.object(c._catalog, "get", return_value=CatalogFetch({}, ok=True)), \
            patch.object(c, "authorize", side_effect=AssertionError("no round-trip needed")):
        return c.translate_page(PAGE, category="UI")


def test_GATE10_a_render_off_the_base_locale_marks_its_root_resolved():
    root = re.search(r"<html[^>]*>", render("it-IT"))
    assert root and 'data-ls-resolved="it-it"' in root.group(0), root


def test_GATE10_the_same_render_in_the_base_locale_is_not_marked():
    """A base-locale render is source; marking it hides the text discovery exists to find."""
    assert "data-ls-resolved" not in render("en-US")


def test_GATE10_an_unknowable_base_locale_leaves_the_page_unmarked():
    pytest.importorskip("lxml")
    c = LangsysClient("k", "p", api_url="http://127.0.0.1:9/api", cache=MemoryCache(),
                      debounce=0, auto_flush=False)
    c.set_locale("it-it")
    assert "data-ls-resolved" not in c.translate_page(PAGE, category="UI")
