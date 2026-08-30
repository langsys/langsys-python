"""REG-2, REG-3, REG-8 and REG-11 — the registration lane's timing and hygiene.

These four compose, and the composition is where the bugs are:

* REG-2's debounce must not become REG-8's busy loop.
* REG-8's backoff must not defeat REG-3's last-chance flush at shutdown.
* Neither may resurrect the WIRE-4 guard's write storm: a catalog we could not read
  queues nothing, so there must be nothing for a retry loop to grow.

Every "does not send" assertion counts **requests actually made**, not return values,
and is paired with a control proving the same call does send when it should.
"""

from __future__ import annotations

import re
import time

import httpx
import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache

API = "https://api.test/api"
AUTH = f"{API}/authorize-project/proj-1"
ITEMS = f"{API}/translatable-items"
TRANS = re.compile(r"https://api\.test/api/translations")


_CLIENTS: list[LangsysClient] = []


def make(cache=None, **kw):
    kw.setdefault("debounce", 0)  # deterministic unless a test asks otherwise
    kw.setdefault("auto_flush", False)
    client = LangsysClient(
        "k", "proj-1", api_url=API, cache=cache or MemoryCache(), base_locale="en-us", **kw
    )
    _CLIENTS.append(client)
    return client


@pytest.fixture(autouse=True)
def _no_timer_leaks():
    """Cancel every debounce timer at teardown.

    Without this a timer armed by one test fires during the next one and consumes its
    mocks — the test suite's own version of the cross-request bleed these rules are
    about. Failing to clean up here produced errors that moved depending on run order,
    which is exactly the kind of flake that gets a real finding dismissed as noise.
    """
    _CLIENTS.clear()
    yield
    for client in _CLIENTS:
        client._cancel_timer()
    _CLIENTS.clear()


def auth(key_type="write", write_enabled=True):
    return {
        "status": True,
        "data": {
            "id": "proj-1",
            "title": "T",
            "base_locale": "en-us",
            "target_locales": ["es-es"],
            "default_locales": {},
            "key_type": key_type,
            "write_enabled": write_enabled,
            "langsys_settings": {"translatable_items": {"batch_limit": 200}},
        },
    }


def catalog(data=None):
    return {"status": True, "write_enabled": True, "data": data or {"UI": {}}}


def item_posts(httpx_mock):
    return [r for r in httpx_mock.get_requests() if r.url.path.endswith("translatable-items")]


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# -- REG-2: debounce ----------------------------------------------------------


def test_REG2_a_burst_of_misses_becomes_one_request(httpx_mock):
    """"Send on a short debounce so a burst from one render becomes one request."
    Five misses in a render must not be five POSTs."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make(debounce=0.05)
    for i in range(5):
        client.translate(f"Phrase {i}", category="UI", locale="en-us")

    assert wait_until(lambda: len(item_posts(httpx_mock)) >= 1), "the debounce never fired"
    time.sleep(0.15)  # let any further timers land
    posts = item_posts(httpx_mock)
    assert len(posts) == 1, f"burst was not coalesced: {len(posts)} POSTs"
    client.close()


def test_REG2_the_debounce_is_a_real_send_path_not_just_a_helper(httpx_mock):
    """No explicit flush anywhere in this test. If the only way to send is an explicit
    call, an interval — or a caller who forgets — is the only path, which is what the
    rule forbids."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make(debounce=0.05)
    client.translate("Only phrase", category="UI", locale="en-us")
    assert wait_until(lambda: item_posts(httpx_mock)), "nothing was ever sent"
    assert client.has_pending is False
    client.close()


def test_CONTROL_no_debounce_means_no_automatic_send(httpx_mock):
    """Positive control for the two above: with the debounce off, nothing sends by
    itself, so those tests are observing the debounce and not some other path."""
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    client = make(debounce=0)
    client.translate("Only phrase", category="UI", locale="en-us")
    time.sleep(0.15)
    assert item_posts(httpx_mock) == []
    assert client.has_pending is True


