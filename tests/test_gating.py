"""GATE-1..8, REG-9/10, CACHE-1, WIRE-3 and the write-grant posture.

The discriminating vector for GATE-1 is a key whose ``key_type`` and ``write_enabled``
*disagree*. A test built from a plain write key that is also write-enabled passes
whether the SDK reads the flag or the key type, which is the whole defect.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache

API = "https://api.test/api"
AUTH = f"{API}/authorize-project/proj-1"
ITEMS = f"{API}/translatable-items"
TRANS = re.compile(r"https://api\.test/api/translations")


def make(cache=None, **kw):
    return LangsysClient(
        "k", "proj-1", api_url=API, cache=cache or MemoryCache(), base_locale="en-us",
        debounce=0, auto_flush=False, **kw
    )


def auth(key_type="write", write_enabled=None, batch_limit=200):
    data = {
        "id": "proj-1",
        "title": "T",
        "base_locale": "en-us",
        "target_locales": ["es-es"],
        "default_locales": {},
        "key_type": key_type,
        "langsys_settings": {"translatable_items": {"batch_limit": batch_limit}},
    }
    if write_enabled is not None:
        data["write_enabled"] = write_enabled
    return {"status": True, "data": data}


def items_bodies(httpx_mock):
    return [
        json.loads(r.content)
        for r in httpx_mock.get_requests()
        if r.url.path.endswith("translatable-items")
    ]


def queue_one(client):
    client.translate("Save", category="UI", locale="en-us")
    assert client.has_pending


# -- GATE-1: the flag wins over the key type ----------------------------------


def test_GATE1_a_write_key_that_is_not_write_enabled_does_not_register(httpx_mock):
    """The discriminating half. `key_type` says write; the server says no."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=False), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": False, "data": {"UI": {}}}, is_reusable=True)
    client = make()
    queue_one(client)
    result = client.flush_pending()
    assert items_bodies(httpx_mock) == [], "registered despite write_enabled=false"
    assert result["success"] is False


def test_GATE1_an_ip_write_key_that_is_write_enabled_does_register(httpx_mock):
    """The other half: a key type that is not `write` but whose session IS enabled.
    Our renderer runs the customer's page on exactly this shape, so a key_type test
    defeats discovery outright."""
    httpx_mock.add_response(url=AUTH, json=auth("ip_write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    queue_one(client)
    result = client.flush_pending()
    assert items_bodies(httpx_mock), "did not register although write_enabled=true"
    assert result["success"] is True


def test_GATE1_reads_the_flag_from_the_authorize_payload_not_the_envelope(httpx_mock):
    """Location differs by endpoint, and getting it wrong reads as false."""
    httpx_mock.add_response(url=AUTH, json=auth("read", write_enabled=True), is_reusable=True)
    client = make()
    assert client.can_write is True


# -- GATE-8: a missing write_enabled is not write-enabled ---------------------
#
# The API sends the field on every answer that carries the decision, so these run in-process
# (tier n/a (pure)): no clone can answer without it.


def _posts(httpx_mock):
    return [r for r in httpx_mock.get_requests() if r.method == "POST"]


def test_GATE8_an_authorization_without_the_flag_leaves_a_write_key_not_write_enabled(httpx_mock):
    """The key type says write and the catalog comes from the cache, so the authorization is the
    only answer, and it carries no flag: only reading that as `false` keeps this session from
    registering."""
    cache = MemoryCache()
    _warm(cache, httpx_mock, "write")
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=None))
    client = make(cache=cache)
    queue_one(client)
    result = client.flush_pending()
    assert result["reason"] == "not-write-enabled", result
    assert _posts(httpx_mock) == [], "registered on a missing write_enabled"


@pytest.mark.parametrize("key_type", ["write", "ip_write", "read"])
def test_GATE8_a_missing_flag_is_not_write_enabled_for_any_key_type(httpx_mock, key_type):
    httpx_mock.add_response(url=AUTH, json=auth(key_type, write_enabled=None), is_reusable=True)
    assert make().can_write is False


