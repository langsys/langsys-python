"""Rows graded `contract` (spec CONF-2): run against the shared contract double.

Every assertion is on a status the SDK reports or on state read back from the double, never on
what the SDK sent: the double keeps no request log. Where a row turns on the SDK holding back,
the absence is only evidence if the double would have ACCEPTED the action, so those tests drift
the capability after the SDK has learned it must not act, and carry a control acting in the
drifted world.
"""

from __future__ import annotations

import hashlib
import logging

import pytest
from contract import (
    FIXTURE_DIR,
    FIXTURE_TREE,
    IP_WRITE_KEY,
    PROJECT,
    READ_KEY,
    WRITE_KEY,
    ContractDouble,
    world,
)

from langsys import LangsysClient
from langsys.cache import MemoryCache

pytest.importorskip("lxml")

AUTHORIZE = f"/authorize-project/{PROJECT}"
LOCAL = ["127.0.0.1"]


def client(double: ContractDouble, key: str = WRITE_KEY, **kw) -> LangsysClient:
    return LangsysClient(
        key, PROJECT, api_url=double.base_url, cache=MemoryCache(), base_locale="en-us",
        debounce=0, auto_flush=False, **kw,
    )


def miss(c: LangsysClient, phrase: str = "Checkout", category: str = "UI") -> None:
    c.translate(phrase, category=category, locale="it-it")
    assert c.has_pending, "control: the miss was not recorded"


def test_the_vendored_double_is_the_pinned_tree():
    """THE CHECK: git's tree id for the directory, computed here with no git and no network."""
    entries = b""
    for path in sorted(FIXTURE_DIR.iterdir(), key=lambda p: p.name):
        data = path.read_bytes()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).digest()  # noqa: S324
        entries += b"100644 " + path.name.encode() + b"\0" + blob
    tree = hashlib.sha1(b"tree %d\0" % len(entries) + entries).hexdigest()  # noqa: S324
    assert tree == FIXTURE_TREE, "re-vendor contract-fixture/ deliberately; never edit it"


# -- GATE-1 / GATE-8: the decision is the server's ------------------------------------------


def test_GATE1_an_allow_listed_ip_write_session_registers(double):
    """The discriminating presence: key_type says not-write, the server computes yes. An SDK
    deciding by key type registers nothing here."""
    double.seed(world(ip_allowlist=LOCAL))
    c = client(double, IP_WRITE_KEY)
    miss(c)
    assert c.flush_pending()["success"] is True
    assert double.phrases() == [("UI", "Checkout")]


def test_GATE1_a_session_told_no_sends_nothing_even_once_the_world_would_accept(double):
    """DRIFT. Learned no -> the allow-list widens -> a later flush still leaves no state. An SDK
    that ignored the answer would have been refused, kept the phrase, and landed it now."""
    double.seed(world())
    c = client(double, IP_WRITE_KEY)
    miss(c)
    assert c.flush_pending()["reason"] == "not-write-enabled"
    double.seed(world(ip_allowlist=LOCAL))
    c.flush_pending(force=True)
    assert double.phrases() == []

    control = client(double, IP_WRITE_KEY)  # acting in the drifted world
    miss(control)
    assert control.flush_pending()["success"] is True
    assert double.phrases() == [("UI", "Checkout")]


def test_GATE8_a_legacy_server_falls_back_to_key_type_for_the_plain_write_arm(double):
    double.seed(world(legacy=True))
    c = client(double, WRITE_KEY)
    miss(c)
    assert c.flush_pending()["success"] is True
    assert double.phrases() == [("UI", "Checkout")]


def test_GATE8_absence_is_never_permission_for_ip_write(double):
    """The double WOULD accept (allow-listed), so the empty state is evidence."""
    double.seed(world(legacy=True, ip_allowlist=LOCAL))
    c = client(double, IP_WRITE_KEY)
    miss(c)
    assert c.flush_pending()["success"] is False
    assert double.phrases() == []


# -- GATE-2 / GATE-5 / REG-8: failures keep the work until it lands -------------------------


def test_GATE2_an_unknown_answer_holds_the_queue_until_the_server_can_say_yes(double):
    double.seed(world(faults=[{"method": "GET", "path": AUTHORIZE, "status": 500}]))
    c = client(double)
    miss(c)
    assert c.flush_pending()["reason"] == "capability-unknown"
    assert double.phrases() == []
    assert c.flush_pending(force=True)["success"] is True
    assert double.phrases() == [("UI", "Checkout")], "the held phrase never landed"


def test_GATE5_a_refused_send_is_not_recorded_as_done_and_lands_on_the_second_read(double):
    double.seed(world(faults=[{"method": "POST", "path": "/translatable-items", "status": 500}]))
    c = client(double)
    miss(c)
    assert c.flush_pending()["success"] is False
    assert double.phrases() == []
    assert c.has_pending, "the refused phrase was marked done"
    assert c.flush_pending(force=True)["success"] is True
    assert double.phrases() == [("UI", "Checkout")]


