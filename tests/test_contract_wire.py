"""Entry points, batching and the wire, on the SDK test clone (spec CONF-2): GATE-7, REG-9,
WIRE-2, WIRE-3, WIRE-4.
"""

from __future__ import annotations

import pytest
from clone import UNCATEGORIZED
from clone import held_blocks as blocks
from clone import held_phrases as phrases
from clone import sdk_client as client

pytest.importorskip("lxml")
pytestmark = pytest.mark.contract


def miss(c, phrase: str = "Checkout", category: str = "UI") -> None:
    c.translate(phrase, category=category, locale="it-it")
    assert c.has_pending, "control: the miss was not recorded"


SEED = "wire"


# -- GATE-7: every detecting path feeds the register lane --------------------------------------


def test_GATE7_every_entry_point_lands_its_misses(clone):
    world = clone.world("writer")
    c = client(clone, world)
    c.translate("Loose phrase", category="UI", locale="it-it")
    c.translate_content_block("<div><p>Block one</p><p>Block two</p></div>", category="UI")
    c.set_locale("it-it")
    c.translate_page("<html><body><p>Page phrase</p></body></html>", category="UI")
    assert c.flush_pending()["success"] is True
    held = {p for _, p in phrases(clone, world)}
    assert {"Loose phrase", "Page phrase"} <= held, held
    assert blocks(clone, world) == [["Block one", "Block two"]]


# -- REG-9 ---------------------------------------------------------------------------------------


def test_REG9_the_api_enforces_its_limit_and_holds_every_item(clone):
    """The clone's batch limit is 3: five items in one request would be refused with 422."""
    world = clone.world("writer")
    c = client(clone, world)
    names = [f"Phrase {i}" for i in range(5)]
    for name in names:
        miss(c, name)
    assert c.flush_pending()["success"] is True
    assert [p for _, p in phrases(clone, world)] == sorted(names)


# -- WIRE-2 / WIRE-3 / WIRE-4 ------------------------------------------------------------------


def test_WIRE2_an_empty_204_is_success(clone):
    world = clone.world("writer", faults=[{"method": "POST", "path": "/translatable-items",
                                           "status": 204}])
    c = client(clone, world)
    miss(c)
    assert c.flush_pending()["success"] is True
    assert not c.has_pending


def test_WIRE3_an_uncategorised_block_reads_back_under_the_sentinel_and_is_found(clone):
    """The API stores an uncategorised block and serves it under `__uncategorized__`, as it does
    uncategorised phrases; a second render must find it there instead of registering it again."""
    world = clone.world("writer")
    html = "<div><p>First sentence</p><p>Second sentence</p></div>"
    first = client(clone, world)
    first.translate_content_block(html)
    assert first.flush_pending()["success"] is True
    held = clone.held(world.key("write"), world.project(), "en-us")
    assert list(held["blocks"]) == [UNCATEGORIZED]

    second = client(clone, world)
    second.translate_content_block(html)
    assert second.pending_content_blocks == [], "the stored block was not found under the sentinel"


@pytest.mark.parametrize("fault", [{"drop": True}, {"status": 500}], ids=["dropped", "http-500"])
def test_WIRE4_a_failed_catalog_degrades_and_queues_nothing(clone, fault):
    world = clone.world("translated", faults=[{"method": "GET", "path": "/translations", **fault}])
    c = client(clone, world)
    assert c.translate("Unknown phrase", category="UI", locale="it-it") == "Unknown phrase"
    assert not c.has_pending
    c.flush_pending(force=True)
    assert phrases(clone, world) == [("UI", "Checkout")], "a failed fetch registered something"

    control = client(clone, world)
    assert control.translate("Checkout", category="UI", locale="it-it") == "Cassa"