def test_GATE8_a_catalog_envelope_without_the_flag_is_an_answer_of_false(httpx_mock):
    """The other place the decision arrives. A newer catalog answer with no flag reads exactly as
    one saying false, so it withdraws an earlier yes; the authorization would answer yes again if
    asked, so a client that took the envelope for silence registers."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
    client = make()
    assert client.can_write is True  # control: the authorization's yes stands
    queue_one(client)  # fetches the catalog live: its envelope carries no flag
    result = client.flush_pending()
    assert result["reason"] == "not-write-enabled", result
    assert _posts(httpx_mock) == []


@pytest.mark.parametrize("flag", ["true", 1, "yes", None])
def test_GATE8_only_a_boolean_true_write_enables(httpx_mock, flag):
    """A flag present but not the boolean `true` grants nothing."""
    data = auth("read", write_enabled=None)
    data["data"]["write_enabled"] = flag
    httpx_mock.add_response(url=AUTH, json=data, is_reusable=True)
    assert make().can_write is False


def test_GATE8_a_present_true_write_enables_whatever_the_key_type(httpx_mock):
    """The positive control for the tests above: the same read key, the flag sent as true."""
    httpx_mock.add_response(url=AUTH, json=auth("read", write_enabled=True), is_reusable=True)
    assert make().can_write is True


def test_GATE1_a_failure_to_ask_is_not_permission(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=AUTH, is_reusable=True)
    assert make().can_write is False


def test_GATE8_a_live_false_is_honored_on_the_very_first_call(httpx_mock):
    """No warm state exists here, so this is the first call by construction."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=False), is_reusable=True)
    client = make()
    assert client._warm_authorize_payload() is None, "control failed: state was already warm"
    assert client.can_write is False


def test_GATE8_a_newer_answer_replaces_the_one_held(httpx_mock):
    """The session holds the API's newest answer, and a new answer replaces it."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=False))
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True))
    client = make()
    assert client.can_write is False
    client.authorize(force=True)
    assert client.can_write is True, "an older answer outranked a newer one"


def test_GATE8_an_authorization_without_the_flag_withdraws_a_yes_held_from_an_envelope(httpx_mock):
    """The authorization lane's absence, as a newer answer: the session held a yes from a catalog
    envelope, and the authorization it asks for next carries no flag."""
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {}})
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=None))
    client = make()
    client.get_translations("es-es")
    assert client.can_write is True  # control: the envelope's yes is held
    client.authorize(force=True)
    assert client.can_write is False, "a missing flag on a newer authorization was taken as silence"


# -- a cached payload is not an answer (GATE-4 strips the flag before caching) --


def _warm(cache, httpx_mock, key_type):
    """One session authorizes and fetches its catalog, so both are cached; then the mocks go."""
    httpx_mock.add_response(url=AUTH, json=auth(key_type, write_enabled=True))
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}})
    client = make(cache=cache)
    client.authorize()
    client.get_translations("en-us")


@pytest.mark.parametrize(
    ("key_type", "answer"),
    [("write", False), ("read", True), ("ip_write", False), ("ip_write", True)],
)
def test_GATE8_a_warm_cache_decides_nothing_the_api_is_asked(httpx_mock, key_type, answer):
    """The cached metadata names the key type; the live answer disagrees with it where it can, so
    a decision taken from the cache comes out wrong."""
    cache = MemoryCache()
    _warm(cache, httpx_mock, key_type)
    httpx_mock.add_response(url=AUTH, json=auth(key_type, write_enabled=answer))
    assert make(cache=cache).can_write is answer


def test_GATE8_a_session_holding_only_cached_payloads_registers_once_the_api_says_yes(httpx_mock):
    """A cached write key decides nothing: while the API cannot answer, the miss is held (GATE-2),
    and it registers once the API answers true."""
    cache = MemoryCache()
    _warm(cache, httpx_mock, "write")
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=AUTH)
    client = make(cache=cache)
    queue_one(client)  # the catalog is the cached one: no answer arrives with it
    held = client.flush_pending()
    assert held["reason"] == "capability-unknown", held
    assert _posts(httpx_mock) == [], "the cached key type decided"
    assert client.has_pending, "the miss was not held"

    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True))
    httpx_mock.add_response(url=ITEMS, json={"status": True})
    assert client.flush_pending(force=True)["success"] is True
    assert len(items_bodies(httpx_mock)) == 1


def test_GATE8_a_warm_cache_answers_false_when_the_api_omits_the_field(httpx_mock):
    cache = MemoryCache()
    _warm(cache, httpx_mock, "write")
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=None))
    assert make(cache=cache).can_write is False


# -- GATE-3 / GATE-4: the decision never reaches a cache ----------------------


def test_GATE4_write_enabled_is_stripped_before_anything_is_cached(httpx_mock):
    """The hazard is any store that is process-external or shared by default. One
    request from an allow-listed address would otherwise write-enable every anonymous
    visitor on the host for the TTL, and the fleet on a shared Redis."""
    cache = MemoryCache()
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    client = make(cache=cache)
    client.authorize()

    cached = cache.get("auth_proj-1")
    assert cached is not None, "control failed: nothing was cached at all"
    assert "write_enabled" not in cached
    assert cached["key_type"] == "write", "key_type is a property of the key and may cache"


def test_GATE3_the_decision_is_not_latched_in_memory_either(httpx_mock):
    """`Project` is held for the life of the client, so a flag riding along in `raw`
    would latch the decision just as surely as caching it would."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    client = make()
    project = client.authorize()
    assert project.raw, "control failed: raw payload is empty"
    assert "write_enabled" not in project.raw


