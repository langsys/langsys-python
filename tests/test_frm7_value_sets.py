"""FRM-7: a finite set of translatable values is declared, written into its sentence, and extended at
runtime.

A declaration names the placeholder it fills. A call whose param is a declared value of that
placeholder looks up the sentence with the value written in as its display word, never the template.
With runtime registration off (FRM-2), the one sentence that still registers is a declared value's
whose template the catalog shows was synced: the catalog lacks it and holds another of the template's
sentences. It is queued for the flush after the response.
"""

from __future__ import annotations

import enum
import logging

import pytest

from langsys import LangsysClient, Miss, request_scope
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch
from langsys.values import ValueSets, translates_as

API = "https://api.test/api"
U = "__uncategorized__"


@translates_as("status")
class Status(enum.Enum):
    SHIPPED = "shipped"
    DELIVERED = "delivered"

    def label(self) -> str:
        return {"shipped": "Shipped", "delivered": "Delivered"}[self.value]


@translates_as("size")
class Size(enum.Enum):
    """No label: each value is its display word."""

    SMALL = "Small"
    LARGE = "Large"


@translates_as("priority")
class Priority(enum.IntEnum):
    LOW = 1
    HIGH = 2

    @property
    def label(self) -> str:
        return "Low" if self is Priority.LOW else "High"


class OrderState(enum.Enum):
    """Never declared for a placeholder, whatever its name says."""

    SHIPPED = "shipped"


class Colours:
    """The contract, read each time it is asked: a row added counts at once."""

    words = ["Red", "Blue"]

    @classmethod
    def translatable_values(cls):
        return {"colour": list(cls.words)}


class Tones:
    def translatable_values(self):
        return {"tone": ["Warm", "Cool"]}


class Broken:
    @classmethod
    def translatable_values(cls):
        raise RuntimeError("the table is gone")


@pytest.fixture(autouse=True)
def _colours():
    Colours.words = ["Red", "Blue"]
    yield
    Colours.words = ["Red", "Blue"]


def make(catalog, *, runtime_registration=True, sets=(Status, Size, Priority, Colours, Tones),
         **options):
    client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                           debounce=0, auto_flush=False, value_sets=sets,
                           runtime_registration=runtime_registration, **options)
    client.set_locale("es-cr")
    client._catalog.get = lambda locale, use_cache=True: CatalogFetch(  # type: ignore[method-assign]
        catalog, ok=True)
    return client


def phrases(client):
    return [p["phrase"] for p in client.pending_phrases]


ORDERS = {U: {
    "The order is {status}.": "El pedido está {status}.",
    "The order is Shipped.": "El pedido fue enviado.",
    "The order is Delivered.": "El pedido fue entregado.",
    "Priority {priority}": "Prioridad {priority}",
    "Priority High": "Prioridad alta",
}}


def test_FRM7_every_kind_of_declaration_gives_its_display_words():
    sets = ValueSets([Status, Size, Priority, Colours, Tones(), OrderState])
    assert sets.words("status") == ["Shipped", "Delivered"], "label() was not the display word"
    assert sets.words("size") == ["Small", "Large"]
    assert sets.words("priority") == ["Low", "High"], "a label attribute was not the display word"
    assert sets.words("colour") == ["Red", "Blue"]
    assert sets.words("tone") == ["Warm", "Cool"], "an instance declaration was not read"
    assert sets.placeholders() == ["status", "size", "priority", "colour", "tone"], \
        "an enum declared for no placeholder became a set"


def test_FRM7_every_value_writes_one_sentence():
    sets = ValueSets([Status, Size])
    assert sets.sentences("The order is {status}.") == [
        "The order is Shipped.", "The order is Delivered."]
    assert sets.sentences("A {size} order, {status}, for {name}.") == [
        "A Small order, Shipped, for {name}.", "A Small order, Delivered, for {name}.",
        "A Large order, Shipped, for {name}.", "A Large order, Delivered, for {name}."]
    assert sets.sentences("Hello {name}") is None


@pytest.mark.parametrize("value", [Status.SHIPPED, "shipped", "Shipped"],
                         ids=["member", "backing-value", "display-word"])
def test_FRM7_a_declared_value_renders_its_sentence(value):
    client = make(ORDERS)
    assert client.translate("The order is {status}.", params={"status": value}) == \
        "El pedido fue enviado."
    assert client.resolve("The order is {status}.", params={"status": value}).origin == "catalog"


def test_FRM7_a_value_outside_the_set_is_an_ordinary_placeholder():
    client = make(ORDERS)
    assert client.translate("The order is {status}.", params={"status": "lost"}) == \
        "El pedido está lost."


