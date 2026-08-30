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
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
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
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
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


# -- GATE-8: absence is a version signal, never permission --------------------


def test_GATE8_absent_flag_falls_back_to_key_type_for_the_plain_write_arm(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=None), is_reusable=True)
    assert make().can_write is True


@pytest.mark.parametrize("key_type", ["ip_write", "read"])
def test_GATE8_absent_flag_is_never_inferred_for_a_non_plain_write_key(httpx_mock, key_type):
    """For `ip_write` the decision is address-dependent, so the absence of a positive
    signal IS the answer. Inferring around it converts a closed gate into an open one."""
    httpx_mock.add_response(url=AUTH, json=auth(key_type, write_enabled=None), is_reusable=True)
    assert make().can_write is False


def test_GATE8_the_decision_is_re_evaluated_per_response_not_latched(httpx_mock):
    """A server upgraded mid-deployment must be picked up without an SDK release, and
    a latched decision strands the ip_write carve-out."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=False))
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True))
    client = make()
    assert client.can_write is False
    assert client.can_write is True, "the decision was latched"


def test_GATE1_a_failure_to_ask_is_not_permission(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=AUTH, is_reusable=True)
    assert make().can_write is False


# -- the ordering hazard: absence must mean absence, not "not looked yet" -----


def test_GATE8_a_live_false_is_honored_on_the_very_first_call(httpx_mock):
    """The inversion this guards against: reading the decision slot before authorize
    has populated it makes a live `write_enabled: false` look like *absence*, and the
    key_type fallback then answers true — a closed gate reported open.

    No warm state exists here, so this is the first call by construction."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=False), is_reusable=True)
    client = make()
    assert client._warm_authorize_payload() is None, "control failed: state was already warm"
    assert client.can_write is False


def test_GATE8_the_fallback_engages_only_when_the_payload_truly_lacked_the_field(httpx_mock):
    """Same key type, same call site; the only difference is whether the server sent
    the field. If both answer the same way, the flag is not being read at all."""
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=False))
    assert make().can_write is False, "a present false was ignored"

    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=None))
    assert make().can_write is True, "the fallback did not engage on genuine absence"


# -- warm cache: absence by construction (our own GATE-4 strip) ---------------


def _warm(cache, httpx_mock, key_type):
    """Authorize once so the stripped payload is cached, then drop the mock."""
    httpx_mock.add_response(url=AUTH, json=auth(key_type, write_enabled=True))
    make(cache=cache).authorize()


@pytest.mark.parametrize(
    ("key_type", "expected"),
    [("write", True), ("read", False)],
)
def test_GATE8_a_plain_key_falls_back_to_key_type_on_a_warm_cache(
    httpx_mock, key_type, expected
):
    """On a cache hit the payload lacks write_enabled by construction, because we
    stripped it. For plain keys the server guarantees `write_enabled == key_type`, so
    the fallback is sound and costs no round-trip."""
    cache = MemoryCache()
    _warm(cache, httpx_mock, key_type)
    # No further AUTH response is registered: needing one would raise here.
    assert make(cache=cache).can_write is expected


def test_GATE8_ip_write_pays_a_live_authorize_on_a_warm_cache(httpx_mock):
    """`ip_write` is address-dependent, so warm metadata cannot answer it and the
    fallback must never fire for it."""
    cache = MemoryCache()
    _warm(cache, httpx_mock, "ip_write")

    httpx_mock.add_response(url=AUTH, json=auth("ip_write", write_enabled=False))
    assert make(cache=cache).can_write is False

    httpx_mock.add_response(url=AUTH, json=auth("ip_write", write_enabled=True))
    assert make(cache=cache).can_write is True


def test_GATE8_ip_write_on_a_warm_cache_is_false_when_the_server_omits_the_field(
    httpx_mock,
):
    cache = MemoryCache()
    _warm(cache, httpx_mock, "ip_write")
    httpx_mock.add_response(url=AUTH, json=auth("ip_write", write_enabled=None))
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
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, status_code=500, json={"error": "boom"}, is_reusable=True)
    client = make()
    queue_one(client)
    result = client.flush_pending()
    assert result["success"] is False
    assert client.has_pending, "the queue was cleared although the server never accepted"