def test_GATE3_an_address_dependent_decision_is_never_inherited_from_a_warm_store(
    httpx_mock,
):
    """The case where staleness actually costs something: `ip_write` answers
    differently from different addresses, so a second client sharing the store must
    ask again rather than inherit the first client's answer.

    (For plain read/write keys the fleet ruling permits the warm-cache key_type
    fallback instead, because the server guarantees `write_enabled == key_type` for
    them — so there is no stale decision to inherit.)"""
    cache = MemoryCache()
    httpx_mock.add_response(url=AUTH, json=auth("ip_write", write_enabled=True))
    make(cache=cache).authorize()

    httpx_mock.add_response(url=AUTH, json=auth("ip_write", write_enabled=False))
    assert make(cache=cache).can_write is False, "inherited a stale address-dependent yes"


# -- GATE-5: bookkeeping records acceptance, never attempts -------------------


def test_GATE5_a_failed_registration_keeps_the_queue(httpx_mock):
    """The most important rule in the spec. A timeout or a 422 must not write a false
    'already registered' record — the phrases would stay suppressed and nothing logs."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, status_code=500, json={"error": "boom"}, is_reusable=True)
    client = make()
    queue_one(client)
    result = client.flush_pending()
    assert result["success"] is False
    assert client.has_pending, "the queue was cleared although the server never accepted"


def test_GATE5_the_queue_clears_only_after_the_server_accepts(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    queue_one(client)
    assert client.flush_pending()["success"] is True
    assert not client.has_pending


def test_GATE5_no_persistent_registered_marker_is_written(httpx_mock):
    """This SDK has no 'already registered' store at all, which is what makes the
    poisoned-bookkeeping failure structurally unreachable. Pinned so a future cache
    cannot reintroduce it silently."""
    cache = MemoryCache()
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make(cache=cache)
    queue_one(client)
    client.flush_pending()
    assert not any("registered" in k for k in cache._store), (
        f"a registration marker was persisted: {list(cache._store)}"
    )


# -- CACHE-1 ------------------------------------------------------------------


def test_CACHE1_every_key_is_namespaced_by_project(httpx_mock):
    cache = MemoryCache()
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {}}, is_reusable=True)
    client = make(cache=cache)
    client.authorize()
    client.get_translations("es-es")
    assert cache._store, "control failed: nothing cached"
    for key in cache._store:
        assert "proj-1" in key, f"unnamespaced cache key: {key}"


def test_CACHE1_the_catalog_key_carries_the_locale(httpx_mock):
    cache = MemoryCache()
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {}}, is_reusable=True)
    client = make(cache=cache)
    client.get_translations("es-es")
    client.get_translations("fr-fr")
    assert any("es-es" in k for k in cache._store)
    assert any("fr-fr" in k for k in cache._store)


# -- WIRE-3 -------------------------------------------------------------------


def test_WIRE3_the_locale_goes_on_the_wire_lowercase(httpx_mock):
    """The deprecated translations route sits outside the normalising middleware, so
    relying on the server to fold case is relying on a normalisation we do not control
    — on the one route whose whole purpose is serving older SDKs."""
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {}}, is_reusable=True)
    client = make()
    client.get_translations("es-ES")
    request = next(r for r in httpx_mock.get_requests() if "translations" in str(r.url))
    assert "locale=es-es" in str(request.url), str(request.url)
    assert "es-ES" not in str(request.url)


def test_WIRE3_casing_variants_are_one_cache_entry_not_two(httpx_mock):
    """Asserted on the fetches made and the keys stored. An earlier version relied on "no second
    response is registered, so a second fetch would raise" - which does not hold on this
    pytest-httpx, so it stayed green with both cache sites keyed by the raw casing."""
    cache = MemoryCache()
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "data": {"UI": {"a": "b"}}}, is_reusable=True
    )
    client = make(cache=cache)
    client.get_translations("es-ES")
    assert client.get_translations("es-es") == {"UI": {"a": "b"}}
    fetches = [str(r.url) for r in httpx_mock.get_requests() if "translations" in str(r.url)]
    assert len(fetches) == 1, f"casing variants were fetched as two catalogs: {fetches}"
    stored = sorted(k for k in cache._store if k.startswith("translations_"))
    assert stored == ["translations_proj-1_es-es"], stored


def test_WIRE3_the_sentinel_is_never_sent_as_a_category(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    client.translate("Save", locale="en-us")  # no category
    client.flush_pending()
    body = items_bodies(httpx_mock)[0]
    assert "__uncategorized__" not in json.dumps(body)


# -- REG-9 / REG-10 -----------------------------------------------------------


def test_REG9_content_blocks_are_batched_into_one_post(httpx_mock):
    """One POST per block turns a first render of a 40-block page into 40 sequential
    blocking requests while the visitor waits."""
    pytest.importorskip("lxml")
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    for i in range(5):
        client.translate_content_block(f"<div><p>Block {i}</p><p>Second {i}</p></div>", "CAT")
    assert len(client.pending_content_blocks) == 5
    client.flush_pending()

    block_posts = [
        b
        for b in items_bodies(httpx_mock)
        if any(i["type"] == "content_block" for i in b["translatable_items"])
    ]
    assert len(block_posts) == 1, f"expected one batched POST, got {len(block_posts)}"
    assert len(block_posts[0]["translatable_items"]) == 5


def test_REG9_the_batch_limit_comes_from_the_server(httpx_mock):
    httpx_mock.add_response(
        url=AUTH, json=auth("write", write_enabled=True, batch_limit=2), is_reusable=True
    )
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    client.register_phrases([f"p{i}" for i in range(5)])
    sizes = [len(b["translatable_items"]) for b in items_bodies(httpx_mock)]
    assert sizes == [2, 2, 1], sizes


def test_REG10_a_skipped_write_is_not_reported_as_success(httpx_mock):
    """A caller that correctly checks the return value must not be told it worked."""
    httpx_mock.add_response(url=AUTH, json=auth("read", write_enabled=False), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": False, "data": {"UI": {}}}, is_reusable=True)
    client = make()
    queue_one(client)
    result = client.flush_pending()
    assert result["success"] is False
    assert result["skipped"] is True
    assert result["discarded_phrases"] == 1


def test_REG10_nothing_pending_is_an_honest_success(httpx_mock):
    assert make().flush_pending() == {"phrases": 0, "content_blocks": 0, "success": True}


def test_REG10_flush_does_not_throw_when_registration_fails(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=ITEMS, is_reusable=True)
    client = make()
    queue_one(client)
    result = client.flush_pending()  # must not raise
    assert result["success"] is False


# -- GRANT: affirmative non-participation -------------------------------------


def test_GRANT_no_write_grant_header_is_ever_sent(httpx_mock):
    """The server gate is `type-allows-write OR valid-grant`. This SDK holds a write
    key already and takes the first arm only.

    THIS TEST GUARDS A PRECONDITION, NOT A PREFERENCE. With no grant, a session's
    capability depends on the key and the caller's address only. A grant makes it per
    user, and every place that holds a session's answer must then be checked against
    GATE-3 again.
    """
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    client.authorize()
    client.get_translations("es-es")
    client.register_phrases(["Save"])

    requests = httpx_mock.get_requests()
    assert requests, "control failed: no requests were made"
    for request in requests:
        assert "x-write-grant" not in {k.lower() for k in request.headers}, (
            "this SDK now sends a write grant: capability is per user, so every place "
            "that holds a session's answer must be checked against GATE-3 again"
        )


# -- precedence between the two decision sources: recency, never source -------
#
# The latch-shaped failure this guards: a stale or empty slot outranking a live
# answer, and failing open when it does. Both directions are asserted, because a
# fixed precedence passes whichever single direction happens to match it.


def _warm_plain_write(cache, httpx_mock):
    """Warm the cache with a plain write key, so the key_type fallback is what the
    observed flag has to beat."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True))
    client = make(cache=cache)
    client.authorize()
    return client


