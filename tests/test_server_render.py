"""SRV-1..3 — the server render profile.

This SDK is a server core, so these bind. SRV-4/5 are the JS hydration and component
half and are recorded `n/a` in CONFORMANCE with that reason.
"""

from __future__ import annotations

import re
import threading
import time

import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch

API = "https://api.test/api"
AUTH = f"{API}/authorize-project/proj-1"
ITEMS = f"{API}/translatable-items"
TRANS = re.compile(r"https://api\.test/api/translations")


def make(**kw):
    kw.setdefault("debounce", 0)
    kw.setdefault("auto_flush", False)
    return LangsysClient(
        "k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us", **kw
    )


def auth(key_type="write", write_enabled=True):
    return {
        "status": True,
        "data": {
            "id": "proj-1", "title": "T", "base_locale": "en-us",
            "target_locales": ["it-it", "de-de"], "default_locales": {},
            "key_type": key_type, "write_enabled": write_enabled,
            "langsys_settings": {"translatable_items": {"batch_limit": 200}},
        },
    }


# -- SRV-1 --------------------------------------------------------------------


def test_SRV1_the_served_output_carries_the_request_locale_translation(httpx_mock):
    """The served bytes are the page for every reader not running our JavaScript —
    crawlers first. Translating after hydration is a flash to a user and invisible to a
    crawler, which indexes the base language under the localised URL."""
    httpx_mock.add_response(
        url=TRANS,
        json={"status": True, "write_enabled": True,
              "data": {"UI": {"Pricing": "Prezzi"}}},
        is_reusable=True,
    )
    client = make()
    assert client.translate("Pricing", category="UI", locale="it-it") == "Prezzi"


def test_SRV1_control_a_phrase_absent_from_the_catalog_emits_base_and_is_a_miss(httpx_mock):
    """What separates "translated correctly" from "rendered a catalog that happened to
    be complete". Same render, one phrase present and one absent."""
    httpx_mock.add_response(
        url=TRANS,
        json={"status": True, "write_enabled": True,
              "data": {"UI": {"Pricing": "Prezzi"}}},
        is_reusable=True,
    )
    client = make()
    assert client.translate("Pricing", category="UI", locale="it-it") == "Prezzi"
    assert client.translate("Checkout", category="UI", locale="it-it") == "Checkout"
    assert {"phrase": "Checkout", "category": "UI"} in client.pending_phrases
    assert {"phrase": "Pricing", "category": "UI"} not in client.pending_phrases


# -- SRV-2 --------------------------------------------------------------------


def test_SRV2_concurrent_locales_do_not_observe_each_others_catalog():
    """Running them sequentially proves nothing; the failure is the interleave. A wrong
    value in a browser harms one user — the same value in a process global decides what
    every visitor to the host sees, intermittently and under load."""
    from unittest.mock import patch

    catalogs = {
        "it-it": {"UI": {"Pricing": "Prezzi"}},
        "de-de": {"UI": {"Pricing": "Preise"}},
    }
    client = make()

    def slow_get(locale, use_cache=True):
        time.sleep(0.02)  # widen the interleave window
        return CatalogFetch(catalogs[locale.lower()], ok=True)

    seen: dict[str, str] = {}
    with patch.object(client._catalog, "get", side_effect=slow_get):
        def render(loc: str) -> None:
            seen[loc] = client.translate("Pricing", category="UI", locale=loc)

        threads = [threading.Thread(target=render, args=(loc,)) for loc in catalogs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

    assert seen["it-it"] == "Prezzi", seen
    assert seen["de-de"] == "Preise", seen


def test_SRV2_no_process_global_holds_per_request_translation_state():
    """Two clients are two catalogs. A module-level cache keyed without the locale would
    make the second client serve the first's text."""
    from unittest.mock import patch

    a, b = make(), make()
    with patch.object(a._catalog, "get", return_value=CatalogFetch({"UI": {"P": "IT"}}, ok=True)):
        assert a.translate("P", category="UI", locale="it-it") == "IT"
    with patch.object(b._catalog, "get", return_value=CatalogFetch({"UI": {"P": "DE"}}, ok=True)):
        assert b.translate("P", category="UI", locale="de-de") == "DE"


# -- SRV-3 --------------------------------------------------------------------


def test_SRV3_a_read_only_key_pushes_nothing(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("read", write_enabled=False), is_reusable=True)
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": False, "data": {"UI": {}}},
        is_reusable=True,
    )
    client = make()
    client.translate("Checkout", category="UI", locale="it-it")
    assert client.has_pending
    client.flush_pending()
    posts = [r for r in httpx_mock.get_requests() if r.url.path.endswith("translatable-items")]
    assert posts == [], "a read-only key pushed misses"


def test_SRV3_control_a_write_key_on_the_same_render_does_push(httpx_mock):
    """The read-only half alone passes against an implementation that never pushes at
    all — GATE-7's failure wearing a permission's clothing."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}},
        is_reusable=True,
    )
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    client.translate("Checkout", category="UI", locale="it-it")
    client.flush_pending()
    posts = [r for r in httpx_mock.get_requests() if r.url.path.endswith("translatable-items")]
    assert posts, "the control did not push, so the read-only assertion proves nothing"


def test_SRV3_collection_does_not_happen_on_the_render_call(httpx_mock):
    """The order-of-events half, as far as a library can assert it: `translate()` is the
    render path, and it must queue without sending. A test that only checks a miss was
    eventually collected passes against an implementation that collects it inline and
    hands the visitor the latency.

    The *response*-flush boundary itself is the wrapper's to enforce — declared in
    CONFORMANCE, because a library has no response to flush."""
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}},
        is_reusable=True,
    )
    client = make()  # debounce=0, so nothing can send behind our back
    client.translate("Checkout", category="UI", locale="it-it")
    posts = [r for r in httpx_mock.get_requests() if r.url.path.endswith("translatable-items")]
    assert posts == [], "the render path sent a registration inline"
    assert client.has_pending, "control: the miss must actually have been recorded"


@pytest.mark.parametrize("debounce", [0.05])
def test_SRV3_the_send_happens_off_the_render_call(httpx_mock, debounce):
    """And with the debounce on, the send lands later and from another thread — never
    inside the caller's `translate()`."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}},
        is_reusable=True,
    )
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make(debounce=debounce)
    try:
        client.translate("Checkout", category="UI", locale="it-it")
        immediate = [
            r for r in httpx_mock.get_requests() if r.url.path.endswith("translatable-items")
        ]
        assert immediate == [], "the render call sent inline"

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if any(r.url.path.endswith("translatable-items") for r in httpx_mock.get_requests()):
                break
            time.sleep(0.01)
        assert any(
            r.url.path.endswith("translatable-items") for r in httpx_mock.get_requests()
        ), "the miss was never collected at all"
    finally:
        client._cancel_timer()
