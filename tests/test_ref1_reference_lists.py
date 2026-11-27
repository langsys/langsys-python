"""REF-1: the core serves the API's reference lists, each named in a display locale.

`api` below plays the spec's fixture: it answers each display locale with a list of its own, and a
locale list carries a second entry ahead of the display locale's unless `append_target_locales` is
off, so a client that lets the API append target locales, or picks the first entry, reads the
wrong list. Every "sends nothing" is read from the transport, beside a call that does send.
"""

from __future__ import annotations

import re
import threading
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import langsys.catalog as catalog_module
import langsys.client as client_module
from langsys import LangsysClient, NotConfiguredError
from langsys.cache import MemoryCache
from langsys.types import LocaleFlat

API = "https://api.test/api"
ANY = re.compile(r"https://api\.test/api/(countries|currencies|locales).*")


def make(key="k", cache=None, **kw):
    return LangsysClient(key, "proj-1", api_url=API, cache=cache if cache is not None else MemoryCache(),
                         base_locale="en-us", debounce=0, auto_flush=False, **kw)


def _country(loc):
    return [{"code": "DE", "label": f"Germany ({loc})"}]


def _flat(loc):
    return [{"code": "es_CR" if loc == "es_ES" else "af-za", "name": f"Name ({loc})"}]


class Api:
    """The fixture: a distinct list per display locale; `fail` answers 500 to everything."""

    def __init__(self, httpx_mock):
        self.fail = False
        self.lists = {"en-us", "it-it", "es_ES", "he-il"}
        httpx_mock.add_callback(self, url=ANY, is_reusable=True, is_optional=True)
        self._mock = httpx_mock

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(500, json={"status": False, "error": {"message": "boom"}})
        parts = urlsplit(str(request.url))
        path = parts.path.split("/api/", 1)[1]
        query = parse_qs(parts.query)
        if path.startswith("locales"):
            (display,) = query["locales[]"]
            data = {}
            if query.get("append_target_locales") != ["0"]:
                data["xx-xx"] = [{"code": "xx-xx", "name": "appended"}]
            for held in sorted(self.lists):
                if held.replace("_", "-").lower() == display:
                    if path == "locales":
                        data[held] = {"Lang": _flat(held)}
                    elif path == "locales/data":
                        data[held] = [{"code": "es-es", "locale_name": f"Spanish ({held})",
                                       "lang_name": "Spanish"}]
                    else:
                        data[held] = _flat(held)
            return httpx.Response(200, json={"status": True, "data": data})
        display = path.rsplit("/", 1)[1]
        if path.startswith("countries/dial-codes"):
            rows = [{"country_code": "DE", "dial_code": "49", "name": f"Germany ({display})"}]
        elif path.startswith("countries"):
            rows = _country(display)
        else:
            rows = [{"code": "EUR", "name": f"Euro ({display})", "symbol": "€"}]
        return httpx.Response(200, json={"status": True, "data": rows})

    def requests(self):
        return [r for r in self._mock.get_requests() if ANY.match(str(r.url))]