def test_PRECEDENCE_a_fresher_envelope_flag_beats_an_older_authorize_answer(httpx_mock):
    """Shadow direction 1: the catalog envelope spoke last, so it wins — even though
    the warm key_type fallback and the earlier authorize both said write."""
    cache = MemoryCache()
    client = _warm_plain_write(cache, httpx_mock)
    assert client.can_write is True  # control: the earlier answer stands

    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": False, "data": {}}, is_reusable=True
    )
    client.get_translations("es-es")
    assert client.can_write is False, "an older authorize answer outranked a fresher envelope"


def test_PRECEDENCE_a_fresher_authorize_answer_beats_an_older_envelope_flag(httpx_mock):
    """Shadow direction 2: same two sources, opposite order, opposite winner. This is
    the direction Ruby's fixed source-precedence failed — the envelope latched and
    outranked every later authorize."""
    cache = MemoryCache()
    client = _warm_plain_write(cache, httpx_mock)

    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": False, "data": {}}, is_reusable=True
    )
    client.get_translations("es-es")
    assert client.can_write is False  # control: the envelope answer stands

    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True))
    client.authorize(force=True)
    assert client.can_write is True, "a stale envelope flag outranked a fresher authorize"


@pytest.mark.parametrize("answer", [True, False])
def test_PRECEDENCE_a_cached_catalog_read_never_displaces_a_real_answer(httpx_mock, answer):
    """A catalog served from the cache carries no answer, in either direction: it neither
    withdraws a yes nor grants over a no. The key is a read key answered yes, so the key type
    would say no; and a write key answered no, so it would say yes."""
    key_type = "read" if answer else "write"
    httpx_mock.add_response(url=AUTH, json=auth(key_type, write_enabled=not answer))
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": answer, "data": {}}
    )
    client = make()
    client.authorize()
    client.get_translations("es-es")
    assert client.can_write is answer  # control: the envelope's newer answer stands

    client.get_translations("es-es")  # held in memory: no request, no answer
    assert client.can_write is answer, "a cache read displaced the API's answer"


