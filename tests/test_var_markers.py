"""VAR-1, VAR-3, VAR-7 and TOK-1's rich-phrase exclusions: value markers on every HTML path.

A marked value (`<!--ls:NAME-->VALUE<!--/ls-->`, or `<span data-ls-param="NAME">VALUE</span>`) is
the placeholder `{NAME}` in what registers, and the value renders back in its marker. Rows come
from the shared canonicalization fixture where it has them (`expected_content`, `phrase_cases`);
the rest are this core's own, each beside the unmarked control that shows the path does register
or render.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("lxml")

import langsys.html.markers as markers_module  # noqa: E402
from langsys import LangsysClient  # noqa: E402
from langsys.cache import MemoryCache  # noqa: E402
from langsys.catalog import CatalogFetch  # noqa: E402
from langsys.html.markers import ValueMarkers  # noqa: E402
from langsys.html.markup import encode_phrase_host, phrase_values  # noqa: E402
from langsys.html.parser import _parse_fragment, extract_phrases  # noqa: E402
from langsys.registration import generate_custom_id  # noqa: E402

_DOC = json.loads((Path(__file__).parent / "fixtures" / "canonicalization-reference.json")
                  .read_text(encoding="utf-8"))
CONTENT_ROWS = [r for r in _DOC["cases"] if "expected_content" in r]
PHRASE_ROWS = _DOC["phrase_cases"]


def client_with(catalog):
    client = LangsysClient("k", "p", api_url="http://x.invalid/api", cache=MemoryCache(),
                           base_locale="en-us", debounce=0, auto_flush=False)
    client.set_locale("it-it")
    return client, patch.object(client._catalog, "get", return_value=CatalogFetch(catalog, ok=True))


def page(body):
    return f"<html><body>{body}</body></html>"


def body_of(out):
    return out.split("<body>", 1)[1].rsplit("</body>", 1)[0]


def registered(client):
    return ([("phrase", p["phrase"]) for p in client.pending_phrases]
            + [("block", tuple(b["phrases"])) for b in client.pending_content_blocks])


@pytest.fixture(autouse=True)
def _fresh_notice(monkeypatch):
    monkeypatch.setattr(markers_module, "_NOTICED", [False])


# -- the fixture's stored-content and rich-phrase rows ---------------------------------------------


@pytest.mark.parametrize("row", CONTENT_ROWS, ids=lambda r: r["id"])
def test_VAR3_a_block_registers_the_fixtures_content_with_placeholders(row):
    client, patched = client_with({})
    with patched:
        client.translate_content_block(row["html"], row["category"])
    (block,) = client.pending_content_blocks
    assert block["content"] == row["expected_content"]
    assert block["custom_id"] == row["expected_custom_id"]


@pytest.mark.parametrize("row", PHRASE_ROWS, ids=lambda r: r["id"])
def test_TOK1_a_rich_phrase_encodes_and_registers_as_the_fixture_says(row):
    root = _parse_fragment(f'<p data-ls-phrase="">{row["html"]}</p>')
    markers = ValueMarkers.read(root)
    (host,) = list(root)
    phrase, _ = encode_phrase_host(host)
    assert phrase == row["expected_phrase"]
    assert phrase_values(markers, host) == row["expected_values"]

    client, patched = client_with({})
    with patched:
        client.translate_page(page(f'<p data-ls-phrase="">{row["html"]}</p>'), category=row["category"])
    assert registered(client) == ([("phrase", row["expected_phrase"])] if row["registers"] else [])


@pytest.mark.parametrize(("attribute", "tokens"), [
    ("data-notrans", ["Keep", "here"]),
    ('data-notrans="false"', ["Keep", "a@b.c", "here"]),
    ('translate="no"', ["Keep", "here"]),
], ids=["data-notrans", "opted-back-in", "translate-no"])
def test_TOK1_an_authors_exclusion_holds_on_the_block_path_too(attribute, tokens):
    assert extract_phrases(f"<p>Keep <span {attribute}>a@b.c</span> here</p>") == tokens


def test_TOK1_an_excluded_element_in_a_rich_phrase_renders_back_as_written():
    html = '<p data-ls-phrase="">Signed in as <span translate="no"><!--ls:email-->a@b.c<!--/ls--></span> today</p>'
    client, patched = client_with({"UI": {"Signed in as {m0o}{m0c} today": "Accesso come {m0o}{m0c} oggi"}})
    with patched:
        out = client.translate_page(page(html), category="UI")
    assert body_of(out) == ('<p data-ls-phrase="">Accesso come <span translate="no">'
                            '<!--ls:email-->a@b.c<!--/ls--></span> oggi</p>')


# -- VAR-1: one phrase for every user, each render its own value ------------------------------------


@pytest.mark.parametrize("form", [
    "<!--ls:name-->{v}<!--/ls-->", '<span data-ls-param="name">{v}</span>'], ids=["comment", "attribute"])
def test_VAR1_the_same_sentence_for_two_users_is_one_phrase_and_renders_back_byte_for_byte(form):
    client, patched = client_with({})
    outs = []
    with patched:
        for user in ("Ana", "Bob"):
            html = f"<p>Hello {form.format(v=user)}, welcome back</p>"
            outs.append((html, body_of(client.translate_page(page(html), category="UI"))))
    assert registered(client) == [("phrase", "Hello {name}, welcome back")]
    for html, out in outs:
        assert out == html


@pytest.mark.parametrize("form", [
    ("<!--ls:name-->Ana<!--/ls-->", "<!--ls:name-->Ana<!--/ls-->"),
    ('<span data-ls-param="name">Ana</span>', '<span data-ls-param="name">Ana</span>'),
], ids=["comment", "attribute"])
def test_VAR3_a_translation_renders_the_value_in_its_marker_where_the_translation_puts_it(form):
    marked, rendered = form
    client, patched = client_with({"UI": {"Hello {name}, welcome back": "Bentornato, {name}!"}})
    with patched:
        out = body_of(client.translate_page(page(f"<p>Hello {marked}, welcome back</p>"), category="UI"))
    assert out == f"<p>Bentornato, {rendered}!</p>"


@pytest.mark.parametrize(("count", "expected"), [("1", "Hai 1 messaggio"), ("3", "Hai 3 messaggi")],
                         ids=["one", "other"])
def test_VAR3_a_plural_in_the_translation_chooses_on_the_marked_value(count, expected):
    key = "You have {count} messages"
    client, patched = client_with({"UI": {key: "{count, plural, one {Hai # messaggio} other {Hai # messaggi}}"}})
    with patched:
        out = body_of(client.translate_page(
            page(f"<p>You have <!--ls:count-->{count}<!--/ls--> messages</p>"), category="UI"))
    assert out == f"<p>{expected}</p>"


def test_VAR3_a_translated_block_renders_each_value_in_place():
    html = "<p>Signed in as <!--ls:email-->a@b.c<!--/ls--> today</p><p>Welcome back</p>"
    tokens = ["Signed in as {email} today", "Welcome back"]
    block_id = generate_custom_id("UI", tokens)
    catalog = {"UI": {block_id: {tokens[0]: "Accesso come {email} oggi", tokens[1]: "Bentornato"}}}
    client, patched = client_with(catalog)
    with patched:
        out = client.translate_content_block(html, "UI")
    assert "<p>Accesso come <!--ls:email-->a@b.c<!--/ls--> oggi</p><p>Bentornato</p>" in out
    assert client.pending_content_blocks == []


def test_VAR3_a_translated_block_on_the_page_renders_each_value_in_place():
    html = "<p>Signed in as <!--ls:email-->a@b.c<!--/ls--> on <b>Monday</b></p>"
    tokens = ["Signed in as {email} on", "Monday"]
    block_id = generate_custom_id("UI", tokens)
    catalog = {"UI": {block_id: {tokens[0]: "Accesso come {email} il", tokens[1]: "lunedì"}}}
    client, patched = client_with(catalog)
    with patched:
        out = body_of(client.translate_page(page(html), category="UI"))
    assert out == (f'<p data-ls-contentblock="{block_id}">Accesso come <!--ls:email-->a@b.c<!--/ls--> '
                   "il <b>lunedì</b></p>")
    assert client.pending_content_blocks == []


def test_VAR3_a_block_with_a_value_in_an_element_slot_has_one_id_for_both_users():
    ids = {generate_custom_id("UI", extract_phrases(
        f"<p>Signed in as <b><!--ls:name-->{u}<!--/ls--></b> on day 2</p>")) for u in ("Ana", "Bob")}
    assert len(ids) == 1
    control = {generate_custom_id("UI", extract_phrases(f"<p>Signed in as <b>{u}</b> on day 2</p>"))
               for u in ("Ana", "Bob")}
    assert len(control) == 2, "control: unmarked values make one block per user"


def test_VAR3_a_phrase_host_reads_its_markers_and_renders_them_back():
    html = '<p data-ls-phrase="">Hello <b><!--ls:name-->Ana<!--/ls--></b>!</p>'
    client, patched = client_with({"UI": {"Hello {m0o}{name}{m0c}!": "Ciao {m0o}{name}{m0c}!"}})
    with patched:
        out = body_of(client.translate_page(page(html), category="UI"))
    assert out == '<p data-ls-phrase="">Ciao <b><!--ls:name-->Ana<!--/ls--></b>!</p>'
    miss, patched = client_with({})
    with patched:
        assert body_of(miss.translate_page(page(html), category="UI")) == html
    assert registered(miss) == [("phrase", "Hello {m0o}{name}{m0c}!")]


# -- what registers nothing ----------------------------------------------------------------------


@pytest.mark.parametrize("path", ["page", "block"])
def test_VAR3_a_unit_of_only_markers_registers_nothing_and_renders_as_it_was(path):
    html = "<p><!--ls:name-->Ana<!--/ls--></p>"
    client, patched = client_with({})
    with patched:
        out = (body_of(client.translate_page(page(html), category="UI")) if path == "page"
               else client.translate_content_block(html, "UI"))
        assert registered(client) == []
        assert out == html
        if path == "page":
            client.translate_page(page("<p>Hi <!--ls:name-->Ana<!--/ls--></p>"), category="UI")
        else:
            client.translate_content_block("<p>Hi <!--ls:name-->Ana<!--/ls--></p>", "UI")
    assert registered(client) == [("phrase", "Hi {name}")], "control: text beside a marker registers"


@pytest.mark.parametrize("path", ["page", "block"])
@pytest.mark.parametrize("marker", [
    '<span data-ls-param="User.Name">Ana</span>', "<!--ls:Name-->Ana<!--/ls-->",
    "<!--ls:m0o-->Ana<!--/ls-->", "<!--ls:-->Ana<!--/ls-->"],
    ids=["dotted", "uppercase", "markup-token", "empty"])
def test_VAR7_a_value_that_cannot_be_named_registers_nothing_and_is_reported_once(caplog, path, marker):
    html = f"<p>Hello {marker} today</p>"
    client, patched = client_with({})
    with caplog.at_level(logging.DEBUG, logger="langsys"), patched:
        for _ in range(2):
            out = (body_of(client.translate_page(page(html), category="UI")) if path == "page"
                   else client.translate_content_block(html, "UI"))
            assert out == html
    assert registered(client) == []
    notices = [r for r in caplog.records if "VAR-7" in r.getMessage()]
    assert len(notices) == 1
    control, patched = client_with({})
    with patched:
        control.translate_content_block("<p>Hello <!--ls:name-->Ana<!--/ls--> today</p>", "UI")
    assert registered(control) == [("phrase", "Hello {name} today")], "control: a named value registers"


def test_VAR3_text_shaped_like_a_sentinel_stays_text():
    lookalike = "" + "0" * 32 + ":0"
    client, patched = client_with({"UI": {"Hello {name}": f"Ciao {{name}} {lookalike}"}})
    with patched:
        out = body_of(client.translate_page(page("<p>Hello <!--ls:name-->Ana<!--/ls--></p>"), category="UI"))
    assert out == f"<p>Ciao <!--ls:name-->Ana<!--/ls--> {lookalike}</p>"


def test_VAR3_an_unmarked_page_is_unchanged_and_registers_the_text_it_renders():
    html = "<p>Hello <!-- note -->Ana, welcome back</p>"
    client, patched = client_with({})
    with patched:
        out = body_of(client.translate_page(page(html), category="UI"))
    assert out.endswith(">Hello <!-- note -->Ana, welcome back</p>"), out
    assert registered(client) == [("block", ("Hello", "Ana, welcome back"))]