@pytest.fixture
def api(httpx_mock):
    return Api(httpx_mock)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(client_module, "_UNCONFIGURED_NOTICE", set())
    for name in ("LANGSYS_API_KEY", "LANGSYS_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)


# -- the display locale -------------------------------------------------------------------------


def test_REF1_each_call_answers_the_current_locale_list_or_the_one_given(api):
    client = make()
    client.set_locale("it-IT")
    assert client.countries()[0].label == "Germany (it-it)"
    assert client.countries("en-US")[0].label == "Germany (en-us)"
    assert client.currencies()[0].name == "Euro (it-it)"
    assert client.dial_codes("en-us")[0].name == "Germany (en-us)"
    assert client.locales_flat()[0].name == "Name (it-it)"
    assert client.locales_data("en-us")[0].locale_name == "Spanish (en-us)"
    assert list(client.locales()) == ["Lang"]


def test_REF1_a_locale_list_asks_for_one_locale_with_no_targets_appended_and_no_project(api):
    client = make()
    client.locales_flat("it-it")
    (request,) = api.requests()
    query = parse_qs(urlsplit(str(request.url)).query)
    assert query == {"locales[]": ["it-it"], "append_target_locales": ["0"]}, query
    assert client.locales_flat("it-it")[0].code == "af-za", "the appended entry was answered"


def test_REF1_the_display_locales_entry_is_answered_whatever_else_the_answer_holds(httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*/locales/flat.*"), json={"status": True, "data": {
        "xx-xx": [{"code": "xx-xx", "name": "another locale's"}],
        "it_IT": [{"code": "af_ZA", "name": "Afrikaans"}],
    }})
    assert make().locales_flat("it-it") == [LocaleFlat(code="af-za", name="Afrikaans")]


def test_REF1_a_list_is_held_per_display_locale(api):
    client = make()
    client.countries("it-it")
    client.countries("it-it")
    assert len(api.requests()) == 1, "a held list was read again"
    client.countries("en-us")
    assert len(api.requests()) == 2, "control: another display locale was not read"
    client.countries("it-it")
    assert len(api.requests()) == 2


def test_REF1_an_entry_keyed_in_the_organisations_format_is_answered_in_wire_form(api):
    """The fixture keys the list `es_ES` and writes its codes `es_CR`."""
    (entry,) = make().locales_flat("es-es")
    assert (entry.code, entry.name) == ("es-cr", "Name (es_ES)")


def test_REF1_a_given_locale_is_normalised_as_the_current_one_is(api):
    client = make()
    client.set_locale("iw-IL")
    stored = client.countries()
    given = make().countries("iw-IL")
    assert stored == given and stored[0].label == "Germany (he-il)"


@pytest.mark.parametrize("display", ["../projects", "es/es", "es?x=1"])
def test_REF1_a_display_locale_not_shaped_as_a_locale_sends_nothing(api, caplog, display):
    client = make()
    with caplog.at_level("WARNING", logger="langsys"):
        assert client.countries(display) == []
        assert client.locales(display) == {}
        assert client.locales_flat(display) == []
    assert api.requests() == []
    assert any("display locale" in r.getMessage() for r in caplog.records), "refused silently"
    assert client.countries("it-it"), "control: a locale-shaped display locale is read"


def test_REF1_a_locale_the_api_does_not_hold_answers_an_empty_list_held_like_any_answer(api):
    client = make()
    assert client.locales_flat("fr-fr") == []
    assert client.locales_flat("fr-fr") == []
    assert len(api.requests()) == 1


# -- failures -----------------------------------------------------------------------------------