def test_GATE5_the_queue_clears_only_after_the_server_accepts(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
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
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
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
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {}}, is_reusable=True)
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
    cache = MemoryCache()
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {"a": "b"}}})
    client = make(cache=cache)
    client.get_translations("es-ES")
    # No second response registered: a second fetch would raise inside the store and
    # degrade to {}, so an equal result proves the cache key unified the casing.
    assert client.get_translations("es-es") == {"UI": {"a": "b"}}


def test_WIRE3_the_sentinel_is_never_sent_as_a_category(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {}}, is_reusable=True)
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
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {}}, is_reusable=True)
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
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
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
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=ITEMS, is_reusable=True)
    client = make()
    queue_one(client)
    result = client.flush_pending()  # must not raise
    assert result["success"] is False


# -- GRANT: affirmative non-participation -------------------------------------


def test_GRANT_no_write_grant_header_is_ever_sent(httpx_mock):
    """The server gate is `type-allows-write OR valid-grant`. This SDK holds a write
    key already and takes the first arm only.

    THIS TEST GUARDS A PRECONDITION, NOT A PREFERENCE. If this SDK ever sends a
    write grant, any short-circuit that assumes capability follows from key type
    alone becomes unsound and must be removed at the same time.
    """
    httpx_mock.add_response(url=AUTH, json=auth("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    client.authorize()
    client.get_translations("es-es")
    client.register_phrases(["Save"])

    requests = httpx_mock.get_requests()
    assert requests, "control failed: no requests were made"
    for request in requests:
        assert "x-write-grant" not in {k.lower() for k in request.headers}, (
            "this SDK now sends a write grant — the read-key/key-type short-circuit "
            "documented alongside GATE-1 is no longer sound and must be removed"
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


def test_PRECEDENCE_an_absent_envelope_flag_never_displaces_a_real_answer(httpx_mock):
    """An empty slot is not an answer. A response that simply carries no flag must not
    overwrite one that did — that is the 'stale-or-empty outranks live' shape."""
    cache = MemoryCache()
    client = _warm_plain_write(cache, httpx_mock)

    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": False, "data": {}}
    )
    client.get_translations("es-es")
    assert client.can_write is False

    # A catalog response with no write_enabled at all.
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {}}, is_reusable=True)
    client.get_translations("fr-fr")
    assert client.can_write is False, "an absent flag overwrote a real observed answer"


def test_PRECEDENCE_an_absent_flag_never_strips_a_real_yes(httpx_mock):
    """The mirror of the test above, and the direction that one cannot see.

    "Absent must not displace a real answer" has two ways of being unsatisfied, and the
    first test only covers one: its real answer is already `False`, so a mutation that
    records absence *as* `False` displaces nothing visible. This is the fail-CLOSED
    direction — a regression silently stripping write capability from a healthy
    session, which registers nothing and logs nothing.

    Built so the observed answer is the only thing holding the result up: the warm
    cache carries a READ key, so the key_type fallback would say False, and a valid
    write grant on a read key is exactly the shape GATE-1 exists for."""
    cache = MemoryCache()
    httpx_mock.add_response(url=AUTH, json=auth("read", write_enabled=False))
    client = make(cache=cache)
    client.authorize()

    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": True, "data": {}}
    )
    client.get_translations("es-es")
    assert client.can_write is True  # control: the observed yes stands over key_type

    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {}}, is_reusable=True)
    client.get_translations("fr-fr")
    assert client.can_write is True, "an absent flag stripped a real write capability"


# -- GATE-3: the request-boundary reset seam ---------------------------------


def test_GATE3_reset_write_decision_clears_an_observed_answer(httpx_mock):
    """A long-lived client outlives the request by construction, so the observed
    decision needs an explicit boundary. Without this seam a framework wrapper has no
    way to comply with GATE-3 at all."""
    cache = MemoryCache()
    httpx_mock.add_response(url=AUTH, json=auth("read", write_enabled=False))
    client = make(cache=cache)
    client.authorize()

    httpx_mock.add_response(
        url=TRANS, json={"status": True, "write_enabled": True, "data": {}}
    )
    client.get_translations("es-es")
    assert client.can_write is True  # control: an answer is being held

    client.reset_write_decision()
    # Nothing observed any more, so the warm read-key fallback decides — no round-trip.
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
