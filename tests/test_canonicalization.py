"""TOK-1..5 and MARK-1/2 — canonicalization and identity stamping.

Anchored on `tests/fixtures/canonicalization-reference.json`, authored in
langsys-js-typescript and vendored here. **Pinned by git blob SHA, not by path** —
content-addressed, verified locally with no network, and it survives the source branch
being deleted. A live ref records provenance; one string never does both jobs.

Rows are compared by **codepoints**, not display strings: several cases differ only by
`U+00A0` or `U+2028`, which render identically to a space, so a mismatch printed as two
apparently identical strings with two different hashes is unreadable.

Characters are written as **escapes, never literals** (TOK-2 says so explicitly): a
literal `U+00A0` looks exactly like the spaces beside it, so a reviewer cannot see what
the test is about and anyone tidying whitespace silently turns it into an assertion
about ordinary spaces that still passes.

SCOPE — this file carries the v8 identity rows and the shared 26-row fixture, and is
EXTENDED, not replaced, by `test_spec_801.py`, which holds the 8.0.1 re-row: math
excluded, svg text translated on every path, the enumerated whitespace set, `%name%`
normalised at capture, and the CONF-1 every-path register/lookup pairs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from langsys.html.attributes import DEFAULT_TRANSLATABLE_ATTRIBUTES
from langsys.interpolate import interpolate
from langsys.registration import generate_custom_id

pytest.importorskip("lxml")
from langsys.html.parser import extract_phrases  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "canonicalization-reference.json"

#: THE CHECK — content-addressed.
SOURCE_BLOB_SHA = "34034931872b93e761faea49fb040f3fd8a6b9f5"
#: THE PROVENANCE — a live ref.
SOURCE_REF = "langsys-js-typescript a639ae8 tests/fixtures/canonicalization-reference.json"
#: What the fixture was AUTHORED against (langsys2 f5568b88, specVersion 8.2.15).
FIXTURE_SPEC_BASIS = "b9fd4b5b1c15f7ba29656d550dca1f06013327c0"
#: What this SDK is FILED against (langsys2 a95af2c2, specVersion 8.2.18). The TOK and MARK
#: sections are byte-identical between the two, so every row binds the target unchanged.
TARGET_SPEC_BLOB = "5d7e6890b733a50fb6f5f5c30e0056c6ef7bcf45"

_DOC = json.loads(FIXTURE.read_text(encoding="utf-8"))
ROWS = _DOC["cases"]


def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324


def cps(text: str) -> list[str]:
    return [f"U+{ord(c):04X}" for c in text]


# -- fixture integrity, before anything is tokenized --------------------------


def test_the_vendored_fixture_is_the_pinned_blob():
    assert _git_blob_sha(FIXTURE.read_bytes()) == SOURCE_BLOB_SHA, (
        f"vendored fixture no longer matches {SOURCE_BLOB_SHA} (from {SOURCE_REF}). "
        "Re-vendor deliberately; never hand-edit this copy."
    )


def test_the_fixture_declares_the_spec_it_was_authored_against():
    """The fixture names its spec; where that is not this SDK's target the difference is recorded
    here (with the TOK and MARK text checked identical), and the pin fails the day it changes."""
    assert FIXTURE_SPEC_BASIS in _DOC["spec_blob"], _DOC["spec_blob"]
    assert TARGET_SPEC_BLOB not in _DOC["spec_blob"], "the fixture now declares the target; update"


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["id"])
def test_codepoints_first_the_html_survived_vendoring(row):
    """Rebuild each input from its codepoints before anything is hashed. A vendoring
    pipeline that normalised `U+00A0` away would otherwise be agreed with downstream."""
    rebuilt = "".join(chr(int(p[2:], 16)) for p in row["html_codepoints"])
    assert rebuilt == row["html"]


# -- the 19 rows --------------------------------------------------------------


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["id"])
def test_tokens_match_the_fixture(row):
    produced = extract_phrases(row["html"])
    assert produced == row["expected_tokens"], (
        f"\n  expected cps: {[cps(t) for t in row['expected_tokens']]}"
        f"\n  produced cps: {[cps(t) for t in produced]}"
    )


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["id"])
def test_custom_id_matches_the_fixture(row):
    assert generate_custom_id(row["category"], extract_phrases(row["html"])) == (
        row["expected_custom_id"]
    )


# -- TOK-1 --------------------------------------------------------------------
#
# The control is the whole test: without a phrase that must survive, an implementation
# that tokenizes nothing at all passes.

TOK1_EXCLUDED = ["script", "style", "noscript", "math"]


@pytest.mark.parametrize("tag", TOK1_EXCLUDED)
def test_TOK1_excluded_elements_produce_no_token_but_ordinary_markup_does(tag):
    html = f"<div><{tag}>Enable JavaScript</{tag}><p>Plans</p></div>"
    assert extract_phrases(html) == ["Plans"]


def test_TOK1_the_control_alone_produces_the_phrase():
    """Positive control for the three above."""
    assert extract_phrases("<div><p>Plans</p></div>") == ["Plans"]


def test_TOK1_one_document_carrying_all_three_yields_exactly_one_phrase():
    """The spec's own test: the same sentence inside a script, a style and a noscript,
    plus once in ordinary markup. Exactly one phrase, and it is the ordinary one."""
    html = (
        "<div><script>Plans</script><style>Plans</style>"
        "<noscript>Plans</noscript><p>Plans</p></div>"
    )
    assert extract_phrases(html) == ["Plans"]


def test_TOK1_template_content_produces_no_token():
    """`<template>` is a no-op for walkers that never reach fragment content — but lxml
    is not one of those. It parses template children into the ordinary tree, so this is
    a live vector here rather than the free pass the rule allows browsers."""
    assert extract_phrases("<div><template><p>Tmpl</p></template><p>Plans</p></div>") == (
        ["Plans"]
    )


def test_TOK1_does_not_exclude_svg_on_the_block_path():
    """8.0.1 makes svg text translatable on every path, so it is not excluded here - and the
    page path now handles it too, rather than skipping it at the top level. Both paths are
    asserted in `test_spec_801.py`; a single-path proof is what CONF-1 now forbids."""
    assert extract_phrases("<svg><text>Label</text></svg>") == ["Label"]


# -- TOK-2 (host-language whitespace, the revision in force here) -------------


def test_TOK2_nbsp_collapses_like_a_space():
    assert extract_phrases("<p>Buy\u00a0now</p>") == extract_phrases("<p>Buy now</p>")


@pytest.mark.parametrize("html", ["<p>\u00a0Buy now</p>", "<p>Buy now\u00a0</p>"])
def test_TOK2_leading_and_trailing_nbsp_are_trimmed(html):
    """The half-fix detector: a fix applied to the collapse alone passes the internal
    pair and still retains a leading one."""
    assert extract_phrases(html) == ["Buy now"]


@pytest.mark.parametrize(
    "html", ["<p>\u00a0</p>", "<p> </p>", "<p>\u2028</p>", "<p> \u00a0\t\n</p>"]
)
def test_TOK2_a_whitespace_only_node_produces_no_token(html):
    """The count case, and the one that moves block ids: a block's id derives from its
    phrases in order, so disagreeing about whether a node produces a token at all
    changes the array length and re-keys every block containing one."""
    assert extract_phrases(html) == []


def test_TOK2_control_a_real_character_difference_is_two_ids():
    assert generate_custom_id("", ["Buy now"]) != generate_custom_id("", ["Buy new"])


# -- TOK-3 --------------------------------------------------------------------


def test_TOK3_the_attribute_list_is_the_twenty_seven_in_order():
    # Deliberately one transcribed string rather than a list literal: it is laid out to
    # be diffed by eye against the rule's own prose, which is how a transcription error
    # in a 27-item ordered list actually gets caught.
    expected = (  # noqa: SIM905
        "placeholder alt title label aria-label aria-placeholder aria-description "
        "aria-valuetext aria-roledescription data-error data-error-message "
        "data-validation-message data-invalid-message data-required-message "
        "data-pattern-message data-confirm data-tooltip data-title data-content "
        "data-original-title data-bs-title data-bs-content data-loading-text "
        "data-success-message data-warning-message data-empty-message data-placeholder"
    ).split()
    assert list(DEFAULT_TRANSLATABLE_ATTRIBUTES) == expected


def test_TOK3_order_decides_the_sequence_and_unlisted_attributes_produce_nothing():
    """An implementation with the same set in a different order agrees on every
    single-attribute element and diverges on exactly the ones hardest to notice."""
    html = (
        '<input placeholder="P" title="T" alt="A" '
        'data-nope="N" name="ignored" value="V" type="text">'
    )
    assert extract_phrases(html) == ["P", "A", "T"]


# -- TOK-4 --------------------------------------------------------------------


def test_TOK4_attribute_values_collapse_internal_whitespace_like_text_nodes():
    """The same authored content must yield the same id in a text node and an
    attribute, or the tooltip and the sentence translate separately."""
    from_text = extract_phrases("<p>Buy   now</p>")
    from_attr = extract_phrases('<span title="Buy   now"></span>')
    assert from_text == from_attr == ["Buy now"]
    assert generate_custom_id("", from_text) == generate_custom_id("", from_attr)


def test_TOK4_attribute_nbsp_collapses_too():
    assert extract_phrases('<span title="Buy\u00a0now"></span>') == ["Buy now"]


# -- TOK-5 --------------------------------------------------------------------


def test_TOK5_both_placeholder_forms_interpolate_the_same_argument():
    """`{` is not inert in a template compiler, so an author whose build eats `{name}`
    needs a form that survives it — and the fleet must read that form back."""
    assert interpolate("Hi {name}", {"name": "Ada"}, "en-US") == "Hi Ada"
    assert interpolate("Hi %name%", {"name": "Ada"}, "en-US") == "Hi Ada"


def test_TOK5_an_unrecognised_form_is_left_literal_rather_than_dropped():
    """ICU-4's observability requirement reaching this rule: a silently removed slot is
    undiagnosable, a visible one costs a bug report."""
    assert interpolate("Hi <name>", {"name": "Ada"}, "en-US") == "Hi <name>"


def test_TOK5_a_percent_form_with_no_argument_stays_visible():
    assert interpolate("Hi %name%", {}, "en-US") == "Hi %name%"


def test_TOK5_bare_percents_are_not_mistaken_for_a_slot():
    """Control: `%` is ordinary text far more often than it is a delimiter."""
    assert interpolate("100% of {n}", {"n": "cases"}, "en-US") == "100% of cases"


# -- CID-2 --------------------------------------------------------------------


def test_CID2_the_sentinel_is_a_lookup_namespace_never_a_hash_input():
    """Pinned against the fixture rather than asserted: `category-empty` and
    `category-sentinel` are two rows that must produce the same id."""
    empty = next(r for r in ROWS if r["id"] == "category-empty")
    sentinel = next(r for r in ROWS if r["id"] == "category-sentinel")
    assert sentinel["expected_custom_id"] == empty["expected_custom_id"]
    assert generate_custom_id("__uncategorized__", ["Plans"]) == generate_custom_id(
        "", ["Plans"]
    )
    assert generate_custom_id(None, ["Plans"]) == generate_custom_id("", ["Plans"])


# -- MARK-1 / MARK-2 ----------------------------------------------------------


def _stub_client(catalog):
    from unittest.mock import patch

    from langsys import LangsysClient
    from langsys.cache import MemoryCache
    from langsys.catalog import CatalogFetch

    client = LangsysClient(
        "k", "p", api_url="http://x.invalid/api", cache=MemoryCache(),
        base_locale="en-us", debounce=0, auto_flush=False,
    )
    return client, patch.object(
        client._catalog, "get", return_value=CatalogFetch(catalog, ok=True)
    )


BLOCK_HTML = "<div><p>Hello there</p><p>Second line</p></div>"


def test_MARK1_a_rendered_block_carries_the_id_it_was_rendered_from():
    """Two paths to one value. Reading back the attribute the renderer just wrote
    proves only that the attribute was written — so the expectation is re-derived
    independently, by running the tokenizer over the same subtree."""
    import lxml.html as LH

    independent_id = generate_custom_id("CAT", extract_phrases(BLOCK_HTML))
    client, patched = _stub_client(
        {"CAT": {independent_id: {"Hello there": "Hola", "Second line": "Segunda"}}}
    )
    with patched:
        rendered = client.translate_content_block(BLOCK_HTML, category="CAT")

    host = LH.fragment_fromstring(rendered)
    assert host.get("data-ls-contentblock") == independent_id
    assert "Hola" in rendered  # control: it really did render the translation


def test_MARK1_the_stamp_is_the_ls_spelling_not_the_langsys_one():
    """Writers emit `data-ls-*`; only readers accept both."""
    independent_id = generate_custom_id("CAT", extract_phrases(BLOCK_HTML))
    client, patched = _stub_client(
        {"CAT": {independent_id: {"Hello there": "Hola", "Second line": "Segunda"}}}
    )
    with patched:
        rendered = client.translate_content_block(BLOCK_HTML, category="CAT")
    assert "data-ls-contentblock" in rendered
    assert "data-langsys-contentblock" not in rendered


def test_MARK1_an_untranslated_block_is_still_stamped():
    """The id is what the block IS, not what happened to be in the catalog. An
    unstamped miss is the case you most need to inspect."""
    import lxml.html as LH

    client, patched = _stub_client({"CAT": {}})
    with patched:
        rendered = client.translate_content_block(BLOCK_HTML, category="CAT")
    host = LH.fragment_fromstring(rendered)
    assert host.get("data-ls-contentblock") == generate_custom_id(
        "CAT", extract_phrases(BLOCK_HTML)
    )


@pytest.mark.parametrize("spelling", ["data-ls-category", "data-langsys-category"])
def test_MARK2_both_category_spellings_are_read(spelling):
    import lxml.html as LH

    from langsys.html.page import _effective_category

    el = LH.fragment_fromstring(f'<div {spelling}="Blog"><p>Hi</p></div>')
    assert _effective_category(el, None, {}) == "Blog"


# -- MARK-2, the spec's own test: on a PAGE and on a BLOCK --------------------
#
# The earlier MARK-2 tests exercised two helper functions. A helper that answers
# correctly proves nothing about a walker that never asks it, which is exactly what was
# happening: no phrase-host reader existed on either path.

PAGE_WITH_JS_HOST = (
    "<html><body>"
    '<p>Intro <span data-ls-phrase="abc123">Hello</span> end</p>'
    "<div><p>Outer</p><p>Second</p></div>"
    "</body></html>"
)


def _client_with_catalog(catalog):
    from unittest.mock import patch

    from langsys import LangsysClient
    from langsys.cache import MemoryCache
    from langsys.catalog import CatalogFetch

    client = LangsysClient(
        "k", "p", api_url="http://x.invalid/api", cache=MemoryCache(),
        base_locale="en-us", debounce=0, auto_flush=False,
    )
    return client, patch.object(
        client._catalog, "get", return_value=CatalogFetch(catalog, ok=True)
    )


@pytest.mark.parametrize("spelling", ["data-ls-phrase", "data-langsys-phrase"])
def test_MARK2_the_mirror_case_both_spellings_on_the_block_path(spelling):
    """Run the mirror too, or the rule is proven in one direction and asserted in the
    other."""
    html = f'<p>Intro <span {spelling}="abc123">Hello</span> end</p>'
    assert extract_phrases(html) == ["Intro", "end"]


def test_MARK2_excision_not_merely_non_registration():
    """The host's text must not reach the parent's phrase list AT ALL. A block's id
    derives from its phrases in order, so harvesting an already-identified host would
    shift the id of the block containing it — re-keying a block whose own content never
    changed."""
    marked = '<div><p>A</p><span data-ls-phrase="x">B</span><p>C</p></div>'
    without = "<div><p>A</p><p>C</p></div>"
    assert extract_phrases(marked) == extract_phrases(without) == ["A", "C"]
    assert generate_custom_id("CAT", extract_phrases(marked)) == generate_custom_id(
        "CAT", extract_phrases(without)
    )


@pytest.mark.parametrize("spelling", ["data-ls-contentblock", "data-langsys-contentblock"])
def test_MARK2_a_nested_content_block_host_is_left_alone_on_the_block_path(spelling):
    """Both spellings: an identity written by either SDK is excised on the block path. The first
    version carried only the JS spelling, and dropping the PHP one from the block path's reader
    turned nothing red."""
    html = f'<div><div {spelling}="abc"><p>Inner</p></div><p>Outer</p></div>'
    assert extract_phrases(html) == ["Outer"]


def test_MARK2_control_an_unmarked_span_is_still_harvested():
    """Without this, an implementation that excised every span would pass every row
    above."""
    assert extract_phrases("<p>Intro <span>Hello</span> end</p>") == [
        "Intro", "Hello", "end"
    ]


def test_MARK2_no_phrase_is_queued_for_the_host_text_on_the_block_path():
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_content_block(
            '<div><p>Outer</p><span data-ls-phrase="x">Hosted</span></div>', category="CAT"
        )
    for block in client.pending_content_blocks:
        assert "Hosted" not in block["phrases"], block["phrases"]


# -- MARK-1 on the page path --------------------------------------------------


def test_MARK1_page_rendered_blocks_are_stamped_too():
    """A stamp on one path and not the other makes the identity inspectable only on
    whichever path the customer did not use.

    The markup is a leaf block with inline children, which is what actually produces a
    content block on the page path. A container of block-level children is walked into
    and yields simple phrases instead — an earlier version of this test used one and
    asserted a stamp that could never appear."""
    client, patched = _client_with_catalog({})
    with patched:
        out = client.translate_page(
            "<html><body><p>Hello <b>there</b> friend</p></body></html>", category="CAT"
        )
    assert client.pending_content_blocks, "control: no block was produced to stamp"
    expected = client.pending_content_blocks[0]["custom_id"]
    assert f'data-ls-contentblock="{expected}"' in out


def test_MARK1_a_declared_block_host_is_stamped_with_its_derived_id():
    """A `data-langsys-contentblock` authoring marker is a request to treat the subtree
    as one block, not an identity. It gets the id derived from its content, alongside
    the marker it was declared with."""
    client, patched = _client_with_catalog({})
    with patched:
        out = client.translate_page(
            '<html><body><div data-langsys-contentblock="1"><p>A</p><p>B</p></div>'
            "</body></html>",
            category="CAT",
        )
    assert client.pending_content_blocks
    assert f'data-ls-contentblock="{client.pending_content_blocks[0]["custom_id"]}"' in out


# -- TOK-5: the injection, and the identifier guard ---------------------------


def test_TOK5_a_parameter_value_cannot_reach_another_parameter():
    """The escape is resolved on the TEMPLATE, never on rendered output. Rewriting the
    output re-scans substituted values, so a user-supplied value containing `%other%`
    would pull in an argument it was never given. `{name}` never had that exposure
    because substitution happens once; the escape has to match it."""
    assert interpolate("{a}", {"a": "%b%", "b": "INJECTED"}, "en-US") == "%b%"


def test_TOK5_control_the_brace_form_has_the_same_property():
    """The control that makes the assertion above mean something: `{b}` inside a value
    is already left literal, so the escape is being held to the existing standard."""
    assert interpolate("{a}", {"a": "{b}", "b": "INJECTED"}, "en-US") == "{b}"


def test_TOK5_a_value_containing_a_percent_pair_survives_verbatim():
    assert interpolate("{a}", {"a": "100% of 50%"}, "en-US") == "100% of 50%"


@pytest.mark.parametrize(
    "prose", ["100% of 50%", "%a b%", "50%-75% off", "%  %", "%1st%"]
)
def test_TOK5_two_percent_prose_is_not_read_as_a_slot(prose):
    """The identifier guard. Without a vector carrying TWO percent signs, loosening the
    pattern to `%([^%]+)%` passes the whole suite — the single-`%` control cannot see
    the difference."""
    assert interpolate(prose, {"a": "X", "b": "X", "n": "X"}, "en-US") == prose


# -- the vectors that actually reach each guard -------------------------------
#
# Three of the fixes above were first written with tests that could not fail. Each is
# kept here as the discriminating vector, because "the fix is in" and "a test can see
# the fix" are different claims and only the second is worth anything.


def test_TOK5_a_non_identifier_name_is_not_a_slot_even_when_it_is_a_parameter():
    """The identifier guard's discriminating vector.

    Prose vectors cannot see a loosened pattern, because substitution is gated on the
    name being a supplied argument: a loose match on text that is not a parameter
    changes nothing and the assertion stays green. The difference is only visible when
    the loosely-matched name IS a parameter — so the guard is what stops `%a b%` from
    becoming a slot, and that is what this pins."""
    assert interpolate("%a b%", {"a b": "SUBSTITUTED"}, "en-US") == "%a b%"


def test_MARK1_the_stamp_does_not_re_serialise_the_markup():
    """Byte preservation on the miss path.

    The stamp is injected into the original string rather than written to a parse tree
    and serialised back. Round-tripping through lxml is not lossless for markup we were
    only asked to identify: entities decode, void tags lose their slash, and attribute
    quoting is normalised. On a miss the caller is handed back its own markup, so every
    one of those changes would be ours to explain and none was asked for."""
    from langsys.html.parser import stamp_content_block

    cases = [
        ("<div><p>Buy&nbsp;now</p></div>", "&nbsp;"),
        ("<div><p>A<br/>B</p></div>", "<br/>"),
        ("<div><p>caf&eacute;</p></div>", "&eacute;"),
        ("<div><input value='x' class=a></div>", "value='x'"),
    ]
    for markup, must_survive in cases:
        stamped = stamp_content_block(markup, "abc123")
        assert must_survive in stamped, f"{must_survive!r} was re-serialised: {stamped}"
        assert 'data-ls-contentblock="abc123"' in stamped
        assert stamped.replace(' data-ls-contentblock="abc123"', "") == markup


def test_MARK1_a_self_closing_root_is_stamped_inside_its_own_tag():
    from langsys.html.parser import stamp_content_block

    assert stamp_content_block('<img src="a.png" alt="Hi"/>', "id1") == (
        '<img src="a.png" alt="Hi" data-ls-contentblock="id1"/>'
    )


# -- MARK-2 on the page path: an identity is not a declaration ----------------


JS_STAMPED_PAGE = (
    "<html><body>"
    '<div data-ls-contentblock="deadbeefdeadbeefdeadbeefdeadbeef">'
    "<p>Hello <b>x</b></p><p>Second</p></div>"
    "<p>Other <i>y</i></p>"
    "</body></html>"
)


def test_MARK2_a_js_stamped_block_host_is_not_re_registered_on_the_page_path():
    """The failure MARK-2's *Why* names, on the block half rather than the phrase half.

    A `<Translate>` host rendered by the TypeScript core carries its resolved id. This
    SDK also lets an author *declare* a block with a truthy flag, and treating any
    non-empty value as a declaration meant a foreign identity was read as a request:
    the subtree was re-tokenized, re-keyed under this page's category, queued as a new
    block, and the other SDK's stamp overwritten. One block, two ids."""
    client, patched = _client_with_catalog({})
    with patched:
        out = client.translate_page(JS_STAMPED_PAGE, category="CAT")

    block_phrases = [t for b in client.pending_content_blocks for t in b["phrases"]]
    assert "Hello" not in block_phrases, f"re-registered: {block_phrases}"
    assert "Second" not in block_phrases, f"re-registered: {block_phrases}"
    assert 'data-ls-contentblock="deadbeefdeadbeefdeadbeefdeadbeef"' in out, (
        "the foreign stamp was overwritten with our own id"
    )
    assert "Other" in block_phrases, "control: ordinary blocks must still be discovered"



