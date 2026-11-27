"""The write decision on the SDK test clone (spec CONF-2): GATE-1, GATE-2, GATE-5, REG-8, OBS-1.

Every assertion is on a status the SDK reports or on what the API holds afterwards, read back
through its own routes. Where a row turns on the SDK holding back, the absence is only evidence if
the API would have ACCEPTED the action, so those tests drift the capability after the SDK has
learned it must not act (the case moves to an address the key's allow-list holds), and carry a
control acting in the drifted world.
"""

from __future__ import annotations

import logging

import pytest
from clone import World
from clone import held_phrases as phrases
from clone import sdk_client as client

from langsys.cache import MemoryCache

pytest.importorskip("lxml")
pytestmark = pytest.mark.contract


def miss(c, phrase: str = "Checkout", category: str = "UI") -> None:
    c.translate(phrase, category=category, locale="it-it")
    assert c.has_pending, "control: the miss was not recorded"


SEED = "write_decision"


def authorize(world: World) -> str:
    return f"/authorize-project/{world.project()}"


# -- GATE-1: the decision is the server's ------------------------------------------------------


def test_GATE1_an_allow_listed_ip_write_session_registers(clone):
    """The discriminating presence: the key type says not-write, the server computes yes. An SDK
    deciding by key type registers nothing here."""
    world = clone.world("listed")
    c = client(clone, world, "ip")
    miss(c)
    assert c.flush_pending()["success"] is True
    assert phrases(clone, world, "ip") == [("UI", "Checkout")]


def test_GATE1_a_session_told_no_sends_nothing_even_once_the_world_would_accept(clone):
    """DRIFT. Learned no -> the case moves to an allow-listed address -> a later flush still leaves
    no state. An SDK that ignored the answer would have been refused, kept the phrase, and landed
    it now."""
    world = clone.world("off-list")
    c = client(clone, world, "ip")
    miss(c)
    assert c.flush_pending()["reason"] == "not-write-enabled"
    clone.update(world, caller=world.moved)
    c.flush_pending(force=True)
    assert phrases(clone, world, "ip") == []

    control = client(clone, world, "ip")  # acting in the drifted world
    miss(control)
    assert control.flush_pending()["success"] is True
    assert phrases(clone, world, "ip") == [("UI", "Checkout")]


# -- GATE-2 / GATE-5 / REG-8: failures keep the work until it lands ----------------------------


def test_GATE2_an_unknown_answer_holds_the_queue_until_the_server_can_say_yes(clone):
    """The session's catalog is one an earlier session cached, so it holds no answer (GATE-8) and
    asks at the send site, where the authorization fails once."""
    world = clone.world("writer")
    cache = MemoryCache()
    client(clone, world, cache=cache).get_translations("it-it")
    clone.update(world, faults=[{"method": "GET", "path": authorize(world), "status": 500}])
    c = client(clone, world, cache=cache)
    miss(c)
    assert c.flush_pending()["reason"] == "capability-unknown"
    assert phrases(clone, world) == []
    assert c.flush_pending(force=True)["success"] is True
    assert phrases(clone, world) == [("UI", "Checkout")], "the held phrase never landed"


def test_GATE5_a_refused_send_is_not_recorded_as_done_and_lands_on_the_second_read(clone):
    world = clone.world("writer", faults=[{"method": "POST", "path": "/translatable-items",
                                           "status": 500}])
    c = client(clone, world)
    miss(c)
    assert c.flush_pending()["success"] is False
    assert phrases(clone, world) == []
    assert c.has_pending, "the refused phrase was marked done"
    assert c.flush_pending(force=True)["success"] is True
    assert phrases(clone, world) == [("UI", "Checkout")]


def test_REG8_nothing_is_sent_while_backing_off_though_the_server_would_accept(clone):
    """The fault is consumed by the first send, so a second send inside the backoff WOULD be
    accepted: an empty state here means it was not made."""
    world = clone.world("writer", faults=[{"method": "POST", "path": "/translatable-items",
                                           "status": 500}])
    c = client(clone, world)
    miss(c)
    c.flush_pending()
    assert c.flush_pending()["reason"] == "backoff"
    assert phrases(clone, world) == []
    assert c.flush_pending(force=True)["success"] is True  # control: the server would accept
    assert phrases(clone, world) == [("UI", "Checkout")]


# -- OBS-1 -------------------------------------------------------------------------------------


def _unusable(caplog):
    return [r for r in caplog.records if "NOT write-enabled although" in r.getMessage()]


def test_OBS1_an_unusable_capability_is_surfaced_once(clone, caplog):
    world = clone.world("off-list")
    c = client(clone, world, "ip")
    with caplog.at_level(logging.WARNING, logger="langsys"):
        for name in ("One", "Two", "Three"):
            miss(c, name)
            c.flush_pending(force=True)
    assert len(_unusable(caplog)) == 1


def test_OBS1_control_an_allow_listed_session_is_not_reported(clone, caplog):
    world = clone.world("listed")
    c = client(clone, world, "ip")
    with caplog.at_level(logging.WARNING, logger="langsys"):
        miss(c)
        c.flush_pending()
    assert _unusable(caplog) == []
