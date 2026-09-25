"""CACHE-2 - a failed catalog fetch is remembered for a bounded window; REG-13 - a miss is decided
only against a catalog that has loaded.

CACHE-2's degradation and window run against the contract double: a lookup inside the window
renders source, which it could not if the SDK had fetched again, because the double's fault is
consumed and a second fetch would return the catalog. The window's growth and the sharing of
concurrent fetches are proven at the SDK's own seam.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from contract import PROJECT, WRITE_KEY, world

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch, CatalogStore

CATALOG = [{"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Cassa"}}]
FAIL_ONCE = [{"method": "GET", "path": "/translations", "status": 500}]


class Clock:
    """The SDK's monotonic clock, moved by the test."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    c = Clock()
    with patch("langsys.catalog._clock", c):
        yield c


def client(double, **kw) -> LangsysClient:
    return LangsysClient(WRITE_KEY, PROJECT, api_url=double.base_url, cache=MemoryCache(),
                         base_locale="en-us", debounce=0, auto_flush=False, **kw)


def t(c: LangsysClient) -> str:
    return c.translate("Checkout", category="UI", locale="it-it")


# -- against the contract double -------------------------------------------------------------------


def test_CACHE2_a_lookup_inside_the_window_renders_source_without_fetching_again(double, clock):
    double.seed(world(phrases=CATALOG, faults=FAIL_ONCE))
    c = client(double)
    assert t(c) == "Checkout", "control: the first fetch fails and degrades"
    clock.now += 1.0
    assert t(c) == "Checkout", "fetched again inside the window (the double would have answered)"
    assert not c.has_pending, "WIRE-4: nothing is queued inside the window"


def test_CACHE2_after_the_window_the_translation_renders(double, clock):
    double.seed(world(phrases=CATALOG, faults=FAIL_ONCE))
    c = client(double)
    t(c)
    clock.now += 3.5
    assert t(c) == "Cassa"


def test_CACHE2_control_a_successful_first_fetch_renders_at_once(double, clock):
    double.seed(world(phrases=CATALOG))
    assert t(client(double)) == "Cassa"


def test_CACHE2_the_window_is_per_locale(double, clock):
    double.seed(world(
        phrases=[{"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Cassa", "es-es": "Caja"}}],
        faults=FAIL_ONCE,
    ))
    c = client(double)
    assert t(c) == "Checkout"
    assert c.translate("Checkout", category="UI", locale="es-es") == "Caja"


# -- the window itself, at the seam ----------------------------------------------------------------


class FakeHttp:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.lock = threading.Lock()

    def get(self, path, params=None):
        with self.lock:
            self.calls += 1
            answer = self.answers.pop(0) if self.answers else {"status": True, "data": {}}
        if isinstance(answer, float):
            time.sleep(answer)
            return {"status": True, "data": {"UI": {"Checkout": "Cassa"}}}
        if isinstance(answer, Exception):
            raise answer
        return answer


def store(http) -> CatalogStore:
    return CatalogStore(http, "p", MemoryCache())  # type: ignore[arg-type]


def test_CACHE2_the_window_starts_at_3s_doubles_to_300s_and_resets_on_success(clock):
    from langsys.exceptions import NetworkError

    http = FakeHttp([NetworkError("down")] * 20)
    s = store(http)
    windows = []
    for _ in range(9):
        assert s.get("it-it").ok is False
        before = http.calls
        clock.now += 0.001
        assert s.get("it-it").ok is False and http.calls == before, "fetched inside the window"
        windows.append(s._failures["it-it"][1])
        clock.now += windows[-1]
    assert windows[:4] == [3.0, 6.0, 12.0, 24.0] and windows[-1] == 300.0, windows
    http.answers = [{"status": True, "data": {"UI": {}}}]
    assert s.get("it-it").ok is True
    assert "it-it" not in s._failures, "the window did not reset on success"


def test_CACHE2_status_false_is_a_failure_too(clock):
    http = FakeHttp([{"status": False, "error": "nope"}, {"status": True, "data": {"UI": {}}}])
    s = store(http)
    assert s.get("it-it").ok is False
    assert s.get("it-it").ok is False and http.calls == 1


def test_CACHE2_concurrent_fetches_for_one_locale_share_one_request():
    http = FakeHttp([0.2])
    s = store(http)
    results: list[CatalogFetch] = []
    threads = [threading.Thread(target=lambda: results.append(s.get("it-it"))) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert http.calls == 1, f"{http.calls} requests for one catalog"
    assert all(r.ok and r.catalog == {"UI": {"Checkout": "Cassa"}} for r in results)


def test_CACHE2_the_failure_is_never_stored_as_a_catalog(clock):
    from langsys.exceptions import NetworkError

    cache = MemoryCache()
    s = CatalogStore(FakeHttp([NetworkError("down")]), "p", cache)  # type: ignore[arg-type]
    s.get("it-it")
    assert cache._store == {}, cache._store


# -- REG-13 ----------------------------------------------------------------------------------------


def test_REG13_a_miss_is_not_decided_before_the_first_catalog_read_settles():
    """With the first read delayed past the debounce and already holding the phrase, the candidate
    set stays empty until the read settles, and after it too: the phrase was known."""
    c = LangsysClient("k", "p", api_url="https://api.test/api", cache=MemoryCache(),
                      base_locale="en-us", debounce=0.05, auto_flush=False)
    released = threading.Event()

    def slow_get(locale, use_cache=True):
        released.wait(2)
        return CatalogFetch({"UI": {"Checkout": "Cassa"}}, ok=True)

    try:
        with patch.object(c._catalog, "get", side_effect=slow_get):
            render = threading.Thread(target=lambda: c.translate("Checkout", category="UI", locale="it-it"))
            render.start()
            time.sleep(0.15)  # three debounce windows
            assert c.pending_phrases == [], "decided against a catalog that had not loaded"
            released.set()
            render.join(2)
        assert c.pending_phrases == []
    finally:
        c._cancel_timer()


def test_REG13_control_a_phrase_the_loaded_catalog_lacks_is_a_miss():
    c = LangsysClient("k", "p", api_url="https://api.test/api", cache=MemoryCache(),
                      base_locale="en-us", debounce=0, auto_flush=False)
    with patch.object(c._catalog, "get", return_value=CatalogFetch({"UI": {}}, ok=True)):
        c.translate("Checkout", category="UI", locale="it-it")
    assert c.pending_phrases == [{"phrase": "Checkout", "category": "UI"}]
