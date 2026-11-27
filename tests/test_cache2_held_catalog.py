"""CACHE-2: a catalog already held keeps serving. The failure window governs fetching, not what the
SDK holds: a catalog that arrived keeps rendering when a later fetch is refused, and only the next
catalog that arrives replaces it, never the failure.

A held catalog is dropped for a refetch after a successful registration flush, `refresh()` and
`clear_cache()`. Each test then refuses the refetch and checks the held translation still renders,
that nothing is queued off the catalog that could not be read, and, after the window, that the next
catalog to arrive is the one served.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache

API = "https://api.test/api"
AUTH = f"{API}/authorize-project/proj-1"
ITEMS = f"{API}/translatable-items"
TRANS = re.compile(r"https://api\.test/api/translations.*")
AUTHORIZED = {"status": True, "data": {"id": "proj-1", "title": "T", "base_locale": "en-us",
                                       "target_locales": ["it-it"], "default_locales": {},
                                       "key_type": "write", "write_enabled": True}}
BLOCK = "<div><p>Block one</p><p>Block two</p></div>"


def catalog(save="Salva", block=None):
    data = {"UI": {"Save": save}}
    if block:
        data["UI"].update(block)
    return {"status": True, "write_enabled": True, "data": data}


@pytest.fixture
def now():
    clock = [1000.0]
    with patch("langsys.catalog._clock", lambda: clock[0]):
        yield clock


def make():
    return LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                         debounce=0, auto_flush=False)


def _refused(httpx_mock, status=500):
    httpx_mock.add_response(url=TRANS, status_code=status, json={"status": False, "error": {"message": "no"}})


def test_CACHE2_a_held_catalog_keeps_serving_when_the_refetch_after_a_flush_is_refused(httpx_mock, now):
    httpx_mock.add_response(url=AUTH, json=AUTHORIZED, is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog())
    httpx_mock.add_response(url=ITEMS, json={"status": True})
    client = make()
    assert client.translate("Save", category="UI", locale="it-it") == "Salva"
    client.translate("New phrase", category="UI", locale="it-it")
    assert client.flush_pending()["success"] is True  # drops the catalog for a refetch

    _refused(httpx_mock)
    assert client.translate("Save", category="UI", locale="it-it") == "Salva", "the failure replaced it"
    client.translate("Another new one", category="UI", locale="it-it")
    assert client.has_pending is False, "a miss was queued off a catalog that could not be read"
    assert client.translate("Save", category="UI", locale="it-it") == "Salva"  # inside the window

    now[0] += 3.1
    httpx_mock.add_response(url=TRANS, json=catalog("Salva ora"))
    assert client.translate("Save", category="UI", locale="it-it") == "Salva ora", \
        "the next catalog to arrive did not replace the held one"

    client.refresh()
    _refused(httpx_mock)
    now[0] += 600
    assert client.translate("Save", category="UI", locale="it-it") == "Salva ora", \
        "a later refusal fell back to an older catalog than the last one that arrived"


@pytest.mark.parametrize("status", [500, 403], ids=["outage", "key-refused"])
def test_CACHE2_a_held_catalog_keeps_serving_through_a_refused_refresh(httpx_mock, now, status):
    httpx_mock.add_response(url=TRANS, json=catalog())
    client = make()
    assert client.translate("Save", category="UI", locale="it-it") == "Salva"
    client.refresh()
    _refused(httpx_mock, status)
    assert client.translate("Save", category="UI", locale="it-it") == "Salva"
    assert client.get_translations("it-it") == {"UI": {"Save": "Salva"}}


def test_CACHE2_a_held_block_keeps_serving_through_a_refused_clear_cache(httpx_mock, now):
    from langsys.registration import generate_custom_id

    block_id = generate_custom_id("UI", ["Block one", "Block two"])
    httpx_mock.add_response(url=TRANS, json=catalog(block={
        block_id: {"Block one": "Blocco uno", "Block two": "Blocco due"}}))
    client = make()
    client.set_locale("it-it")
    assert "Blocco uno" in client.translate_content_block(BLOCK, category="UI")
    client.clear_cache("it-it")
    _refused(httpx_mock)
    assert "Blocco uno" in client.translate_content_block(BLOCK, category="UI")
    stamped = f'<html><body><div data-ls-contentblock="{block_id}"><p>Block one</p><p>Block two</p></div></body></html>'
    assert "Blocco uno" in client.translate_page(stamped, category="UI"), "the page path dropped it"


def test_CACHE2_control_with_nothing_held_a_refused_fetch_renders_source(httpx_mock, now):
    _refused(httpx_mock)
    assert make().translate("Save", category="UI", locale="it-it") == "Save"