# -- MARK-1: the stamp must not corrupt the markup it is inserted into --------


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<p title="a>b">Hi</p>', '<p title="a>b" data-ls-contentblock="id1">Hi</p>'),
        ("<p title='a>b'>Hi</p>", "<p title='a>b' data-ls-contentblock=\"id1\">Hi</p>"),
        (
            '<!-- <b>note</b> --><p>Hi</p>',
            '<!-- <b>note</b> --><p data-ls-contentblock="id1">Hi</p>',
        ),
    ],
    ids=["gt-in-double-quotes", "gt-in-single-quotes", "leading-comment"],
)
def test_MARK1_the_stamp_honours_quotes_and_comments(markup, expected):
    """A `>` inside an attribute value is legal and ordinary — a `title`, a `data-*`
    holding JSON — and a pattern that stops at the first `>` inserts the attribute into
    the middle of that value. The result is not merely different markup, it is broken
    markup. A comment before the host can contain markup of its own, so the first
    `<tag` in the string is not necessarily the host's."""
    from langsys.html.parser import stamp_content_block

    assert stamp_content_block(markup, "id1") == expected


# -- MARK-2: the excision is symmetric ----------------------------------------


def test_MARK2_a_marked_host_is_not_rewritten_either():
    """DECISION, tested rather than left implicit: a host we refuse to tokenize is also
    one we refuse to rewrite.

    Its text is not in our phrase list, so a translation applied to it is one keyed to a
    *sibling's* phrase that happened to read the same — and the SDK owning the host
    re-renders it regardless, so the write is both wrong and temporary. It only bites
    when the two texts coincide, which is exactly when it is hardest to notice."""
    from langsys.html.parser import apply_block_translations

    out = apply_block_translations(
        '<p>Intro <span data-ls-phrase="x">Hello</span> end</p>',
        {"Hello": "HOLA", "Intro": "INTRO"},
    )
    assert ">Hello<" in out, f"the foreign host's text was rewritten: {out}"
    assert "INTRO" in out, "control: our own text must still be translated"