def test_FRM7_a_placeholder_is_never_linked_by_name():
    catalog = {U: {"The order is {status}.": "El pedido está {status}.",
                   "The order is Shipped.": "El pedido fue enviado.",
                   "Paint it {color}.": "Píntalo {color}.",
                   "Paint it Red.": "Píntalo de rojo."}}
    undeclared = make(catalog, sets=[OrderState])
    assert undeclared.translate("The order is {status}.", params={"status": OrderState.SHIPPED}) \
        != "El pedido fue enviado.", "an enum declared for no placeholder was linked by its name"
    assert undeclared.translate("The order is {status}.", params={"status": "shipped"}) == \
        "El pedido está shipped."
    other = make(catalog)
    assert other.translate("Paint it {color}.", params={"color": "Red"}) == "Píntalo Red.", \
        "a set declared for {colour} filled {color}"


def test_FRM7_an_int_backed_set_renders_its_sentence():
    client = make(ORDERS)
    assert client.translate("Priority {priority}", params={"priority": Priority.HIGH}) == \
        "Prioridad alta"
    assert client.translate("Priority {priority}", params={"priority": 2}) == "Prioridad alta"


@pytest.mark.parametrize("value", ["2", "urgent", 2.0, True], ids=["digit", "word", "float", "bool"])
def test_FRM7_a_value_of_another_type_renders_the_template_and_never_raises(value):
    client = make(ORDERS)
    rendered = client.translate("Priority {priority}", params={"priority": value})
    assert rendered.startswith("Prioridad ") and rendered != "Prioridad alta"


def test_FRM7_membership_is_read_at_call_time():
    catalog = {U: {"Paint it Red.": None}}
    client = make(catalog, runtime_registration=False)
    assert client.translate("Paint it {colour}.", params={"colour": "Green"}) == "Paint it Green."
    assert phrases(client) == [], "a value outside the set registered"
    Colours.words.append("Green")
    assert client.translate("Paint it {colour}.", params={"colour": "Green"}) == "Paint it Green."
    assert phrases(client) == ["Paint it Green."], "the set was read before the row was added"


def test_FRM7_a_value_added_since_the_sync_registers_after_the_response():
    Colours.words.append("Green")
    client = make({U: {"Paint it Red.": "Píntalo de rojo.", "Paint it Blue.": None}},
                  runtime_registration=False)
    with request_scope():
        assert client.translate("Paint it {colour}.", params={"colour": "Green"}) == \
            "Paint it Green."
        assert client.flush_pending().get("reason") == "request-in-progress", \
            "the registration was sent on the request path"
        assert phrases(client) == ["Paint it Green."]
    assert client.pending_phrases == [{"phrase": "Paint it Green.", "category": U}]


def test_FRM7_anything_else_registers_nothing_at_runtime():
    catalog = {U: {"Paint it Red.": "Píntalo de rojo."}}
    client = make(catalog, runtime_registration=False)
    client.translate("Paint it {colour}.", params={"colour": "Mauve"})
    client.translate("Wash it {colour}.", params={"colour": "Red"})
    client.translate("Hello {name}", params={"name": "Ana"})
    client.translate("A new literal")
    assert phrases(client) == [], "only a synced template's new value registers"
    client.translate("Paint it {colour}.", params={"colour": "Blue"})
    assert phrases(client) == ["Paint it Blue."], "control: a declared value the sync missed"


def test_FRM7_with_runtime_registration_on_a_written_in_sentence_registers_as_the_phrase():
    client = make({U: {}})
    client.translate("Paint it {colour}.", params={"colour": "Red"})
    assert phrases(client) == ["Paint it Red."], "the template registered instead of the sentence"


def test_FRM7_the_miss_fallback_gets_the_call_as_written_and_fills_with_every_param():
    asked: list[Miss] = []

    def files(miss: Miss):
        asked.append(miss)
        return {"The order is {status}.": "El pedido está {status}."}.get(miss.argument)

    client = make({U: {}}, miss_fallback=files)
    assert client.translate("The order is {status}.", params={"status": "shipped"}) == \
        "El pedido está shipped."
    assert asked == [Miss("The order is Shipped.", "es-CR", None, "The order is {status}.")]


def test_FRM7_a_declaration_that_raises_declares_nothing(caplog):
    client = make(ORDERS, sets=[Broken, Status])
    with caplog.at_level(logging.WARNING, logger="langsys"):
        assert client.translate("The order is {status}.", params={"status": "shipped"}) == \
            "El pedido fue enviado."
    assert any("declares nothing" in r.getMessage() for r in caplog.records)


def test_FRM7_translate_legacy_writes_a_declared_value_in():
    client = make({U: {"The order is Shipped.": "El pedido fue enviado."}})
    assert client.translate_legacy("The order is %(status)s.", params={"status": "shipped"}) == \
        "El pedido fue enviado."
