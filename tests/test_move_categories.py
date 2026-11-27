"""MOVE-1..5: content moved between categories names the category it was registered under before.

Every surface that takes a category takes `previous_category` (`""` for none, None for no move).
It travels beside `category` on the registration a miss triggers and on nothing else, is never part
of an id or a queue key, and until the new category has a translation the previous category's entry
renders. A host filled from the previous category names it.

The catalog each test renders against is held in `Moving.catalog`, so a test can change it between
renders; registrations are read from the transport.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

import httpx
import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch
from langsys.registration import generate_custom_id, legacy_custom_ids
from langsys.snapshot import Snapshot

API = "https://api.test/api"
ITEMS = f"{API}/translatable-items"
AUTH = f"{API}/authorize-project/proj-1"
ABSENT = object()

TOKENS = ["Buy now", "Free returns"]
BLOCK = "<div><p>Buy now</p><p>Free returns</p></div>"
#: The same two tokens as one unit on the page path, where a `div` of `p`s is two phrases.
PAGE_BLOCK = "<div><span>Buy now</span><span>Free returns</span></div>"
CURRENT_ID = generate_custom_id("Checkout", TOKENS)
PREVIOUS_ID = generate_custom_id("Header", TOKENS)
SPANISH = {"Buy now": "Comprá ya", "Free returns": "Devolución gratis"}


def historical(category):
    """A CID-3 id of the block that is not its primary one: for ASCII text the JS family's hash
    is the primary id, so the pipe-join form."""
    (found,) = [i for i in legacy_custom_ids(category, TOKENS)
                if i != generate_custom_id(category, TOKENS)]
    return found


def authorized(write_enabled=True):
    return {"status": True, "data": {
        "id": "proj-1", "title": "T", "base_locale": "en-us",
        "target_locales": ["en-us", "es-cr", "it-it"], "default_locales": {},
        "key_type": "write", "write_enabled": write_enabled,
        "langsys_settings": {"translatable_items": {"batch_limit": 200}}}}


class Api:
    """Authorizes the project and records every registration POST; `hold` keeps one open."""

    def __init__(self, httpx_mock, write_enabled=True):
        httpx_mock.add_response(url=AUTH, json=authorized(write_enabled), is_reusable=True,
                                is_optional=True)
        httpx_mock.add_callback(self, url=ITEMS, is_reusable=True, is_optional=True)
        self.bodies: list[dict[str, Any]] = []
        self.hold: Optional[threading.Event] = None
        self.arrived = threading.Event()

    def __call__(self, request):
        self.bodies.append(json.loads(request.content))
        self.arrived.set()
        if self.hold is not None:
            self.hold.wait(3)
        return httpx.Response(200, json={"status": True, "data": [{"status": True}]})

    def items(self):
        return [item for body in self.bodies for item in body["translatable_items"]]

    def moves(self):
        """(type, category, previous_category or ABSENT) per item sent, in order."""
        return [(item["type"], item.get("category"), item.get("previous_category", ABSENT))
                for item in self.items()]


@pytest.fixture
def api(httpx_mock):
    return Api(httpx_mock)


class Moving:
    def __init__(self, catalog=None, locale="es-cr", **options):
        self.catalog: dict[str, Any] = catalog if catalog is not None else {}
        self.client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(),
                                    base_locale="en-us", debounce=0, auto_flush=False, **options)
        self.client.set_locale(locale)
        self.client._catalog.get = self._get  # type: ignore[method-assign]

    def _get(self, locale, use_cache=True):
        return CatalogFetch(self.catalog, ok=True)

    def queued(self):
        """(category, phrase, previous or ABSENT) for each queued phrase."""
        return [(p["category"], p["phrase"], p.get("previous_category", ABSENT))
                for p in self.client.pending_phrases]


def _phrases(api):
    return [(i.get("category"), i["phrase"], i.get("previous_category", ABSENT))
            for i in api.items() if i["type"] == "phrase"]


# -- MOVE-1: every surface takes it, and it travels beside `category` ----------------------------


@pytest.mark.parametrize(("previous", "sent"), [
    ("Header", "Header"), ("", ""), (None, ABSENT), ("Checkout", ABSENT),
], ids=["moved", "from-no-category", "not-moved", "equal-to-the-category"])
def test_MOVE1_a_missed_phrase_registers_with_the_previous_category_it_names(api, previous, sent):
    moving = Moving()
    moving.client.translate("Buy now", category="Checkout", previous_category=previous)
    moving.client.flush_pending()
    assert _phrases(api) == [("Checkout", "Buy now", sent)]


@pytest.mark.parametrize(("previous", "sent"), [("Header", "Header"), ("", ABSENT)],
                         ids=["moved-out-of-a-category", "no-category-on-both-sides"])
def test_MOVE1_no_category_is_the_empty_string_on_both_sides(api, previous, sent):
    moving = Moving()
    moving.client.translate("Buy now", previous_category=previous)
    moving.client.flush_pending()
    assert _phrases(api) == [(None, "Buy now", sent)]


def test_MOVE1_a_block_registered_from_a_fragment_carries_it(api):
    moving = Moving()
    moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    moving.client.flush_pending()
    assert api.moves() == [("content_block", "Checkout", "Header")]
    assert api.items()[0]["custom_id"] == CURRENT_ID


def test_MOVE1_explicit_registrations_carry_it_and_drop_one_equal_to_the_category(api):
    client = Moving().client
    client.register_phrases([
        {"phrase": "Buy now", "category": "Checkout", "previous_category": "Header"},
        {"phrase": "Pay", "category": "Checkout", "previous_category": "Checkout"},
        {"phrase": "Hello", "previous_category": ""},
        "Plain",
    ])
    client.register_content_block(BLOCK, TOKENS, category="Checkout", previous_category="")
    assert api.moves() == [("phrase", "Checkout", "Header"), ("phrase", "Checkout", ABSENT),
                           ("phrase", None, ABSENT), ("phrase", None, ABSENT),
                           ("content_block", "Checkout", "")]


def test_MOVE1_a_framework_entry_point_carries_it(api):
    moving = Moving()
    moving.client.translate_legacy("Welcome back", category="Home", previous_category="Landing")
    moving.client.flush_pending()
    assert _phrases(api) == [("Home", "Welcome back", "Landing")]


def test_MOVE1_a_pages_default_category_names_its_move_head_and_body(api):
    moving = Moving()
    moving.client.translate_page(
        "<html><head><title>Store</title></head><body><p>Buy now</p></body></html>",
        category="Checkout", previous_category="Header")
    moving.client.flush_pending()
    assert sorted(_phrases(api)) == [("Checkout", "Buy now", "Header"),
                                     ("Checkout", "Store", "Header")]


PAGE = """<html><body>
<div data-ls-category="Checkout" data-ls-previous-category="Header">
  <p>Pay now</p>
  <span data-ls-phrase>Inherits the move</span>
  <span data-ls-phrase data-ls-category="Footer">Own category, no move</span>
  <span data-ls-phrase data-ls-category="Footer" data-langsys-previous-category="Nav">Own move</span>
  <section><p>Read the <a href="/t">terms</a> <span data-ls-phrase>Inside a block</span></p></section>