# -- the block attribute has three meanings, classified in ONE place ----------
#
# Introduced as a regression and caught in review: the page walker gained a helper that
# read anything not a declaration flag as a foreign identity — including `""`, `"0"`,
# `"false"` and the bare attribute — and excised those subtrees from discovery
# entirely, while a second helper eight lines below still called the same values "not a
# declaration, walk normally". Two readers, one attribute, opposite answers.
#
# A resolved custom_id is never empty, `0` or `false`, so reading them as identities
# gained nothing and cost the content its registration.


# -- MARK-2: a phrase host is recognised, and registers whole ------------------------------------


def test_MARK2_a_js_rendered_phrase_host_is_not_re_split_on_the_page_path():
    """The spec's test: the host is recognised, and its text is never registered a second time
    under a different split. A host that misses registers WHOLE, as the one string it defines."""
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_page(PAGE_WITH_JS_HOST, category="CAT")
    queued = [p["phrase"] for p in client.pending_phrases]
    block_phrases = [t for b in client.pending_content_blocks for t in b["phrases"]]
    assert queued.count("Hello") == 1, f"not registered once, whole: {queued}"
    assert "Hello" not in block_phrases, f"re-split into the enclosing block: {block_phrases}"
    assert ["Intro", "end"] in [b["phrases"] for b in client.pending_content_blocks]