def test_REG2_an_explicit_flush_sends_immediately_and_cancels_the_timer(httpx_mock):
    """The debounce delays sending; it must not become a floor on latency for a caller
    who has a boundary of its own."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make(debounce=5.0)  # long enough that the timer cannot be what sent it
    client.translate("Phrase", category="UI", locale="en-us")
    assert client.flush_pending()["success"] is True
    assert len(item_posts(httpx_mock)) == 1
    assert client._timer is None, "the timer outlived the flush it was scheduled for"
    client.close()


# -- REG-3: flush before the execution context ends ---------------------------


def test_REG3_a_public_manual_flush_exists_and_is_not_the_automatic_path(httpx_mock):
    """Server SDKs MUST expose a public manual flush and MUST NOT treat the automatic
    path as reliable — a shutdown hook does not run on an OOM kill or a hard timeout."""
    assert callable(LangsysClient.flush_pending)
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    client.translate("Phrase", category="UI", locale="en-us")
    assert client.flush_pending()["success"] is True


def test_REG3_the_end_of_context_flush_is_registered_by_default(httpx_mock):
    """Opt-in-by-default was the shipping bug the rule names: phrased as "flush on page
    teardown" it reads browser-only and the server SDKs skip it."""
    import atexit

    registered = []
    original = atexit.register

    def spy(fn, *a, **kw):
        registered.append(fn)
        return original(fn, *a, **kw)

    atexit.register = spy  # type: ignore[assignment]
    try:
        client = LangsysClient(
            "k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us", debounce=0
        )
    finally:
        atexit.register = original  # type: ignore[assignment]
    assert any(fn == client._auto_flush for fn in registered)


def test_REG3_the_shutdown_flush_forces_past_an_active_backoff(httpx_mock):
    """The context is ending, so this is the last attempt rather than a retry loop —
    and a queue discarded here is discarded for good."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    httpx_mock.add_exception(httpx.ConnectError("down"), url=ITEMS)
    client = make()
    client.translate("Phrase", category="UI", locale="en-us")
    client.flush_pending()  # fails -> backoff armed
    assert client._backoff_seconds > 0
    before = len(item_posts(httpx_mock))

    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client._auto_flush()
    assert len(item_posts(httpx_mock)) == before + 1, "shutdown flush was blocked by backoff"
    assert client.has_pending is False


def test_REG3_the_shutdown_flush_never_raises(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("down"), url=AUTH, is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    client = make()
    client.translate("Phrase", category="UI", locale="en-us")
    client._auto_flush()  # must not raise


# -- REG-8: exponential backoff ----------------------------------------------


def _failing_client(httpx_mock, **kw):
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    httpx_mock.add_exception(httpx.ConnectError("down"), url=ITEMS, is_reusable=True)
    client = make(**kw)
    client.translate("Phrase", category="UI", locale="en-us")
    return client


def test_REG8_a_failed_send_keeps_the_queue_and_arms_a_backoff(httpx_mock):
    client = _failing_client(httpx_mock)
    result = client.flush_pending()
    assert result["success"] is False
    assert client.has_pending, "the queue was dropped on failure"
    assert client._backoff_seconds == pytest.approx(3.0)
    assert result["retry_in_seconds"] == pytest.approx(3.0, abs=0.1)


def test_REG8_the_backoff_doubles_and_stops_at_the_ceiling(httpx_mock):
    client = _failing_client(httpx_mock)
    seen = []
    for _ in range(12):
        client.flush_pending(force=True)
        seen.append(client._backoff_seconds)
    assert seen[:3] == [3.0, 6.0, 12.0], seen[:3]
    assert seen[-1] == 300.0, f"ceiling not reached or exceeded: {seen[-1]}"
    assert max(seen) <= 300.0


def test_REG8_while_backing_off_nothing_is_sent(httpx_mock):
    """The behaviour the rule exists for: without this a failing endpoint gets a
    request every interval for as long as the process lives, against a payload that
    grows as new misses join a queue that never drains."""
    client = _failing_client(httpx_mock)
    client.flush_pending()
    sent = len(item_posts(httpx_mock))

    for _ in range(5):
        result = client.flush_pending()
        assert result["reason"] == "backoff"
    assert len(item_posts(httpx_mock)) == sent, "backoff did not stop the sends"
    assert client.has_pending, "the queue was dropped while backing off"


def test_REG8_the_queue_keeps_growing_but_the_sends_do_not(httpx_mock):
    """New misses join a backing-off queue — that is expected and is why the payload
    grows. What must not grow is the request count."""
    client = _failing_client(httpx_mock)
    client.flush_pending()
    sent = len(item_posts(httpx_mock))
    for i in range(10):
        client.translate(f"Later phrase {i}", category="UI", locale="en-us")
        client.flush_pending()
    assert len(item_posts(httpx_mock)) == sent
    assert len(client.pending_phrases) == 11


def test_REG8_backoff_resets_on_the_first_success(httpx_mock):
    """Reset on first success, not gradually — a recovered endpoint is recovered."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    httpx_mock.add_exception(httpx.ConnectError("down"), url=ITEMS)
    client = make()
    client.translate("Phrase", category="UI", locale="en-us")
    client.flush_pending()
    assert client._backoff_seconds > 0

    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    assert client.flush_pending(force=True)["success"] is True
    assert client._backoff_seconds == 0.0
    assert client._backoff_until == 0.0