</div>
<ul class="menu"><li>Home</li></ul>
<ol class="plain"><li>Away</li></ol>
</body></html>"""


def test_MOVE1_markup_and_selectors_name_the_move_and_nested_hosts_follow_their_block(api):
    moving = Moving()
    moving.client.translate_page(PAGE, selector_categories={
        ".menu": {"category": "Menu", "previous_category": "Nav"}, ".plain": "Plain"})
    moving.client.flush_pending()
    phrases = set(_phrases(api))
    assert phrases == {
        ("Checkout", "Pay now", "Header"),
        ("Checkout", "Inherits the move", "Header"),
        ("Footer", "Own category, no move", ABSENT),
        ("Footer", "Own move", "Nav"),
        ("Checkout", "Inside a block", "Header"),
        ("Menu", "Home", "Nav"),
        ("Plain", "Away", ABSENT),
    }, phrases
    blocks = [m for m in api.moves() if m[0] == "content_block"]
    assert blocks == [("content_block", "Checkout", "Header")]


# -- MOVE-1: a whole category by configuration -------------------------------------------------------


MAP = {"checkout": "header", "legal": None, "contact": ""}


@pytest.mark.parametrize(("category", "named", "sent"), [
    ("checkout", None, "header"),
    ("checkout", "", ""),
    ("checkout", "checkout", "header"),
    ("checkout", "nav", "nav"),
    ("legal", None, ABSENT),
    ("contact", None, ""),
    ("other", None, ABSENT),
], ids=["map", "call-names-no-category", "call-names-its-own-category", "call-names-another",
        "null-entry", "map-names-no-category", "unmapped"])
def test_MOVE1_the_configured_map_moves_what_no_call_moves(api, category, named, sent):
    moving = Moving(previous_categories=MAP)
    moving.client.translate("Pay", category=category, previous_category=named)
    moving.client.flush_pending()
    assert _phrases(api) == [(category, "Pay", sent)]


def test_MOVE1_the_map_moves_a_keys_group_at_render(api, tmp_path):
    source = tmp_path / "en.json"
    source.write_text(json.dumps({"checkout": {"submit": "Pay now"}}))
    moving = Moving({"header": {"Pay now": "Pagá ahora"}}, previous_categories=MAP,
                    legacy_files=[source])
    assert moving.client.translate("checkout.submit") == "Pagá ahora"
    assert moving.queued() == [("checkout", "Pay now", "header")]


def test_MOVE1_the_map_moves_every_line_an_import_registers(httpx_mock, tmp_path):
    api = Api(httpx_mock)
    source = tmp_path / "en.json"
    source.write_text(json.dumps({"checkout": {"submit": "Pay now"}, "welcome": "Hi"}))
    italian = tmp_path / "it.json"
    italian.write_text(json.dumps({"checkout": {"submit": "Paga ora"}, "welcome": "Ciao"}))
    moving = Moving(previous_categories={"checkout": "header"}, legacy_files=[source])
    moving.client.import_legacy_translations({"it-it": [italian]})
    assert set(_phrases(api)) == {(None, "Hi", ABSENT), ("checkout", "Pay now", "header")}


def test_MOVE1_a_sync_sends_the_first_previous_category_named_and_reports_a_disagreement(api):
    moving = Moving(previous_categories={"checkout": "header"})
    result = moving.client.sync([
        {"phrase": "Pay", "category": "checkout"},
        {"phrase": "Pay", "category": "checkout", "previous_category": "nav"},
        {"phrase": "Pay", "category": "checkout", "previous_category": "cart"},
        {"phrase": "Total", "category": "checkout"},
        {"phrase": "Total", "category": "checkout", "previous_category": "checkout"},
    ])
    assert _phrases(api) == [("checkout", "Pay", "nav"), ("checkout", "Total", "header")]
    assert result["move_conflicts"] == [
        {"phrase": "Pay", "category": "checkout", "previous_categories": ["nav", "cart"]}]


def test_MOVE1_a_move_that_leaves_a_use_behind_is_reported_once_with_debug_on(caplog):
    moving = Moving(debug=True)
    with caplog.at_level("DEBUG", logger="langsys"):
        moving.client.translate("Buy now", category="Checkout", previous_category="Header")
        moving.client.translate("Free returns", category="Checkout", previous_category="Header")
        assert not [r for r in caplog.records if "drops out" in r.getMessage()], \
            "control: a move with no use left behind was reported"
        moving.client.translate("Buy now", category="Header")
        moving.client.translate("Buy now", category="Header")
    reported = [r.getMessage() for r in caplog.records if "drops out" in r.getMessage()]
    assert len(reported) == 1 and "'Buy now'" in reported[0] and "'Header'" in reported[0]


# -- MSG-6: the message category's previous category ------------------------------------------------


def test_MSG6_templates_register_under_the_new_category_with_its_previous_one(api):
    moving = Moving(message_category="Messages", message_previous_category="Errors",
                    previous_categories={"Messages": "Other"})
    moving.client.server_message("Invalid API key")
    moving.client.flush_pending()
    moving.client.register_templates(["The key expired."])
    assert _phrases(api) == [("Messages", "Invalid API key", "Errors"),
                             ("Messages", "The key expired.", "Errors")]


def test_MSG6_a_template_renders_from_the_previous_category_before_message():
    entry = {"template": "Invalid API key", "message": "Invalid API key (served)"}
    moving = Moving({"Errors": {"Invalid API key": "Clave inválida"}},
                    message_category="Messages", message_previous_category="Errors")
    assert moving.client.render_server_message(entry) == "Clave inválida"
    moving.catalog["Messages"] = {"Invalid API key": "Clave no válida"}
    assert moving.client.render_server_message(entry) == "Clave no válida"
    unmoved = Moving({"Errors": {"Invalid API key": "Clave inválida"}}, message_category="Messages")
    assert unmoved.client.render_server_message(entry) == "Invalid API key (served)"


def test_MSG6_registered_or_not_is_decided_under_the_new_category_alone():
    moving = Moving({"Errors": {"Invalid API key": None}}, message_category="Messages",
                    message_previous_category="Errors")
    moving.client.server_message("Invalid API key")
    assert moving.queued() == [("Messages", "Invalid API key", "Errors")]


# -- MOVE-2: never part of identity ----------------------------------------------------------------


def test_MOVE2_a_block_has_one_id_and_one_queue_entry_with_and_without_it():
    moving = Moving()
    moved = moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    unmoved = moving.client.translate_content_block(BLOCK, "Checkout")
    assert f'data-ls-contentblock="{CURRENT_ID}"' in moved
    assert f'data-ls-contentblock="{CURRENT_ID}"' in unmoved
    assert [b["custom_id"] for b in moving.client.pending_content_blocks] == [CURRENT_ID]


def test_MOVE2_a_phrase_has_one_queue_entry_and_one_catalog_key_with_and_without_it():
    moving = Moving()
    moving.client.translate("Buy now", category="Checkout")
    moving.client.translate("Buy now", category="Checkout", previous_category="Header")
    assert moving.queued() == [("Checkout", "Buy now", "Header")]
    moving.catalog["Checkout"] = {"Buy now": "Comprar"}
    assert moving.client.translate("Buy now", category="Checkout", previous_category="Header") \
        == "Comprar"


# -- MOVE-3: only on the registration a miss triggers ----------------------------------------------


@pytest.mark.parametrize("order", [("Header", None), (None, "Header"), ("Header", "Nav")],
                         ids=["moved-first", "unmoved-first", "two-moves"])
def test_MOVE3_an_item_queued_twice_is_sent_once_with_the_first_previous_category(api, order):
    moving = Moving()
    for previous in order:
        moving.client.translate("Buy now", category="Checkout", previous_category=previous)
        moving.client.translate_content_block(BLOCK, "Checkout", previous_category=previous)
    moving.client.flush_pending()
    assert api.moves() == [("phrase", "Checkout", "Header"), ("content_block", "Checkout", "Header")]


def test_MOVE3_an_item_the_catalog_has_under_its_category_is_not_registered_for_its_move(api):
    moving = Moving({"Checkout": {"Buy now": None, CURRENT_ID: {}}})
    moving.client.translate("Buy now", category="Checkout", previous_category="Header")
    moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    assert not moving.client.has_pending
    moving.client.translate("Pay", category="Checkout", previous_category="Header")
    assert moving.queued() == [("Checkout", "Pay", "Header")], "control: a miss registers"


def test_MOVE3_once_registered_and_refreshed_a_moved_item_is_not_sent_again(api):
    moving = Moving()
    moving.client.translate("Buy now", category="Checkout", previous_category="Header")
    moving.client.flush_pending()
    moving.catalog["Checkout"] = {"Buy now": None}  # the refreshed catalog holds the new item
    moving.client.translate("Buy now", category="Checkout", previous_category="Header")
    moving.client.flush_pending()
    assert _phrases(api) == [("Checkout", "Buy now", "Header")]


def test_MOVE3_a_session_that_may_not_write_sends_nothing(httpx_mock):
    api = Api(httpx_mock, write_enabled=False)
    moving = Moving()
    moving.client.translate("Buy now", category="Checkout", previous_category="Header")
    result = moving.client.flush_pending()
    assert api.items() == [] and result["reason"] == "not-write-enabled"


def test_MOVE3_a_move_named_while_the_unmoved_send_is_out_is_sent_after_it(api):
    moving = Moving()
    moving.client.translate("Buy now", category="Checkout")
    moving.client.translate_content_block(BLOCK, "Checkout")
    api.hold = threading.Event()
    flush = threading.Thread(target=moving.client.flush_pending, daemon=True)
    flush.start()
    assert api.arrived.wait(3)
    moving.client.translate("Buy now", category="Checkout", previous_category="Header")
    moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    moving.client.translate("Pay", category="Checkout")
    api.hold.set()
    flush.join(3)
    assert moving.queued() == [("Checkout", "Buy now", "Header"), ("Checkout", "Pay", ABSENT)]
    assert [b.get("previous_category") for b in moving.client.pending_content_blocks] == ["Header"]
    moving.client.flush_pending()
    assert _phrases(api) == [("Checkout", "Buy now", ABSENT), ("Checkout", "Buy now", "Header"),
                             ("Checkout", "Pay", ABSENT)]
    assert [m for m in api.moves() if m[0] == "content_block"] == [
        ("content_block", "Checkout", ABSENT), ("content_block", "Checkout", "Header")]
    assert not moving.client.has_pending


@pytest.mark.parametrize(("second", "posts"), [("Header", 2), (None, 1)],
                         ids=["moved-joins-unmoved", "unmoved-joins-unmoved"])
def test_MOVE3_a_moved_block_joining_an_unmoved_request_in_flight_sends_its_move_after(
        api, second, posts):
    api.hold = threading.Event()
    first = threading.Thread(target=lambda: Moving().client.register_content_block(
        BLOCK, TOKENS, category="Checkout"), daemon=True)
    first.start()
    assert api.arrived.wait(3)
    joined = threading.Thread(target=lambda: Moving().client.register_content_block(
        BLOCK, TOKENS, category="Checkout", previous_category=second), daemon=True)
    joined.start()
    joined.join(0.3)
    assert len(api.bodies) == 1, "the joining registration sent while the first was in flight"
    api.hold.set()
    first.join(3)
    joined.join(3)
    assert len(api.bodies) == posts
    assert api.moves()[-1] == ("content_block", "Checkout", second if second else ABSENT)


def test_MOVE3_a_flushed_moved_block_joining_an_unmoved_request_sends_its_move_after(api):
    api.hold = threading.Event()
    first = threading.Thread(target=lambda: Moving().client.register_content_block(
        BLOCK, TOKENS, category="Checkout"), daemon=True)
    first.start()
    assert api.arrived.wait(3)
    moving = Moving()
    moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    flush = threading.Thread(target=moving.client.flush_pending, daemon=True)
    flush.start()
    flush.join(0.3)
    assert len(api.bodies) == 1
    api.hold.set()
    first.join(3)
    flush.join(3)
    assert api.moves() == [("content_block", "Checkout", ABSENT),
                           ("content_block", "Checkout", "Header")]


def test_MOVE3_a_moved_block_joining_a_moved_request_sends_nothing_more(api):
    api.hold = threading.Event()
    register = lambda: Moving().client.register_content_block(  # noqa: E731
        BLOCK, TOKENS, category="Checkout", previous_category="Header")
    first = threading.Thread(target=register, daemon=True)
    first.start()
    assert api.arrived.wait(3)
    joined = threading.Thread(target=register, daemon=True)
    joined.start()
    joined.join(0.3)
    api.hold.set()
    first.join(3)
    joined.join(3)
    assert len(api.bodies) == 1


# -- MOVE-4: the previous category's entry renders until the new one has its own --------------------


def test_MOVE4_the_specs_vector_renders_the_previous_entry_and_registers_under_the_new_category():
    moving = Moving({"Header": {"Buy now": "Comprá ya"}})
    assert moving.client.translate("Buy now", category="Checkout", previous_category="Header") \
        == "Comprá ya"
    assert moving.queued() == [("Checkout", "Buy now", "Header")]


def test_MOVE4_the_current_categorys_translation_wins():
    moving = Moving({"Header": {"Buy now": "Comprá ya"}, "Checkout": {"Buy now": "Comprar"}})
    assert moving.client.translate("Buy now", category="Checkout", previous_category="Header") \
        == "Comprar"
    assert not moving.client.has_pending


@pytest.mark.parametrize("untranslated", [None, ""], ids=["null", "empty"])
def test_MOVE4_an_entry_the_current_category_holds_untranslated_falls_back_too(untranslated):
    moving = Moving({"Header": {"Buy now": "Comprá ya"}, "Checkout": {"Buy now": untranslated}})
    assert moving.client.translate("Buy now", category="Checkout", previous_category="Header") \
        == "Comprá ya"
    assert not moving.client.has_pending, "registration is decided on the current category alone"


def test_MOVE4_a_previous_category_of_empty_reads_the_catalogs_no_category_entries():
    moving = Moving({"__uncategorized__": {"Buy now": "Comprá ya"}, "": {"Buy now": "literal key"}})
    assert moving.client.translate("Buy now", category="Checkout", previous_category="") \
        == "Comprá ya"


@pytest.mark.parametrize("previous_entry", [{"Buy now": ""}, {"Buy now": None}, {}],
                         ids=["empty", "null", "absent"])
def test_MOVE4_a_previous_category_with_no_translation_renders_source(previous_entry):
    moving = Moving({"Header": previous_entry})
    assert moving.client.translate("Buy now", category="Checkout", previous_category="Header") \
        == "Buy now"


def test_MOVE4_params_fill_the_previous_categorys_translation():
    moving = Moving({"Header": {"Hello {name}": "Hola {name}"}})
    assert moving.client.translate("Hello {name}", category="Checkout", previous_category="Header",
                                   params={"name": "Ana"}) == "Hola Ana"


def test_MOVE4_a_block_renders_from_its_previous_categorys_id_and_registers_under_its_own():
    moving = Moving({"Header": {PREVIOUS_ID: SPANISH}})
    html = moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    assert "Comprá ya" in html and "Devolución gratis" in html
    assert [(b["custom_id"], b["category"], b.get("previous_category"))
            for b in moving.client.pending_content_blocks] == [(CURRENT_ID, "Checkout", "Header")]


def test_MOVE4_a_block_reads_the_previous_categorys_historical_id_after_its_primary_one():
    moving = Moving({"Header": {historical("Header"): SPANISH}})
    html = moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    assert "Comprá ya" in html


def test_MOVE4_every_read_under_the_current_category_comes_before_the_previous_one():
    moving = Moving({"Checkout": {historical("Checkout"): {"Buy now": "Actual",
                                                           "Free returns": "Gratis"}},
                     "Header": {PREVIOUS_ID: SPANISH}})
    html = moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    assert "Actual" in html and "Comprá ya" not in html


def test_MOVE4_a_block_entry_with_no_phrases_is_no_translation():
    moving = Moving({"Checkout": {CURRENT_ID: {}}, "Header": {PREVIOUS_ID: SPANISH}})
    html = moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    assert "Comprá ya" in html
    assert not moving.client.has_pending


def test_MOVE4_the_page_path_renders_a_block_and_a_phrase_from_the_previous_category():
    moving = Moving({"Header": {PREVIOUS_ID: SPANISH, "Pay": "Pagar"}})
    page = moving.client.translate_page(
        f"<html><body>{PAGE_BLOCK}<p>Pay</p></body></html>", category="Checkout",
        previous_category="Header")
    assert "Comprá ya" in page and "Pagar" in page
    assert f'data-ls-contentblock="{CURRENT_ID}" data-ls-previous-contentblock="{PREVIOUS_ID}"' \
        in page, page
    assert moving.queued() == [("Checkout", "Pay", "Header")]
    assert [(b["custom_id"], b["previous_category"])
            for b in moving.client.pending_content_blocks] == [(CURRENT_ID, "Header")]


def test_MOVE4_an_id_the_app_fixed_is_read_under_the_previous_category():
    moving = Moving({"Header": {"hero": {"Buy now": "Comprá ya"}}})
    page = moving.client.translate_page(
        '<html><body><div data-ls-contentblock="hero" data-ls-previous-category="Header">'
        "<p>Buy now</p></div></body></html>", category="Checkout")
    assert "Comprá ya" in page
    assert [(b["custom_id"], b["category"], b.get("previous_category"))
            for b in moving.client.pending_content_blocks] == [("hero", "Checkout", "Header")]


def test_MOVE4_a_loaded_snapshot_falls_back_to_its_previous_category_too(tmp_path):
    path = Snapshot({
        "project_id": "proj-1", "generated_at": "2026-11-27T00:00:00Z", "base_locale": "en-us",
        "locales": ["es-cr"], "categories": ["Checkout", "Header"],
        "catalog": {"es-cr": {"Checkout": {"Buy now": None, CURRENT_ID: {}},
                              "Header": {"Buy now": "Comprá ya", PREVIOUS_ID: SPANISH}}},
    }).write_to(tmp_path / "snapshot.json")
    client = LangsysClient("k", "proj-1", api_url="http://127.0.0.1:9/api", cache=MemoryCache(),
                           base_locale="en-us", debounce=0, auto_flush=False)
    client.load_snapshot(path)
    client.set_locale("es-cr")
    assert client.translate("Buy now", category="Checkout", previous_category="Header") == "Comprá ya"
    assert client.translate("Buy now", category="Checkout") == "Buy now", "control: no move"
    html = client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    assert "Devolución gratis" in html and f'data-ls-previous-contentblock="{PREVIOUS_ID}"' in html


# -- MOVE-5: a host filled from the previous category says so ----------------------------------------


def test_MOVE5_a_block_filled_from_its_previous_category_carries_both_ids():
    moving = Moving({"Header": {PREVIOUS_ID: SPANISH}})
    html = moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    assert f'data-ls-contentblock="{CURRENT_ID}"' in html
    assert f'data-ls-previous-contentblock="{PREVIOUS_ID}"' in html


def test_MOVE5_a_block_filled_from_its_current_category_carries_no_previous_id():
    moving = Moving({"Checkout": {CURRENT_ID: {"Buy now": "Comprar", "Free returns": "Gratis"}},
                     "Header": {PREVIOUS_ID: SPANISH}})
    html = moving.client.translate_content_block(BLOCK, "Checkout", previous_category="Header")
    assert "Comprar" in html and "previous-contentblock" not in html
    page = moving.client.translate_page(
        f'<html><body><div data-langsys-previous-contentblock="{PREVIOUS_ID}"><span>Buy now</span>'
        "<span>Free returns</span></div></body></html>", category="Checkout")
    assert "Comprar" in page and "previous-contentblock" not in page, page


def test_MOVE5_a_phrase_host_filled_from_its_previous_category_names_it():
    moving = Moving({"Header": {"Buy now": "Comprá ya"}, "Checkout": {"Pay": "Pagar"}})
    page = moving.client.translate_page(
        '<html><body><p data-ls-phrase data-ls-category="Checkout" data-ls-previous-category="Header">'
        'Buy now</p><p data-ls-phrase data-ls-category="Checkout" data-ls-previous-category="Header">'
        'Pay</p><p data-ls-phrase data-ls-category="Checkout" data-ls-previous-category="Header">'
        "Untranslated</p></body></html>")
    assert '<p data-ls-phrase data-ls-category="Checkout" data-ls-previous-category="Header">Comprá ya' \
        in page, page
    assert '<p data-ls-phrase data-ls-category="Checkout">Pagar</p>' in page, page
    assert 'data-ls-previous-category="Header">Untranslated</p>' in page, "source keeps what was written"


def test_MOVE5_a_one_phrase_fragment_filled_from_its_previous_category_names_it():
    moving = Moving({"Header": {"Buy now": "Comprá ya"}})
    html = moving.client.translate_content_block("<p>Buy now</p>", "Checkout",
                                                 previous_category="Header")
    assert html == '<p data-ls-previous-category="Header">Comprá ya</p>'
    assert Moving({"Checkout": {"Buy now": "Comprar"}}).client.translate_content_block(
        "<p>Buy now</p>", "Checkout", previous_category="Header") == "<p>Buy now</p>".replace(
        "Buy now", "Comprar")


@pytest.mark.parametrize("spelling", ["data-ls-previous-contentblock",
                                      "data-langsys-previous-contentblock"])
def test_MOVE5_a_stamped_host_renders_from_its_stamped_previous_id_and_registers_its_own(spelling):
    moving = Moving({"Header": {PREVIOUS_ID: SPANISH}})
    page = moving.client.translate_page(
        f'<html><body><div data-ls-contentblock="{CURRENT_ID}" {spelling}="{PREVIOUS_ID}">'
        "<p>Buy now</p><p>Free returns</p></div></body></html>", category="Checkout")
    assert "Comprá ya" in page
    assert [(b["custom_id"], b.get("previous_category"))
            for b in moving.client.pending_content_blocks] == [(CURRENT_ID, "Header")]
    unstamped = Moving({"Header": {PREVIOUS_ID: SPANISH}}).client.translate_page(
        f'<html><body><div data-ls-contentblock="{CURRENT_ID}"><p>Buy now</p><p>Free returns</p>'
        "</div></body></html>", category="Checkout")
    assert "Comprá ya" not in unstamped, "control: without the stamp there is no way back"


def test_MOVE5_inside_a_resolved_scope_a_stamped_host_renders_and_registers_nothing():
    moving = Moving({"Header": {PREVIOUS_ID: SPANISH}})
    page = moving.client.translate_page(
        f'<html><body><main data-ls-resolved="es-cr"><div data-ls-contentblock="{CURRENT_ID}" '
        f'data-ls-previous-contentblock="{PREVIOUS_ID}"><p>Buy now</p><p>Free returns</p></div>'
        "</main></body></html>", category="Checkout")
    assert "Comprá ya" in page
    assert not moving.client.has_pending