@pytest.mark.parametrize("spelling", ["data-ls-phrase", "data-langsys-phrase"])
def test_MARK2_a_phrase_host_with_markup_registers_as_one_string(spelling):
    """`<p data-langsys-phrase>Based on {n} <strong>reviews</strong></p>` is one catalog entry by
    design, in the JS <Phrase> wire format, so the count and its noun stay in one phrase."""
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_page(
            f"<html><body><p {spelling}>Based on {{n}} <strong>reviews</strong></p></body></html>",
            category="CAT",
        )
    assert client.pending_phrases == [
        {"phrase": "Based on {n} {m0o}reviews{m0c}", "category": "CAT"}
    ]
    assert client.pending_content_blocks == []


def test_MARK2_a_phrase_host_translation_keeps_its_markup_where_the_tokens_now_sit():
    catalog = {"CAT": {"Based on {n} {m0o}reviews{m0c}": "{m0o}Recensioni{m0c}: {n}"}}
    client, patched = _client_with_catalog(catalog)
    with patched:
        out = client.translate_page(
            '<html><body><p data-ls-phrase>Based on {n} <strong class="c">reviews</strong></p>'
            "</body></html>",
            category="CAT",
        )
    assert '<strong class="c">Recensioni</strong>: {n}' in out, out


def test_MARK2_a_phrase_host_marked_false_is_ordinary_markup():
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_page(
            '<html><body><p data-ls-phrase="false">One <b>two</b></p></body></html>', category="CAT"
        )
    assert [b["phrases"] for b in client.pending_content_blocks] == [["One", "two"]]


