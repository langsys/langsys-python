"""REG-7: a content block has one registration request in flight, keyed by its credentials, category
and id. A registration for a block whose request is unanswered resolves with that request's result
and sends nothing; only a refused or failed answer lets a later registration send it again; and a
registration under other credentials is not held back by it.

Each test holds the first request open on a gate, so the second registration provably arrives while
it is in flight, and counts the requests the transport received.
"""

from __future__ import annotations

import json
import re
import threading

import httpx
import pytest

from langsys import ApiError, LangsysClient
from langsys.cache import MemoryCache
from langsys.registration import generate_custom_id

API = "https://api.test/api"
ITEMS = f"{API}/translatable-items"
TRANS = re.compile(r"https://api\.test/api/translations.*")
BLOCK = "<div><p>Block one</p><p>Block two</p></div>"
PHRASES = ["Block one", "Block two"]
BLOCK_ID = generate_custom_id("UI", PHRASES)


def auth_for(project):
    return {"status": True, "data": {"id": project, "title": "T", "base_locale": "en-us",
                                     "target_locales": ["it-it"], "default_locales": {},
                                     "key_type": "write", "write_enabled": True,
                                     "langsys_settings": {"translatable_items": {"batch_limit": 200}}}}


def make(key="k", project="proj-1"):
    return LangsysClient(key, project, api_url=API, cache=MemoryCache(), base_locale="en-us",
                         debounce=0, auto_flush=False)


class Gate:
    """Answers translatable-items once released; counts what arrived, by key."""

    def __init__(self, httpx_mock, status=200):
        self.arrived = threading.Event()
        self.release = threading.Event()
        self.status = status
        self.posts: list[httpx.Request] = []
        httpx_mock.add_callback(self, url=ITEMS, is_reusable=True)

    def __call__(self, request):
        self.posts.append(request)
        self.arrived.set()
        self.release.wait(3)
        if self.status != 200:
            return httpx.Response(self.status, json={"status": False, "error": {"message": "no"}})
        return httpx.Response(200, json={"status": True, "data": [{"status": True}]})

    def blocks(self):
        return [item["custom_id"] for r in self.posts for item in json.loads(r.content)["translatable_items"]
                if item.get("type") == "content_block"]


def _authorize(httpx_mock, *projects):
    for project in projects:
        httpx_mock.add_response(url=f"{API}/authorize-project/{project}", json=auth_for(project),
                                is_reusable=True)


