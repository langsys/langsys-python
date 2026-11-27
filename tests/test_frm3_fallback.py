"""FRM-3: the translate function resolves the catalog, then the framework's language files, then the
source, and never throws.

The language files are the binding's: the core asks them through the miss fallback, given the phrase,
the locale, the category and the argument as the call wrote it. Its answer is filled with the call's
params and is never registered or cached. `resolve()` says who wrote the text (FRM-8).
"""

from __future__ import annotations

import json

import pytest

import langsys.client as client_module
from langsys import LangsysClient, Miss, Resolved
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch

API = "https://api.test/api"


class Files:
    """The framework's language files for `es-cr`, recording every question it is asked."""

    def __init__(self, lines=None, raises=False):
        self.lines = lines if lines is not None else {}
        self.raises = raises
        self.asked: list[Miss] = []

    def __call__(self, miss: Miss):
        self.asked.append(miss)
        if self.raises:
            raise RuntimeError("the language files are broken")
        return self.lines.get(miss.argument)


def make(catalog=None, ok=True, files=None, **options):
    client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                           debounce=0, auto_flush=False, miss_fallback=files, **options)
    client.set_locale("es-cr")
    client._catalog.get = lambda locale, use_cache=True: CatalogFetch(  # type: ignore[method-assign]
        catalog if catalog is not None else {}, ok=ok)
    return client


@pytest.fixture(autouse=True)
def _fresh_notice(monkeypatch):
    monkeypatch.setattr(client_module, "_CATALOG_NOTICE", set())


def test_FRM3_catalog_then_language_files_then_source():
    files = Files({"Welcome back": "Bienvenido (archivo)", "Hello {name}": "Hola {name} (archivo)"})
    client = make({"__uncategorized__": {"Welcome back": "Bienvenido"}}, files=files)
    assert client.translate("Welcome back") == "Bienvenido"
    assert files.asked == [], "the catalog's translation was not the first answer"
    assert client.translate("Hello {name}", params={"name": "Ana"}) == "Hola Ana (archivo)"
    assert client.translate("Goodbye {name}", params={"name": "Ana"}) == "Goodbye Ana"


@pytest.mark.parametrize(("catalog", "ok"), [
    ({"__uncategorized__": {"Welcome back": None}}, True),
    ({"__uncategorized__": {"Welcome back": ""}}, True),
    ({}, False),
], ids=["untranslated", "empty", "unreadable"])
def test_FRM3_an_untranslated_or_unreadable_catalog_falls_to_the_files(catalog, ok):
    client = make(catalog, ok=ok, files=Files({"Welcome back": "Bienvenido (archivo)"}))
    assert client.translate("Welcome back") == "Bienvenido (archivo)"


def test_FRM3_the_fallback_sees_the_call_and_its_answer_is_never_registered():
    files = Files({"Welcome back": "Bienvenido (archivo)"})
    client = make({}, files=files)
    assert client.translate("Welcome back", category="Home") == "Bienvenido (archivo)"
    assert files.asked == [Miss("Welcome back", "es-CR", "Home", "Welcome back")]
    assert client.pending_phrases == [{"phrase": "Welcome back", "category": "Home"}], \
        "the miss registers the phrase, never the fallback's line"
    assert client.translate("Welcome back", category="Home") == "Bienvenido (archivo)"
    assert len(files.asked) == 2, "the fallback's answer was cached"


def test_FRM3_the_fallback_gets_the_key_of_a_key_style_call(tmp_path):
    source = tmp_path / "en.json"
    source.write_text(json.dumps({"checkout": {"submit": "Pay now"}}))
    files = Files({"checkout.submit": "Pagar ahora (archivo)"})
    client = make({}, files=files, legacy_files=[source])
    assert client.translate("checkout.submit") == "Pagar ahora (archivo)"
    assert files.asked == [Miss("Pay now", "es-CR", "checkout", "checkout.submit")]


def test_FRM3_a_framework_entry_point_asks_with_its_text_as_written():
    files = Files({"Hello %(name)s": "Hola %(name)s"})
    client = make({}, files=files)
    assert client.translate_legacy("Hello %(name)s") == "Hola %(name)s"
    assert files.asked[0].argument == "Hello %(name)s"


def test_FRM3_a_fallback_that_raises_renders_the_source():
    client = make({}, files=Files(raises=True))
    assert client.translate("Hello {name}", params={"name": "Ana"}) == "Hello Ana"


def test_FRM3_with_no_key_every_call_goes_down_the_chain_and_none_raises(monkeypatch):
    for name in ("LANGSYS_API_KEY", "LANGSYS_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    files = Files({"Welcome back": "Bienvenido (archivo)"})
    client = LangsysClient(api_url=API, cache=MemoryCache(), base_locale="en-us", debounce=0,
                           auto_flush=False, miss_fallback=files)
    assert client.translate("Welcome back") == "Bienvenido (archivo)"
    assert client.translate("Hello {name}", params={"name": "Ana"}) == "Hello Ana"
    assert client.translate_legacy("%(count)d file", entry_point="ngettext", plural="%(count)d files",
                                   params={"count": 2}) == "2 files"


def test_FRM3_an_unavailable_catalog_is_reported_once_at_debug(caplog):
    client = make({}, ok=False, files=Files())
    with caplog.at_level("DEBUG", logger="langsys"):
        for phrase in ("One", "Two", "Three"):
            client.translate(phrase)
    notices = [r for r in caplog.records if "reported once" in r.getMessage()]
    assert len(notices) == 1 and notices[0].levelname == "DEBUG"
    assert [r for r in caplog.records if r.levelno >= 30] == [], "the translate path was not quiet"


def test_FRM3_control_a_readable_catalog_reports_nothing(caplog):
    client = make({}, ok=True, files=Files())
    with caplog.at_level("DEBUG", logger="langsys"):
        client.translate("One")
    assert not [r for r in caplog.records if "reported once" in r.getMessage()]


def test_FRM3_control_a_failed_catalog_read_still_warns_on_the_transport(httpx_mock, caplog):
    httpx_mock.add_response(status_code=500, json={"status": False}, is_reusable=True)
    client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                           debounce=0, auto_flush=False)
    client.set_locale("es-cr")
    with caplog.at_level("DEBUG", logger="langsys"):
        assert client.translate_content_block("<div><p>One</p><p>Two</p></div>").startswith("<div")
    assert [r for r in caplog.records if r.levelno >= 30], "a failed read was not reported"


def test_FRM3_the_page_path_does_not_ask_the_translate_functions_fallback():
    files = Files({"Welcome back": "Bienvenido (archivo)"})
    page = make({}, files=files).translate_page("<html><body><p>Welcome back</p></body></html>")
    assert "Welcome back" in page and files.asked == []


@pytest.mark.parametrize(("catalog", "lines", "expected"), [
    ({"__uncategorized__": {"Welcome back": "Bienvenido"}}, {}, Resolved("Bienvenido", "catalog")),
    ({}, {"Welcome back": "Bienvenido (archivo)"}, Resolved("Bienvenido (archivo)", "fallback")),
    ({}, {}, Resolved("Welcome back", "source")),
    ({"Header": {"Welcome back": "Bienvenido"}}, {}, Resolved("Bienvenido", "catalog")),
], ids=["catalog", "fallback", "source", "previous-category"])
def test_FRM3_resolve_says_who_wrote_the_text(catalog, lines, expected):
    client = make(catalog, files=Files(lines))
    assert client.resolve("Welcome back", previous_category="Header") == expected