# -- MARK-3: bare or truthy declares, false/0 opts out, anything else is an identity -------------

DECLARATIONS = ["", '=""', '="true"', '="1"', '="YES"']
OPT_OUTS = ['="0"', '="false"', '="FALSE"']
DECL_IDS = ["bare", "empty", "true", "1", "YES"]
BLOCK_SPELLINGS = ["data-ls-contentblock", "data-langsys-contentblock"]
DECLARED_BODY = "<p>Hello there</p>"


@pytest.mark.parametrize("spelling", BLOCK_SPELLINGS)
@pytest.mark.parametrize("suffix", DECLARATIONS, ids=DECL_IDS)
def test_MARK3_a_declaration_registers_one_block_with_the_same_id_on_the_page_path(suffix, spelling):
    """The one attribute an author writes to ask for a block. A single-phrase body stays a block:
    a declaration outranks the TOK-6 shape."""
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_page(
            f"<html><body><div {spelling}{suffix}>{DECLARED_BODY}</div></body></html>", category="CAT"
        )
    assert client.pending_phrases == []
    assert [b["custom_id"] for b in client.pending_content_blocks] == [
        generate_custom_id("CAT", ["Hello there"])
    ]


@pytest.mark.parametrize("spelling", BLOCK_SPELLINGS)
@pytest.mark.parametrize("suffix", DECLARATIONS, ids=DECL_IDS)
def test_MARK3_a_declaration_registers_one_block_on_the_block_path(suffix, spelling):
    client, patched = _client_with_catalog({})
    with patched:
        out = client.translate_content_block(f"<div {spelling}{suffix}>{DECLARED_BODY}</div>", "CAT")
    expected = generate_custom_id("CAT", ["Hello there"])
    assert [b["custom_id"] for b in client.pending_content_blocks] == [expected]
    assert f'data-ls-contentblock="{expected}"' in out


