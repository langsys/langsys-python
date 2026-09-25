"""SRV-1..3 — the server render profile.

This SDK is a server core, so these bind. SRV-4/5 are the JS hydration and component
half and are recorded `n/a` in CONFORMANCE with that reason.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time

import httpx
import pytest

import langsys
import langsys.scope
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


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_SRV3_collection_does_not_happen_on_the_render_call(httpx_mock):
    """The order-of-events half, as far as a library can assert it: `translate()` is the
    render path, and it must queue without sending. A test that only checks a miss was
    eventually collected passes against an implementation that collects it inline and
    hands the visitor the latency.

    The double is fully able to accept a registration: a write-enabled authorize and the
    registration endpoint are both served. Without them an inline flush could not POST, and
    "nothing was sent" would pass against the very implementation this exists to catch.

    The *response*-flush boundary itself is the wrapper's to enforce — declared in
    CONFORMANCE, because a library has no response to flush."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}},
        is_reusable=True,
    )
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
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



# -- SRV-3: request scopes. The order of events is the contract ------------------------------
#
# Asserted as an ORDER, not as "a miss was eventually collected": that passes against an SDK that
# collects inline and hands the visitor the latency. Two failures, both measured by the wrappers:
# a render that outlasts the debounce had its misses POSTed by the timer before the response
# existed, and with no timer at all one request's flush drained another in-flight request's.


def _recording(httpx_mock, events):
    def translatable_items(request: httpx.Request) -> httpx.Response:
        phrases = [item["phrase"] for item in json.loads(request.content)["translatable_items"]]
        events.append(("posted", *phrases))
        return httpx.Response(200, json={"status": True})

    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}},
        is_reusable=True,
    )
    httpx_mock.add_callback(translatable_items, url=ITEMS, is_reusable=True)


def _posted(events, phrase):
    return [i for i, e in enumerate(events) if isinstance(e, tuple) and phrase in e]


def test_SRV3_a_miss_is_not_sent_before_a_render_longer_than_the_debounce_has_responded(httpx_mock):
    events: list[object] = []
    _recording(httpx_mock, events)
    client = make(debounce=0.05)
    try:
        scope = langsys.begin_request_scope()
        client.translate("Checkout", category="UI", locale="it-it")
        time.sleep(0.4)  # the render goes on well past the debounce window
        events.append("rendered")
        events.append("response-returned")
        langsys.end_request_scope(scope)
        client.flush_pending()  # the wrapper's post-response flush
    finally:
        client._cancel_timer()
    assert _posted(events, "Checkout"), f"control: the miss was never collected at all: {events}"
    assert events.index("response-returned") < _posted(events, "Checkout")[0], events