def test_REF1_a_failed_read_answers_empty_logs_and_holds_off_inside_the_window(api, caplog, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(catalog_module, "_clock", lambda: now[0])
    api.fail = True
    client = make()
    with caplog.at_level("WARNING", logger="langsys"):
        assert client.countries("it-it") == []
        assert client.locales("it-it") == {}
    assert any("countries" in r.getMessage() and "500" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
    assert len(api.requests()) == 2
    assert client.countries("it-it") == []
    assert len(api.requests()) == 2, "a read inside the window was sent"

    api.fail = False
    other = make(key="other")
    assert other.countries("it-it")[0].label == "Germany (it-it)", "other credentials were held back"
    assert len(api.requests()) == 3
    assert client.countries("it-it") == [], "the window ended early"

    now[0] += 3.1
    assert client.countries("it-it")[0].label == "Germany (it-it)", "the window never ended"


def test_REF1_a_failure_is_never_held_as_a_list(api):
    cache = MemoryCache()
    api.fail = True
    make(cache=cache).countries("it-it")
    api.fail = False
    assert make(cache=cache).countries("it-it")[0].label == "Germany (it-it)"


@pytest.mark.parametrize("body", [
    {"status": True, "data": {"it-it": []}},
    {"status": True, "data": "nope"},
    {"status": True},
], ids=["map-for-a-list", "string", "no-data"])
def test_REF1_an_answer_not_of_the_lists_shape_answers_empty_and_logs(httpx_mock, caplog, body):
    httpx_mock.add_response(url=re.compile(r".*/countries/it-it$"), json=body)
    with caplog.at_level("WARNING", logger="langsys"):
        assert make().countries("it-it") == []
    assert any("shape" in r.getMessage() for r in caplog.records)


def test_REF1_a_locale_list_answered_as_a_list_is_not_of_its_shape(httpx_mock, caplog):
    httpx_mock.add_response(url=re.compile(r".*/locales/flat.*"),
                            json={"status": True, "data": [{"code": "af", "name": "Afrikaans"}]})
    with caplog.at_level("WARNING", logger="langsys"):
        assert make().locales_flat("it-it") == []
    assert any("shape" in r.getMessage() for r in caplog.records)


# -- unconfigured ---------------------------------------------------------------------------------


@pytest.mark.parametrize("call", ["countries", "dial_codes", "currencies", "locales", "locales_flat",
                                  "locales_data"])
def test_REF1_an_unconfigured_core_answers_the_empty_value_and_sends_nothing(api, caplog, call):
    client = LangsysClient(api_url=API, cache=MemoryCache(), base_locale="en-us", auto_flush=False)
    with caplog.at_level("WARNING", logger="langsys"):
        answer = getattr(client, call)("it-it")
    assert answer == ({} if call == "locales" else [])
    assert api.requests() == []
    assert [r for r in caplog.records if "until the client is configured" not in r.getMessage()] == []


def test_REF1_an_unconfigured_core_under_require_credentials_throws(api):
    client = LangsysClient(api_url=API, cache=MemoryCache(), base_locale="en-us", auto_flush=False,
                           require_credentials=True)
    with pytest.raises(NotConfiguredError):
        client.countries("it-it")
    assert api.requests() == []


# -- the hold outlives the request ---------------------------------------------------------------


def test_REF1_a_second_core_in_a_new_request_answers_the_held_list_without_a_request(api):
    cache = MemoryCache()
    make(cache=cache).locales_data("it-it")
    assert len(api.requests()) == 1
    assert make(cache=cache).locales_data("it-it")[0].locale_name == "Spanish (it-it)"
    assert len(api.requests()) == 1, "the held list was read again"


def test_REF1_concurrent_reads_of_one_list_share_one_request(httpx_mock):
    gate = threading.Event()

    def slow(request):
        gate.wait(2)
        return httpx.Response(200, json={"status": True, "data": _country("it-it")})

    httpx_mock.add_callback(slow, url=re.compile(r".*/countries/it-it$"), is_reusable=True)
    client = make()
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(client.countries("it-it")))
               for _ in range(4)]
    for thread in threads:
        thread.start()
    gate.set()
    for thread in threads:
        thread.join(3)
    assert len(answers) == 4 and all(a and a[0].code == "DE" for a in answers)
    assert len(httpx_mock.get_requests()) == 1


def test_REF1_refresh_reads_the_list_again(api):
    client = make()
    client.countries("it-it")
    client.refresh()
    client.countries("it-it")
    assert len(api.requests()) == 2


# -- the helpers built on the lists ---------------------------------------------------------------


def test_REF1_names_come_from_the_held_lists(api):
    client = make()
    assert client.country_name("de", "it-it") == "Germany (it-it)"
    assert client.country_name("ZZ", "it-it") == "ZZ"
    assert client.currency_name("eur", "it-it") == "Euro (it-it)"
    assert client.locale_name("es-ES", in_locale="en-us") == "Spanish (en-us)"
    assert client.locale_name("es-ES", short=True, in_locale="en-us") == "Spanish"
    assert len(api.requests()) == 3