@pytest.mark.parametrize("spelling", BLOCK_SPELLINGS)
@pytest.mark.parametrize("suffix", OPT_OUTS, ids=["0", "false", "FALSE"])
def test_MARK3_an_opt_out_registers_the_content_as_its_units_would(suffix, spelling):
    """Without the attribute this markup is a container of two phrase units."""
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_page(
            f"<html><body><div {spelling}{suffix}><p>Hello</p><p>Second</p></div></body></html>",
            category="CAT",
        )
    assert [p["phrase"] for p in client.pending_phrases] == ["Hello", "Second"]
    assert client.pending_content_blocks == []


@pytest.mark.parametrize("spelling", BLOCK_SPELLINGS)
@pytest.mark.parametrize("value", ["abc123", "no", "off"])
def test_MARK3_any_other_value_is_an_identity_that_renders_and_registers_nothing(value, spelling):
    """`no` and `off` are identities now, not opt-outs: only `false` and `0` opt out."""
    catalog = {"CAT": {value: {"Hello": "Ciao"}}}
    client, patched = _client_with_catalog(catalog)
    with patched:
        out = client.translate_page(
            f'<html><body><div {spelling}="{value}"><p>Hello</p></div></body></html>', category="CAT"
        )
    assert client.pending_phrases == [] and client.pending_content_blocks == []
    assert "<p>Ciao</p>" in out, "the host did not render the catalog entry under its id"