def test_SRV3_one_requests_flush_does_not_send_another_in_flight_requests_misses(httpx_mock):
    """No timer: the debounce is off. Ordered by events, not sleeps - the quick request waits
    until the held one has recorded its miss, and the held one is released only after the quick
    one's flush."""
    events: list[object] = []
    _recording(httpx_mock, events)
    client = make()
    held_recorded, quick_flushed = threading.Event(), threading.Event()

    def held_request() -> None:
        with langsys.request_scope():
            client.translate("Held miss", category="UI", locale="it-it")
            held_recorded.set()
            quick_flushed.wait(5)  # still rendering
            events.append("held-response")
        client.flush_pending()

    def quick_request() -> None:
        held_recorded.wait(5)
        with langsys.request_scope():
            client.translate("Quick miss", category="UI", locale="it-it")
            events.append("quick-response")
        client.flush_pending()
        quick_flushed.set()

    threads = [threading.Thread(target=held_request), threading.Thread(target=quick_request)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert _posted(events, "Quick miss"), f"control: the quick request's miss never went: {events}"
    assert events.index("quick-response") < _posted(events, "Quick miss")[0], events
    held = _posted(events, "Held miss")
    assert held, f"control: the held miss was never collected at all: {events}"
    assert events.index("held-response") < held[0], events


def test_SRV3_a_miss_outside_any_scope_keeps_the_debounce(httpx_mock):
    """A worker, a management command, a script: nothing to wait for."""
    events: list[object] = []
    _recording(httpx_mock, events)
    client = make(debounce=0.05)
    try:
        client.translate("Background miss", category="UI", locale="it-it")
        deadline = time.monotonic() + 3
        while not _posted(events, "Background miss") and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        client._cancel_timer()
    assert _posted(events, "Background miss"), events


def test_SRV3_an_explicit_flush_inside_the_scope_declines_and_keeps_the_miss(httpx_mock):
    events: list[object] = []
    _recording(httpx_mock, events)
    client = make()
    with langsys.request_scope():
        client.translate("Checkout", category="UI", locale="it-it")
        result = client.flush_pending()
        assert result.get("reason") == "request-in-progress", result
        assert client.has_pending
    assert client.flush_pending()["success"] is True
    assert _posted(events, "Checkout")


def test_SRV3_an_unended_scope_holds_its_misses_until_the_shutdown_flush(httpx_mock):
    events: list[object] = []
    _recording(httpx_mock, events)
    client = make()
    token = langsys.scope._CURRENT.set(langsys.scope.RequestScope())
    try:
        client.translate("Checkout", category="UI", locale="it-it")
        client.flush_pending()
        assert events == [], "a flush sent a miss whose request never ended"
        client._auto_flush()  # REG-3's last attempt releases everything
    finally:
        langsys.scope._CURRENT.reset(token)
    assert _posted(events, "Checkout"), events


def test_SRV3_a_client_built_during_the_request_joins_its_scope(httpx_mock):
    """Bindings build the client lazily, so the first request of a worker records misses before
    any client existed to open a scope on. The scope is ambient, not the client's."""
    events: list[object] = []
    _recording(httpx_mock, events)
    scope = langsys.begin_request_scope()
    client = make()
    client.translate("Checkout", category="UI", locale="it-it")
    client.flush_pending()
    assert events == []
    langsys.end_request_scope(scope)
    client.flush_pending()
    assert _posted(events, "Checkout")


def test_SRV3_either_request_that_recorded_a_miss_releases_it(httpx_mock):
    events: list[object] = []
    _recording(httpx_mock, events)
    client = make()
    first = langsys.begin_request_scope()
    client.translate("Shared", category="UI", locale="it-it")
    second = langsys.begin_request_scope()
    client.translate("Shared", category="UI", locale="it-it")
    langsys.end_request_scope(second)
    client.flush_pending()
    langsys.end_request_scope(first)
    assert _posted(events, "Shared"), events


def test_SRV3_scopes_are_per_asyncio_task(httpx_mock):
    """Async servers serve many requests on one thread; the scope follows the task."""
    events: list[object] = []
    _recording(httpx_mock, events)
    client = make()

    async def request(name: str, release: asyncio.Event, answered: asyncio.Event) -> None:
        scope = langsys.begin_request_scope()
        client.translate(name, category="UI", locale="it-it")
        await release.wait()
        events.append(f"{name} response")
        langsys.end_request_scope(scope)
        answered.set()

    async def main() -> None:
        slow_release, fast_release = asyncio.Event(), asyncio.Event()
        slow_answered, fast_answered = asyncio.Event(), asyncio.Event()
        tasks = [
            asyncio.ensure_future(request("Slow", slow_release, slow_answered)),
            asyncio.ensure_future(request("Fast", fast_release, fast_answered)),
        ]
        await asyncio.sleep(0)
        fast_release.set()
        await fast_answered.wait()
        client.flush_pending()  # the fast request's post-response flush
        slow_release.set()
        await asyncio.gather(*tasks)
        client.flush_pending()

    asyncio.run(main())
    assert events.index("Fast response") < _posted(events, "Fast")[0], events
    assert events.index("Slow response") < _posted(events, "Slow")[0], events


def test_SRV3_a_miss_already_free_stays_free_when_a_request_records_it_again(httpx_mock):
    """Recorded first by a background job, then during a request: the job's miss has nothing to
    wait for, and a later request touching the same phrase must not hold it back."""
    events: list[object] = []
    _recording(httpx_mock, events)
    client = make()
    client.translate("Shared", category="UI", locale="it-it")
    with langsys.request_scope():
        client.translate("Shared", category="UI", locale="it-it")
        client.flush_pending()
        assert _posted(events, "Shared"), "a free miss was held behind a request"


def test_SRV3_ending_the_scope_arms_the_debounce(httpx_mock):
    """With the debounce on, the released miss goes by itself once the response is out."""
    events: list[object] = []
    _recording(httpx_mock, events)
    client = make(debounce=0.05)
    try:
        with langsys.request_scope():
            client.translate("Checkout", category="UI", locale="it-it")
            time.sleep(0.2)
            assert events == []
        deadline = time.monotonic() + 3
        while not _posted(events, "Checkout") and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        client._cancel_timer()
    assert _posted(events, "Checkout"), events

def test_SRV3_a_scope_can_be_ended_from_another_context():
    """ASGI middleware can send the response from a different task than the one that began."""
    import contextvars

    scope = langsys.begin_request_scope()
    contextvars.Context().run(langsys.end_request_scope, scope)
    assert scope.ended
    assert langsys.scope.current_scope() is None


# -- HINT-2 -------------------------------------------------------------------


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
@pytest.mark.parametrize("write_enabled", [False, True])
def test_HINT2_a_server_sdk_never_reports_across_a_whole_render(httpx_mock, write_enabled):
    """A server SDK is the origin. A hint asks our renderer to load a page so the page's own
    SDK can register, and the page's own SDK is this one. The double WILL accept a hint; the
    assertion is that none is ever sent, on both sides of the capability split, by a render
    that demonstrably talks to the API through every entry point."""
    pytest.importorskip("lxml")
    httpx_mock.add_response(url=re.compile(r".*/discovery/hint.*"), status_code=204, is_reusable=True)
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=write_enabled), is_reusable=True)
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": write_enabled, "data": {"UI": {}}},
        is_reusable=True,
    )
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    client.translate("Checkout", category="UI", locale="it-it")
    client.translate_content_block("<div><p>One</p><p>Two</p></div>", category="UI")
    client.translate_page("<html><body><p>Pricing</p></body></html>", category="UI")
    assert client.has_pending, "control: the render discovered nothing, so there was nothing to report"
    client.flush_pending()

    paths = [r.url.path for r in httpx_mock.get_requests()]
    assert any(path.endswith("translations") for path in paths), f"control: no API traffic: {paths}"
    assert any(path.endswith("authorize-project/proj-1") for path in paths), "control: the lane was never chosen"
    assert not any("hint" in path or "discovery" in path for path in paths), paths