def test_REG8_nothing_is_sent_while_backing_off_though_the_server_would_accept(double):
    """The fault is consumed by the first send, so a second send inside the backoff WOULD be
    accepted: an empty state here means it was not made."""
    double.seed(world(faults=[{"method": "POST", "path": "/translatable-items", "status": 500}]))
    c = client(double)
    miss(c)
    c.flush_pending()
    assert c.flush_pending()["reason"] == "backoff"
    assert double.phrases() == []
    assert c.flush_pending(force=True)["success"] is True  # control: the server would accept
    assert double.phrases() == [("UI", "Checkout")]


# -- GATE-7: every detecting path feeds the register lane -----------------------------------


def test_GATE7_every_entry_point_lands_its_misses(double):
    double.seed(world())
    c = client(double)
    c.translate("Loose phrase", category="UI", locale="it-it")
    c.translate_content_block("<div><p>Block one</p><p>Block two</p></div>", category="UI")
    c.set_locale("it-it")
    c.translate_page("<html><body><p>Page phrase</p></body></html>", category="UI")
    assert c.flush_pending()["success"] is True
    phrases = {p for _, p in double.phrases()}
    assert {"Loose phrase", "Page phrase"} <= phrases, phrases
    blocks = [[p["phrase"] for p in b["phrases"]] for b in double.blocks()]
    assert blocks == [["Block one", "Block two"]], blocks


# -- REG-9 / REG-10 -------------------------------------------------------------------------


def test_REG9_the_double_enforces_the_limit_and_holds_every_item(double):
    double.seed(world(batch_limit=2))
    c = client(double)
    names = [f"Phrase {i}" for i in range(5)]
    for name in names:
        miss(c, name)
    assert c.flush_pending()["success"] is True
    assert [p for _, p in double.phrases()] == sorted(names)


def test_REG10_a_read_only_flush_is_reported_as_failure(double):
    double.seed(world())
    c = client(double, READ_KEY)
    miss(c)
    result = c.flush_pending()
    assert result["success"] is False and result["skipped"] is True
    assert double.phrases() == []

    control = client(double, WRITE_KEY)
    miss(control)
    assert control.flush_pending()["success"] is True


# -- WIRE-2 / WIRE-4 ------------------------------------------------------------------------


def test_WIRE2_an_empty_204_is_success(double):
    double.seed(world(faults=[{"method": "POST", "path": "/translatable-items", "status": 204}]))
    c = client(double)
    miss(c)
    assert c.flush_pending()["success"] is True
    assert not c.has_pending


@pytest.mark.parametrize("fault", [{"drop": True}, {"status": 500}], ids=["dropped", "http-500"])
def test_WIRE4_a_failed_catalog_degrades_and_queues_nothing(double, fault):
    double.seed(world(
        phrases=[{"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Cassa"}}],
        faults=[{"method": "GET", "path": "/translations", **fault}],
    ))
    c = client(double)
    assert c.translate("Unknown phrase", category="UI", locale="it-it") == "Unknown phrase"
    assert not c.has_pending
    c.flush_pending(force=True)
    assert double.phrases() == [("UI", "Checkout")], "a failed fetch registered something"

    control = client(double)
    assert control.translate("Checkout", category="UI", locale="it-it") == "Cassa"


# -- OBS-1 ----------------------------------------------------------------------------------


def _unusable(caplog):
    return [r for r in caplog.records if "NOT write-enabled although" in r.getMessage()]


def test_OBS1_an_unusable_capability_is_surfaced_once(double, caplog):
    double.seed(world())
    c = client(double, IP_WRITE_KEY)
    with caplog.at_level(logging.WARNING, logger="langsys"):
        for name in ("One", "Two", "Three"):
            miss(c, name)
            c.flush_pending(force=True)
    assert len(_unusable(caplog)) == 1


def test_OBS1_control_an_allow_listed_session_is_not_reported(double, caplog):
    double.seed(world(ip_allowlist=LOCAL))
    c = client(double, IP_WRITE_KEY)
    with caplog.at_level(logging.WARNING, logger="langsys"):
        miss(c)
        c.flush_pending()
    assert _unusable(caplog) == []


def test_WIRE3_an_uncategorised_block_reads_back_under_the_sentinel_and_is_found(double):
    """The API stores an uncategorised block and serves it under `__uncategorized__`, as it does
    uncategorised phrases; a second render must find it there instead of registering it again."""
    double.seed(world())
    html = "<div><p>First sentence</p><p>Second sentence</p></div>"
    first = client(double)
    first.translate_content_block(html)
    assert first.flush_pending()["success"] is True
    [block] = double.blocks()
    assert block["category"] is None

    second = client(double)
    second.translate_content_block(html)
    assert second.pending_content_blocks == [], "the stored block was not found under the sentinel"
