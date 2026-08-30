import re

import pytest

from langsys import (
    AuthenticationError,
    LangsysClient,
    PaymentRequiredError,
    RateLimitError,
    ValidationError,
)
from langsys.cache import MemoryCache

API = "https://api.test/api"
AUTH_URL = f"{API}/authorize-project/proj-1"
TRANS_URL = re.compile(r"https://api\.test/api/translations")


def make(**kw):
    return LangsysClient(
        "k", "proj-1", api_url=API, cache=MemoryCache(), debounce=0, auto_flush=False, **kw
    )


def authorize_payload(key_type="read"):
    return {
        "status": True,
        "data": {
            "id": "proj-1",
            "title": "Test",
            "base_locale": "en-us",
            "target_locales": ["es-es"],
            "default_locales": {},
            "key_type": key_type,
            "write_enabled": key_type == "write",
            "langsys_settings": {"translatable_items": {"batch_limit": 200}},
        },
    }


def catalog_payload(data):
    return {"status": True, "words": 1, "untranslatedWords": 0, "data": data}


def test_authorize_parses_project(httpx_mock):
    httpx_mock.add_response(url=AUTH_URL, json=authorize_payload("write"), is_reusable=True)
    client = make()
    project = client.authorize()
    assert project.base_locale == "en-us"
    assert project.batch_limit == 200
    assert client.key_type == "write"
    assert client.can_write is True


def test_translate_hit_and_fallbacks(httpx_mock):
    httpx_mock.add_response(
        url=TRANS_URL,
        json=catalog_payload(
            {"CAT_3": {"Technical Support": "Soporte Técnico", "Null": None}}
        ),
    )
    client = make()
    assert client.translate("Technical Support", category="CAT_3", locale="es-ES") == "Soporte Técnico"
    # null -> base phrase, already registered (not queued)
    assert client.translate("Null", category="CAT_3", locale="es-ES") == "Null"
    assert client.has_pending is False
    # absent -> base phrase + queued for discovery
    assert client.translate("Brand new", category="CAT_3", locale="es-ES") == "Brand new"
    assert client.pending_phrases == [{"phrase": "Brand new", "category": "CAT_3"}]


def test_translate_interpolates_in_loaded_locale(httpx_mock):
    httpx_mock.add_response(url=TRANS_URL, json=catalog_payload({"Greetings": {}}))
    client = make()
    out = client.translate(
        "Hello, {name}! You have {n, plural, one {# msg} other {# msgs}}.",
        category="Greetings",
        params={"name": "Sarah", "n": 1},
        locale="en-US",
    )
    assert out == "Hello, Sarah! You have 1 msg."


def test_set_locale_drives_translation(httpx_mock):
    httpx_mock.add_response(url=TRANS_URL, json=catalog_payload({"UI": {"Save": "Guardar"}}))
    client = make()
    client.set_locale("es-ES")
    assert client.locale == "es-ES"
    assert client.translate("Save", category="UI") == "Guardar"


@pytest.mark.parametrize(
    "status,exc",
    [
        (401, AuthenticationError),
        (402, PaymentRequiredError),
        (422, ValidationError),
        (429, RateLimitError),
    ],
)
def test_error_status_maps_to_exception(httpx_mock, status, exc):
    httpx_mock.add_response(url=AUTH_URL, status_code=status, json={"error": "nope"})
    client = make()
    with pytest.raises(exc):
        client.authorize()