def test_REG8_the_debounce_does_not_send_through_a_backoff(httpx_mock):
    """Outer guarantee: no requests leave while backing off, however often the debounce
    is rescheduled."""
    client = _failing_client(httpx_mock, debounce=0.05)
    client.flush_pending()  # arm the backoff
    sent = len(item_posts(httpx_mock))
    for i in range(5):
        client.translate(f"More {i}", category="UI", locale="en-us")  # each reschedules
    time.sleep(0.4)  # ~8 debounce periods, well inside the 3s backoff
    assert len(item_posts(httpx_mock)) == sent, "the debounce retried through the backoff"
    client.close()


def test_REG8_the_debounce_reschedules_past_the_backoff_rather_than_waking_into_it(
    httpx_mock,
):
    """The inner half, and the reason this test exists separately.

    The test above passes even if the debounce ignores the backoff entirely — because
    `flush_pending` declines a send on its own, so no request escapes either way. The
    request count cannot see the difference, so a mutation removing the delay extension
    goes green there. It is a real cost though: a wake every 50ms for up to five
    minutes, doing nothing each time.

    So assert the thing the scheduling actually decides — the delay — rather than a
    consequence that something else already guarantees."""
    client = _failing_client(httpx_mock, debounce=0.05)
    client.flush_pending()  # arm the ~3s backoff
    assert client._backoff_seconds == pytest.approx(3.0)

    client.translate("Another", category="UI", locale="en-us")  # reschedules the timer
    assert client._timer is not None, "no timer was scheduled"
    assert client._timer.interval > 1.0, (
        f"debounce woke into the backoff after {client._timer.interval}s rather than "
        "sleeping until it expires"
    )
    client.close()


def test_REG8_composes_with_the_WIRE4_guard_a_dead_catalog_cannot_storm(httpx_mock):
    """A catalog we could not read records nothing (WIRE-4), so there is nothing for a
    retry loop to grow. Asserted as a boundary property: zero registration requests."""
    # No AUTH mock is registered, deliberately. If this test needed one, something had
    # tried to resolve write capability — which would mean something had tried to
    # register off a catalog it could not read.
    httpx_mock.add_exception(httpx.ConnectError("catalog down"), url=TRANS, is_reusable=True)
    client = make(debounce=0.05)
    for i in range(10):
        client.translate(f"Phrase {i}", category="UI", locale="en-us")
    time.sleep(0.2)
    assert client.has_pending is False
    assert item_posts(httpx_mock) == [], "an unreadable catalog produced registrations"
    client.close()


# -- REG-11: ellipsis-terminated text ----------------------------------------


@pytest.mark.parametrize("suffix", ["…", "..."])
def test_REG11_warns_but_still_registers_without_a_second_signal(httpx_mock, caplog, suffix):
    """"Loading…", "Saving…", "Please wait…" are legitimate. Silently refusing to
    register them would create a NEW silent failure — the class this spec removes."""
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    client = make()
    phrase = f"Loading{suffix}"
    with caplog.at_level("WARNING", logger="langsys"):
        client.translate(phrase, category="UI", locale="en-us")
    assert any(phrase in r.getMessage() for r in caplog.records), "no warning named the phrase"
    assert {"phrase": phrase, "category": "UI"} in client.pending_phrases


def test_REG11_suppresses_only_when_a_longer_entry_shares_the_prefix(httpx_mock, caplog):
    """The actual harm condition: the full paragraph is already in the catalog, so the
    truncated form is pollution — and it fires only once the pollution has occurred."""
    full = "The quick brown fox jumps over the lazy dog and keeps going"
    truncated = "The quick brown fox jumps…"
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {full: "…"}}), is_reusable=True)
    client = make()
    with caplog.at_level("WARNING", logger="langsys"):
        client.translate(truncated, category="UI", locale="en-us")
    assert client.pending_phrases == [], "registered a truncated form of a known phrase"
    assert any("longer phrase" in r.getMessage() for r in caplog.records)