def test_MARK3_an_identity_with_no_catalog_entry_keeps_its_source():
    client, patched = _client_with_catalog({"CAT": {}})
    with patched:
        out = client.translate_page(
            '<html><body><div data-ls-contentblock="abc123"><p>Hello</p></div></body></html>',
            category="CAT",
        )
    assert "<p>Hello</p>" in out
    assert client.pending_phrases == [] and client.pending_content_blocks == []


def test_MARK3_an_identity_on_the_block_path_renders_under_its_id():
    client, patched = _client_with_catalog({"CAT": {"abc123": {"Hello": "Ciao"}}})
    with patched:
        out = client.translate_content_block('<div data-ls-contentblock="abc123"><p>Hello</p></div>', "CAT")
    assert "<p>Ciao</p>" in out and client.pending_content_blocks == []


def test_MARK3_the_classifier_table():
    from langsys.html.attributes import classify_block_attribute

    table = {None: "absent", "": "declaration", "  TRUE ": "declaration", "1": "declaration",
             "Yes": "declaration", "0": "opt-out", " false ": "opt-out", "no": "identity",
             "off": "identity", "on": "identity", "deadbeef": "identity"}
    assert {k: classify_block_attribute(k) for k in table} == table


# -- MARK-4: a marked host inside a walked unit is excised ---------------------------------------

NESTED = (
    '<div><p>Outer text</p><div data-ls-contentblock><p>Inner one</p><p>Inner two</p></div>'
    '<span data-ls-phrase>Phrase host</span><p>Closing</p></div>'
)


@pytest.mark.parametrize("path", ["block", "page"])
def test_MARK4_nested_hosts_are_excised_and_each_registers_once_on_its_own(path):
    client, patched = _client_with_catalog({})
    with patched:
        if path == "block":
            client.translate_content_block(NESTED, "CAT")
        else:
            client.translate_page(f"<html><body><section>{NESTED}</section></body></html>", category="CAT")
    blocks = [b["phrases"] for b in client.pending_content_blocks]
    phrases = [p["phrase"] for p in client.pending_phrases]
    assert blocks.count(["Inner one", "Inner two"]) == 1, blocks
    assert phrases.count("Phrase host") == 1, phrases
    outer = [tokens for tokens in blocks if "Outer text" in tokens]
    for tokens in outer:
        assert "Inner one" not in tokens and "Phrase host" not in tokens, tokens