# -- GATE-3: the request-boundary reset seam ---------------------------------


def test_GATE3_reset_write_decision_clears_an_observed_answer(httpx_mock):
    """A long-lived client outlives the request by construction, so the observed
    decision needs an explicit boundary. Without this seam a framework wrapper has no
    way to comply with GATE-3 at all."""
    httpx_mock.add_response(url=AUTH, json=auth("read", write_enabled=False))
    client = make()
    client.authorize()

    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": True, "data": {}}
    )
    client.get_translations("es-es")
    assert client.can_write is True  # control: an answer is being held

    client.reset_write_decision()
    # Nothing held any more, so the API is asked again, and its answer decides.
    httpx_mock.add_response(url=AUTH, json=auth("read", write_enabled=False))
    assert client.can_write is False, "the decision survived the request boundary"


def test_GATE3_reset_does_not_disturb_cached_project_metadata(httpx_mock):
    """`key_type` is a property of the key and may be cached; only the decision resets.
    A reset that dropped metadata would turn every request boundary into a round-trip."""
    cache = MemoryCache()
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True))
    client = make(cache=cache)
    client.authorize()
    client.reset_write_decision()
    # No further AUTH response is registered: needing one would raise here.
    assert client.project.key_type == "write"
    assert client.project.batch_limit == 200


