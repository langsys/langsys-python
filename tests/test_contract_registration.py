"""What a write reports, on the SDK test clone (spec CONF-2): REG-10.

A skipped write, a refused one and a successful one each read differently, and each is checked
against what the API holds afterwards.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from clone import held_phrases as phrases
from clone import sdk_client as client

pytest.importorskip("lxml")
pytestmark = pytest.mark.contract


def miss(c, phrase: str = "Checkout", category: str = "UI") -> None:
    c.translate(phrase, category=category, locale="it-it")
    assert c.has_pending, "control: the miss was not recorded"


SEED = "registration"


# -- REG-10: what a write reports --------------------------------------------------------------


def test_REG10_a_read_only_flush_is_reported_as_failure(clone):
    world = clone.world("writer")
    c = client(clone, world, "read")
    miss(c)
    result = c.flush_pending()
    assert result["success"] is False and result["skipped"] is True
    assert phrases(clone, world) == []

    control = client(clone, world, "write")
    miss(control)
    assert control.flush_pending()["success"] is True
    assert phrases(clone, world) == [("UI", "Checkout")]


def test_REG10_a_skip_names_not_write_enabled(clone):
    world = clone.world("writer")
    c = client(clone, world, "read")
    miss(c)
    result = c.flush_pending()
    assert (result["success"], result["skipped"], result["reason"]) == (False, True, "not-write-enabled")
    synced = client(clone, world, "read").sync(["Brand new"], locale="it-it")
    assert (synced["success"], synced["skipped"], synced["reason"]) == (False, True, "not-write-enabled")
    assert phrases(clone, world) == []


def test_REG10_a_skip_names_the_unavailable_catalog(clone):
    world = clone.world("writer", faults=[{"method": "GET", "path": "/translations", "status": 500,
                                           "times": 4}])
    result = client(clone, world).sync(["Brand new"], locale="it-it")
    assert (result["success"], result["skipped"], result["reason"]) == (False, True, "catalog-unavailable")
    assert phrases(clone, world) == []


def test_REG10_a_flush_with_nothing_queued_because_the_catalog_failed_names_it(clone):
    """WIRE-4 queues nothing off an unread catalog, so the flush has nothing to send: that is a
    skipped write, not an empty success."""
    world = clone.world("writer", faults=[{"method": "GET", "path": "/translations", "status": 500}])
    c = client(clone, world)
    c.translate("Checkout", category="UI", locale="it-it")
    assert not c.has_pending
    result = c.flush_pending()
    assert (result["success"], result["skipped"], result["reason"]) == (False, True, "catalog-unavailable")


def test_REG10_once_the_catalog_recovers_an_empty_flush_is_an_honest_success(clone):
    now = [1000.0]
    world = clone.world("translated", faults=[{"method": "GET", "path": "/translations",
                                               "status": 500}])
    with patch("langsys.catalog._clock", lambda: now[0]):
        c = client(clone, world)
        assert c.translate("Checkout", category="UI", locale="it-it") == "Checkout"
        assert c.flush_pending()["reason"] == "catalog-unavailable", "control: the failure is seen"
        now[0] += 3600
        assert c.translate("Checkout", category="UI", locale="it-it") == "Cassa"
        assert c.flush_pending() == {"phrases": 0, "content_blocks": 0, "success": True}


def test_REG10_a_refused_batch_is_a_failure_distinct_from_both_skips(clone):
    world = clone.world("writer", faults=[{"method": "POST", "path": "/translatable-items",
                                           "status": 500}])
    c = client(clone, world)
    miss(c)
    result = c.flush_pending()
    assert result["success"] is False and not result.get("skipped")
    assert result["reason"] == "registration-failed"
    assert phrases(clone, world) == []


def test_REG10_control_a_write_enabled_registration_succeeds_and_reads_back(clone):
    world = clone.world("writer")
    c = client(clone, world)
    miss(c, "Brand new")
    assert c.flush_pending()["success"] is True
    synced = client(clone, world).sync([{"phrase": "Also new", "category": "UI"}], locale="it-it")
    assert synced["success"] is True and synced["synced"] is True
    assert phrases(clone, world) == [("UI", "Also new"), ("UI", "Brand new")]