def test_MARK4_control_an_opted_out_nested_block_folds_into_the_outer_tokens():
    """Without it, a walker that drops every nested element passes the test above."""
    folded = NESTED.replace("data-ls-contentblock>", 'data-ls-contentblock="false">')
    folded = folded.replace("<span data-ls-phrase>Phrase host</span>", "")
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_content_block(folded, "CAT")
    assert [b["phrases"] for b in client.pending_content_blocks] == [
        ["Outer text", "Inner one", "Inner two", "Closing"]
    ]


def test_MARK4_excision_moves_the_outer_id():
    outer = extract_phrases(NESTED)
    assert outer == ["Outer text", "Closing"], outer


# -- TOK-6: the registration shape, on every path -------------------------------------------------

SHAPES = [
    ("<p>Hello</p>", "phrase", ["Hello"]),
    ('<p title="Tooltip">Hello</p>', "block", ["Tooltip", "Hello"]),
    ('<img alt="Logo">', "block", ["Logo"]),
    ('<button data-confirm="Are you sure?">Go</button>', "block", ["Are you sure?", "Go"]),
    ("<p><svg><text>Label</text><path/></svg></p>", "phrase", ["Label"]),
    ("<p>Hello <b>bold</b></p>", "block", ["Hello", "bold"]),  # control
]
SHAPE_IDS = ["one-text-node", "own-attribute", "top-level-void", "top-level-inline", "svg", "control"]


def _registered(client):
    return (
        [("phrase", [p["phrase"]]) for p in client.pending_phrases]
        + [("block", b["phrases"]) for b in client.pending_content_blocks]
    )


@pytest.mark.parametrize(("markup", "shape", "tokens"), SHAPES, ids=SHAPE_IDS)
def test_TOK6_the_page_path_registers_each_unit_in_its_shape(markup, shape, tokens):
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_page(f"<html><body>{markup}</body></html>", category="CAT")
    assert _registered(client) == [(shape, tokens)]


@pytest.mark.parametrize(("markup", "shape", "tokens"), SHAPES, ids=SHAPE_IDS)
def test_TOK6_the_block_path_registers_the_fragment_in_its_shape(markup, shape, tokens):
    """An explicit block call is a unit too: its fragment is a phrase when its one token is its
    one text node."""
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_content_block(markup, "CAT")
    assert _registered(client) == [(shape, tokens)]


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["id"])
def test_TOK6_every_fixture_row_tokenizes_identically_on_the_page_path(row):
    """The block path runs every row above; this is the same 32 rows through translate_page,
    reading what it actually registers."""
    client, patched = _client_with_catalog({})
    with patched:
        client.translate_page(f"<html><body>{row['html']}</body></html>", category=row["category"] or None)
    registered = [t for _, tokens in _registered(client) for t in tokens]
    assert registered == row["expected_tokens"], (
        f"\n  expected cps: {[cps(t) for t in row['expected_tokens']]}"
        f"\n  page cps:     {[cps(t) for t in registered]}"
    )


def test_TOK6_a_phrase_unit_translates_in_place_and_keeps_its_markup():
    client, patched = _client_with_catalog({"CAT": {"Label": "Etichetta"}})
    with patched:
        out = client.translate_page(
            '<html><body><p class="k"><svg><text>Label</text><path d="M0 0"/></svg></p></body></html>',
            category="CAT",
        )
    assert '<text>Etichetta</text><path d="M0 0"></path>' in out, out
    assert 'class="k"' in out


def test_TOK6_a_top_level_void_block_renders_its_attribute():
    tokens = ["Logo"]
    catalog = {"CAT": {generate_custom_id("CAT", tokens): {"Logo": "Marchio"}}}
    client, patched = _client_with_catalog(catalog)
    with patched:
        out = client.translate_page('<html><body><img alt="Logo"></body></html>', category="CAT")
    assert 'alt="Marchio"' in out and client.pending_content_blocks == []


@pytest.mark.parametrize("markup", [
    "<p>A\x1clong description</p><p>Other</p>",
    '<p title="A\x1clong">Hello</p>',
    "<div><p>One\x0b</p><p>Two</p></div>",
], ids=["text", "attribute", "block"])
def test_TOK2_markup_carrying_a_c0_control_renders_instead_of_raising(markup):
    """lxml parses these characters but refuses to ASSIGN them, so an apply that rewrote every
    node raised on any page carrying one - a render path that throws (WIRE-4's harm)."""
    client, patched = _client_with_catalog({"CAT": {"Other": "Altro"}})
    with patched:
        page = client.translate_page(f"<html><body>{markup}</body></html>", category="CAT")
        block = client.translate_content_block(markup, "CAT")
    assert "<body>" in page and block