def test_GATE3_reset_rearms_the_obs1_notice(httpx_mock):
    """OBS-1 is once per process for a stable misconfiguration, but a reset boundary
    starts a new session — the operator should hear about it again."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=False), is_reusable=True)
    client = make()
    assert client._warned_unusable is False
    assert client.can_write is False  # resolving is what arms the notice
    assert client._warned_unusable is True
    client.reset_write_decision()
    assert client._warned_unusable is False


# -- OBS-1: the diagnostic itself, not the flag behind it ---------------------


def _unusable_notices(caplog):
    return [
        r for r in caplog.records
        if r.levelname == "WARNING" and "NOT write-enabled although" in r.getMessage()
    ]


def test_OBS1_an_unusable_capability_is_surfaced_once_not_per_miss(httpx_mock, caplog):
    """REG-1 makes a misconfigured integration completely silent - no request, no error,
    nothing in the catalog - and a server has no network tab either. One line is the only
    signal. Asserted on the emitted record rather than on the flag guarding it: a flag can be
    set by code that never logs."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=False), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": False, "data": {"UI": {}}}, is_reusable=True)
    client = make()
    with caplog.at_level("WARNING", logger="langsys"):
        for phrase in ("One", "Two", "Three"):
            client.translate(phrase, category="UI", locale="en-us")
            client.flush_pending()
    assert len(_unusable_notices(caplog)) == 1, [r.getMessage() for r in caplog.records]


def test_OBS1_control_a_write_enabled_session_is_not_reported_unusable(httpx_mock, caplog):
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    with caplog.at_level("WARNING", logger="langsys"):
        client.translate("One", category="UI", locale="en-us")
        assert client.flush_pending()["success"] is True
    assert _unusable_notices(caplog) == []


# -- REG-9 against a double that can refuse -----------------------------------


def test_REG9_a_double_that_refuses_oversized_batches_ends_up_holding_every_item(httpx_mock):
    """The acceptance-shaped REG-9 test (CONF-1). The double enforces the batch limit the way
    the server does - an oversized batch is refused - and keeps what it accepted, so the
    assertion is on what the server ends up holding, not on the shape of what was sent. A
    request-shape test that happens to use the hardcoded number passes; this does not."""
    limit = 2
    accepted: list[str] = []

    def translatable_items(request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)["translatable_items"]
        if len(items) > limit:
            return httpx.Response(422, json={"error": f"batch of {len(items)} exceeds {limit}"})
        accepted.extend(item["phrase"] for item in items)
        return httpx.Response(200, json={"status": True})

    httpx_mock.add_response(
        url=AUTH, json=auth("write", write_enabled=True, batch_limit=limit), is_reusable=True
    )
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_callback(translatable_items, url=ITEMS, is_reusable=True)
    client = make()
    phrases = [f"Phrase {i}" for i in range(5)]
    for phrase in phrases:
        client.translate(phrase, category="UI", locale="en-us")

    result = client.flush_pending()
    assert result["success"] is True, result
    assert sorted(accepted) == sorted(phrases), "the server does not hold every discovered phrase"
    assert not client.has_pending