def test_REG11_a_shorter_sibling_does_not_suppress(httpx_mock):
    """Sharing a prefix is not enough — the sibling must be LONGER, or a legitimate
    ellipsis phrase would be suppressed by its own stem."""
    httpx_mock.add_response(
        url=TRANS, json=catalog({"UI": {"Loading": "Cargando"}}), is_reusable=True
    )
    client = make()
    client.translate("Loading…", category="UI", locale="en-us")
    assert client.pending_phrases, "a shorter sibling suppressed a legitimate phrase"


def test_REG11_the_warning_is_deduplicated_per_phrase(httpx_mock, caplog):
    """The check runs on every render; the developer needs to learn once."""
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    client = make()
    with caplog.at_level("WARNING", logger="langsys"):
        for _ in range(5):
            client.translate("Loading…", category="UI", locale="en-us")
    hits = [r for r in caplog.records if "Loading" in r.getMessage()]
    assert len(hits) == 1, f"expected one warning, got {len(hits)}"


def test_CONTROL_a_normal_phrase_warns_nothing(httpx_mock, caplog):
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    client = make()
    with caplog.at_level("WARNING", logger="langsys"):
        client.translate("A normal phrase", category="UI", locale="en-us")
    assert caplog.records == []
    assert client.pending_phrases


def test_REG11_an_ellipsis_only_phrase_is_not_treated_as_truncation(httpx_mock):
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    client = make()
    client.translate("…", category="UI", locale="en-us")
    assert client.pending_phrases, "a bare ellipsis was treated as a truncated phrase"


# -- REG-6 / REG-7: no longer n/a, because REG-2's debounce added a second thread ----
#
# Both were filed `n/a (synchronous)` in wave 1, correctly: there was no await window
# and no concurrent sender. Adding the debounce timer created both. The expiry
# condition on those rows was "an async twin lands" — a background timer is that twin
# arriving through a side door, which is why the rows are re-verified here rather than
# carried forward.


def test_REG6_a_miss_recorded_during_a_send_is_not_dropped(httpx_mock):
    """"Snapshot the batch; never clear the live queue after an await."

    The POST is slow and the lock is released across it, so a render on the caller's
    thread lands between the snapshot and the cleanup. Clearing the whole queue there
    silently loses every phrase seen during the send — and nothing logs."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)

    client = make()
    client.translate("First", category="UI", locale="en-us")

    def slow_post(*args, **kwargs):
        # Arrives while the batch above is in flight.
        client._queue_missing("Recorded mid-send", "UI")
        return {"status": True}

    client._reg._post_items = slow_post  # type: ignore[method-assign]
    assert client.flush_pending()["success"] is True

    remaining = [p["phrase"] for p in client.pending_phrases]
    assert "Recorded mid-send" in remaining, "a miss recorded during the send was dropped"
    assert "First" not in remaining, "the sent batch was not cleared"


def test_REG7_only_one_send_is_in_flight_at_a_time(httpx_mock):
    """The debounce timer and an explicit flush are different threads and can arrive
    together; two senders would POST the same snapshot twice."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)

    client = make()
    client.translate("First", category="UI", locale="en-us")
    seen = []

    def reentrant_post(items):
        seen.append(len(items))
        # A second flush arriving mid-send must decline, not send the same batch again.
        nested = client.flush_pending()
        assert nested["reason"] == "send-in-flight", nested
        return {"status": True}

    client._reg._post_items = reentrant_post  # type: ignore[method-assign]
    assert client.flush_pending()["success"] is True
    assert len(seen) == 1, f"the batch was sent {len(seen)} times"


def test_REG7_declining_keeps_the_queue_for_the_next_flush(httpx_mock):
    """Declining is correct; losing the work is not."""
    httpx_mock.add_response(url=TRANS, json=catalog(), is_reusable=True)
    client = make()
    client.translate("Phrase", category="UI", locale="en-us")
    assert client.has_pending, "control failed: nothing was queued to begin with"
    client._sending.acquire()
    try:
        result = client.flush_pending()
    finally:
        client._sending.release()
    assert result["reason"] == "send-in-flight"
    assert result["queued_phrases"] == 1
    assert client.has_pending
