import re

from langsys.http import HttpClient
from langsys.utilities import Utilities

API = "https://api.test/api"


def util():
    return Utilities(HttpClient(API, "k"), "proj-1")


def test_countries_parsed_and_cached(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/countries/en-us$"),
        json={"status": True, "data": [{"label": "Germany", "code": "DE"}]},
    )
    u = util()
    countries = u.countries("en-US")
    assert countries[0].code == "DE" and countries[0].label == "Germany"
    # second call is served from cache (no extra request registered)
    assert u.country_name("de", "en-US") == "Germany"
    assert u.country_name("ZZ", "en-US") == "ZZ"  # unknown -> the code
    assert len(httpx_mock.get_requests()) == 1


def test_currencies_full_shape(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/currencies/en-us$"),
        json={
            "status": True,
            "data": [
                {
                    "code": "USD",
                    "name": "US Dollar",
                    "symbol": "$",
                    "symbol_native": "$",
                    "decimal_digits": 2,
                    "rounding": 0,
                }
            ],
        },
    )
    cur = util().currencies("en-US")[0]
    assert (cur.code, cur.name, cur.symbol, cur.decimal_digits) == ("USD", "US Dollar", "$", 2)


def test_dial_codes(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/countries/dial-codes/en-us$"),
        json={"status": True, "data": [{"country_code": "DE", "dial_code": "49", "name": "Germany (+49)"}]},
    )
    d = util().dial_codes("en-US")[0]
    assert (d.country_code, d.dial_code, d.name) == ("DE", "49", "Germany (+49)")


def test_locales_object_is_keyed_by_locale(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/locales/data.*"),
        json={
            "status": True,
            "data": {
                "en-us": [{"code": "es-es", "locale_name": "Spanish (Spain)", "lang_name": "Spanish"}]
            },
        },
    )
    u = util()
    assert u.locale_name("es-ES", locale="en-US") == "Spanish (Spain)"
    assert u.locale_name("es-ES", short=True, locale="en-US") == "Spanish"
    assert u.locale_name("de-DE", locale="en-US") == "de-DE"  # unknown -> input


def test_locales_flat(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/locales/flat.*"),
        json={"status": True, "data": {"en-us": [{"code": "af", "name": "Afrikaans"}]}},
    )
    flat = util().locales_flat("en-US")
    assert flat[0].code == "af" and flat[0].name == "Afrikaans"
