"""WIRE-4 — the translation call must never throw, and must not write-storm.

Two obligations, and the second is the one that gets skipped:

1. A failure to reach the API is an expected condition. On a server SDK we sit in
   the request path, so an exception here converts a working page into a 500 for
   every visitor.
2. **A failed catalog fetch MUST NOT queue registrations.** Without a catalog you
   cannot tell a miss from a hit, so treating the failure as "everything is
   unknown" re-registers phrases that already exist — every outage becomes a write
   storm, on paths that were already failing.

Every "does not queue" assertion below is paired with a positive control proving
the same call *does* queue when the fetch succeeds. Without it the test passes
against an SDK that never queues at all.
"""

from __future__ import annotations

import re

import httpx
import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache

API = "https://api.test/api"
AUTH_URL = f"{API}/authorize-project/proj-1"
TRANS_URL = re.compile(r"https://api\.test/api/translations")

HTML = "<div><p>Hello there</p><p>Second line</p></div>"


def _without_stamp(markup: str) -> str:
    """Drop MARK-1's identity attribute.

    The stamp is added on every rendered block including a miss, so a test asserting
    "the content degraded to source" can no longer compare the whole string. Removing
    only this attribute keeps the comparison strict about everything else, which is
    what these tests are actually about.
    """
    return re.sub(r'\s+data-ls-contentblock="[^"]*"', "", markup)


def make(**kw):
    return LangsysClient(
        "k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
        debounce=0, auto_flush=False, **kw
    )


def authorize_payload(key_type="write", write_enabled=True):
    return {
        "status": True,
        "data": {
            "id": "proj-1",
            "title": "Test",
            "base_locale": "en-us",
            "target_locales": ["es-es"],
            "default_locales": {},
            "key_type": key_type,
            "write_enabled": write_enabled,
            "langsys_settings": {"translatable_items": {"batch_limit": 200}},
        },
    }


def catalog_payload(data, write_enabled=True):
    return {"status": True, "words": 1, "write_enabled": write_enabled, "data": data}


# -- positive controls: the queue does fill on a healthy fetch ----------------


def test_CONTROL_translate_queues_a_miss_when_the_catalog_fetch_succeeds(httpx_mock):
    httpx_mock.add_response(url=TRANS_URL, json=catalog_payload({}))
    client = make()
    client.translate("Brand new phrase", category="CAT", locale="es-es")
    assert client.pending_phrases, "control failed: a genuine miss must queue"


def test_CONTROL_content_block_queues_when_the_catalog_fetch_succeeds(httpx_mock):
    pytest.importorskip("lxml")
    httpx_mock.add_response(url=TRANS_URL, json=catalog_payload({}))
    client = make()
    client.translate_content_block(HTML, category="CAT")
    assert client.pending_content_blocks, "control failed: an unknown block must queue"


# -- transport failure --------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param("connect", id="connection-refused"),
        pytest.param("status", id="http-500"),
    ],
)
def test_WIRE4_translate_degrades_to_the_source_phrase(httpx_mock, failure):
    if failure == "connect":
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=TRANS_URL, is_reusable=True)
    else:
        httpx_mock.add_response(url=TRANS_URL, status_code=500, json={"error": "boom"})
    client = make()
    assert client.translate("Technical Support", category="CAT_3", locale="es-es") == (
        "Technical Support"
    )


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param("connect", id="connection-refused"),
        pytest.param("status", id="http-500"),
    ],
)
def test_WIRE4_a_failed_fetch_queues_nothing(httpx_mock, failure):
    """The write-storm guard. An empty catalog because the fetch failed is not the
    same as an empty catalog because the phrase is genuinely new."""
    if failure == "connect":
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=TRANS_URL, is_reusable=True)
    else:
        httpx_mock.add_response(url=TRANS_URL, status_code=500, json={"error": "boom"})
    client = make()
    client.translate("Technical Support", category="CAT_3", locale="es-es")
    assert client.pending_phrases == []
    assert client.has_pending is False


def test_WIRE4_translate_still_interpolates_while_degraded(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=TRANS_URL, is_reusable=True)
    client = make()
    out = client.translate("Hi {name}", params={"name": "Ada"}, locale="es-es")
    assert out == "Hi Ada"


def test_WIRE4_content_block_degrades_and_queues_nothing(httpx_mock):
    pytest.importorskip("lxml")
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=TRANS_URL, is_reusable=True)
    client = make()
    assert _without_stamp(client.translate_content_block(HTML, category="CAT")) == HTML
    assert client.pending_content_blocks == []


def test_WIRE4_translate_page_degrades_and_queues_nothing(httpx_mock):
    pytest.importorskip("lxml")
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=TRANS_URL, is_reusable=True)
    client = make()
    out = client.translate_page(f"<html><body>{HTML}</body></html>", category="CAT")
    assert "Hello there" in out
    assert client.has_pending is False


def test_WIRE4_an_authorize_failure_does_not_throw_from_translate(httpx_mock):
    """`translate()` resolves the locale through `authorize()` when none is set, so
    an authorize outage reaches the render path too."""
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=AUTH_URL, is_reusable=True)
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=TRANS_URL, is_reusable=True)
    client = LangsysClient(
        "k", "proj-1", api_url=API, cache=MemoryCache(), debounce=0, auto_flush=False
    )
    assert client.translate("Technical Support", category="CAT_3") == "Technical Support"


def test_WIRE4_a_401_degrades_rather_than_throwing(httpx_mock):
    httpx_mock.add_response(url=TRANS_URL, status_code=401, json={"error": "bad key"})
    client = make()
    assert client.translate("Technical Support", locale="es-es") == "Technical Support"
    assert client.pending_phrases == []


def test_WIRE4_logs_loudly_when_it_degrades(httpx_mock, caplog):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=TRANS_URL, is_reusable=True)
    client = make()
    with caplog.at_level("WARNING", logger="langsys"):
        client.translate("Technical Support", locale="es-es")
    assert any(r.levelname == "WARNING" for r in caplog.records), (
        "a silent degradation is undiscoverable"
    )


def test_WIRE4_get_translations_returns_empty_rather_than_throwing(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=TRANS_URL, is_reusable=True)
    client = make()
    assert client.get_translations("es-es") == {}


def test_WIRE4_a_failed_fetch_is_not_cached_as_an_empty_catalog(httpx_mock):
    """Caching the failure would serve source text for the whole TTL, and would make
    the outage outlive itself on every host sharing the store."""
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=TRANS_URL)
    httpx_mock.add_response(url=TRANS_URL, json=catalog_payload({"CAT": {"Hi": "Hola"}}))
    client = make()
    assert client.translate("Hi", category="CAT", locale="es-es") == "Hi"
    # Second call must re-fetch rather than read a cached empty catalog.
    assert client.translate("Hi", category="CAT", locale="es-es") == "Hola"