def _in_thread(fn):
    out: dict = {}

    def run():
        try:
            out["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            out["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


def test_REG7_two_direct_registrations_share_one_request_and_both_report_its_result(httpx_mock):
    _authorize(httpx_mock, "proj-1")
    gate = Gate(httpx_mock)
    first, second = make(), make()
    t1, r1 = _in_thread(lambda: first.register_content_block(BLOCK, PHRASES, category="UI"))
    assert gate.arrived.wait(3)
    t2, r2 = _in_thread(lambda: second.register_content_block(BLOCK, PHRASES, category="UI"))
    t2.join(0.3)
    assert t2.is_alive(), "the second registration did not wait for the request in flight"
    gate.release.set()
    t1.join(3)
    t2.join(3)
    assert gate.blocks() == [BLOCK_ID], gate.blocks()
    assert r1["result"]["status"] is True
    assert r2["result"] == r1["result"], "the waiter did not report the request's result"


@pytest.mark.parametrize("other", [("k2", "proj-2"), ("k2", "proj-1")],
                         ids=["another-project", "another-key-on-the-project"])
def test_REG7_other_credentials_are_not_held_back_and_each_project_holds_the_block(httpx_mock, other):
    _authorize(httpx_mock, *sorted({"proj-1", other[1]}))
    gate = Gate(httpx_mock)
    t1, r1 = _in_thread(lambda: make().register_content_block(BLOCK, PHRASES, category="UI"))
    assert gate.arrived.wait(3)
    t2, r2 = _in_thread(lambda: make(*other).register_content_block(BLOCK, PHRASES, category="UI"))
    t2.join(0.5)
    assert len(gate.posts) == 2, "a registration under other credentials was held back"
    gate.release.set()
    t1.join(3)
    t2.join(3)
    sent = {(r.headers["X-Authorization"], json.loads(r.content)["project_id"]) for r in gate.posts}
    assert sent == {("k", "proj-1"), other}
    assert gate.blocks() == [BLOCK_ID, BLOCK_ID]
    assert "error" not in r1 and "error" not in r2


def test_REG7_one_id_under_two_categories_is_two_blocks(httpx_mock):
    _authorize(httpx_mock, "proj-1")
    gate = Gate(httpx_mock)
    t1, _ = _in_thread(lambda: make().register_content_block(BLOCK, PHRASES, category="UI",
                                                             custom_id="hero"))
    assert gate.arrived.wait(3)
    t2, _ = _in_thread(lambda: make().register_content_block(BLOCK, PHRASES, category="Footer",
                                                             custom_id="hero"))
    t2.join(0.5)
    assert len(gate.posts) == 2, "a block under another category was held back"
    gate.release.set()
    t1.join(3)
    t2.join(3)


def test_REG7_a_refused_request_is_reported_to_its_waiter_and_a_later_registration_sends_again(httpx_mock):
    _authorize(httpx_mock, "proj-1")
    gate = Gate(httpx_mock, status=500)
    t1, r1 = _in_thread(lambda: make().register_content_block(BLOCK, PHRASES, category="UI"))
    assert gate.arrived.wait(3)
    t2, r2 = _in_thread(lambda: make().register_content_block(BLOCK, PHRASES, category="UI"))
    t2.join(0.3)
    gate.release.set()
    t1.join(3)
    t2.join(3)
    assert isinstance(r1.get("error"), ApiError) and isinstance(r2.get("error"), ApiError)
    assert len(gate.posts) == 1, "the waiter sent its own request"

    gate.status = 200
    assert make().register_content_block(BLOCK, PHRASES, category="UI")["status"] is True
    assert len(gate.posts) == 2, "a refused request kept the block from being sent again"


def test_REG7_a_flush_joins_a_block_a_direct_registration_has_in_flight(httpx_mock):
    _authorize(httpx_mock, "proj-1")
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}},
                            is_reusable=True)
    gate = Gate(httpx_mock)
    t1, r1 = _in_thread(lambda: make().register_content_block(BLOCK, PHRASES, category="UI"))
    assert gate.arrived.wait(3)
    flusher = make()
    flusher.set_locale("it-it")
    flusher.translate_content_block(BLOCK, category="UI")
    assert flusher.has_pending
    t2, r2 = _in_thread(lambda: flusher.flush_pending())
    t2.join(0.3)
    gate.release.set()
    t1.join(3)
    t2.join(3)
    assert gate.blocks() == [BLOCK_ID], "the flush sent a block already in flight"
    assert r2["result"]["success"] is True and flusher.has_pending is False


def test_REG7_a_flush_that_joined_a_refused_request_reports_it_and_keeps_its_block(httpx_mock):
    _authorize(httpx_mock, "proj-1")
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}},
                            is_reusable=True)
    gate = Gate(httpx_mock, status=500)
    t1, _ = _in_thread(lambda: make().register_content_block(BLOCK, PHRASES, category="UI"))
    assert gate.arrived.wait(3)
    flusher = make()
    flusher.set_locale("it-it")
    flusher.translate_content_block(BLOCK, category="UI")
    t2, r2 = _in_thread(lambda: flusher.flush_pending())
    t2.join(0.3)
    gate.release.set()
    t1.join(3)
    t2.join(3)
    assert len(gate.posts) == 1
    assert r2["result"]["success"] is False and r2["result"]["reason"] == "registration-failed"
    assert flusher.has_pending, "the block was dropped although its registration was refused"


def test_REG7_a_block_rendered_again_during_its_flush_reaches_the_api_once(httpx_mock):
    _authorize(httpx_mock, "proj-1")
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}},
                            is_reusable=True)
    gate = Gate(httpx_mock)
    client = make()
    client.set_locale("it-it")
    client.translate_content_block(BLOCK, category="UI")
    t1, r1 = _in_thread(lambda: client.flush_pending())
    assert gate.arrived.wait(3)
    for _ in range(3):
        client.translate_content_block(BLOCK, category="UI")
    gate.release.set()
    t1.join(3)
    assert r1["result"]["success"] is True
    client.flush_pending()
    assert gate.blocks() == [BLOCK_ID]


@pytest.fixture(autouse=True)
def _no_flight_left_behind():
    yield
    from langsys import registration

    assert not registration._IN_FLIGHT, "a registration left its block in flight"
