"""Spec 8.0.1 re-row — TOK-1, TOK-2, TOK-5, the CONF-1 every-path pairs, SRV-5, template.

Filed against langsys2 `5cff03a17751e7dae9dcf1af52a9454d027c9006`, `docs/sdk-spec.mdx` blob
`5c5c0723f88fb8e6b13f58876c7adca8b6b35691`, specVersion 8.0.1 — committed and unpublished.

Characters are written as escapes, never literals (TOK-2). This file is generated through a
quoted shell heredoc and byte-scanned on write: an earlier file in this suite had its escapes
decoded into invisible literals by the tool that wrote it.

Rows marked `needs_214` pin PARSER behaviour, not this SDK's code. They are labelled with the
libxml2 version because the behaviour moves when a runtime crosses libxml2 2.14, and they skip
rather than pass on an older one: a DOM-level assertion that a character is not collapsed passes
for the wrong reason where the parser dropped the character before the tokenizer ever saw it.
Collapse-set MEMBERSHIP is therefore asserted on `normalize_whitespace` directly, a string in and
a string out, and the DOM rows are secondary.

The 28 C0 controls are removed before anything collapses (TOK-2 as ruled): VT and FF included, TAB,
LF and CR excluded, NUL, U+007F and the C1 range kept.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("lxml")

import lxml.etree as E  # noqa: E402
import lxml.html as LH  # noqa: E402

from langsys import LangsysClient  # noqa: E402
from langsys.cache import MemoryCache  # noqa: E402
from langsys.catalog import CatalogFetch  # noqa: E402
from langsys.html.parser import (  # noqa: E402
    apply_block_translations,
    extract_phrases,
    normalize_whitespace,
)
from langsys.registration import generate_custom_id  # noqa: E402

LIBXML_VERSION = E.LIBXML_VERSION
needs_214 = pytest.mark.skipif(
    LIBXML_VERSION < (2, 14, 0),
    reason=f"parser-behaviour row, labelled for libxml2>=2.14; runtime is {LIBXML_VERSION}",
)


def cps(text):
    return [f"U+{ord(c):04X}" for c in text]


def _client():
    return LangsysClient(
        "k", "p", api_url="http://x.invalid/api", cache=MemoryCache(),
        base_locale="en-us", debounce=0, auto_flush=False,
    )


def render_page(body, catalog=None, head=""):
    client = _client()
    with patch.object(client._catalog, "get", return_value=CatalogFetch(catalog or {}, ok=True)):
        out = client.translate_page(
            f"<html><head>{head}</head><body>{body}</body></html>", category="CAT"
        )
    phrases = [p["phrase"] for p in client.pending_phrases]
    blocks = [b["phrases"] for b in client.pending_content_blocks]
    return out, phrases, blocks


def count_queue_calls(body):
    """Count registration CALLS, not the queue.

    The queue is a dict keyed by (category, phrase), so a duplicate registration collapses
    structurally and a count taken from it cannot exceed one whatever the walker does. SRV-5 asks
    for the count precisely because "the duplicates are identical and a set-based assertion hides
    them" — and this queue is a set-based assertion. The calls are what can repeat.
    """
    client = _client()
    calls = {"phrase": [], "block": []}
    real_phrase, real_block = client._queue_missing, client._queue_content_block

    def spy_phrase(phrase, category, *args, **kwargs):
        calls["phrase"].append(phrase)
        return real_phrase(phrase, category, *args, **kwargs)

    def spy_block(html, category, custom_id, phrases):
        calls["block"].append(tuple(phrases))
        return real_block(html, category, custom_id, phrases)

    client._queue_missing = spy_phrase
    client._queue_content_block = spy_block
    with patch.object(client._catalog, "get", return_value=CatalogFetch({}, ok=True)):
        client.translate_page(f"<html><body>{body}</body></html>", category="CAT")
    return calls


# -- TOK-2: the enumerated set, on the collapse function ----------------------

NON_MEMBERS = [
    ("U+0085", "\x85"),
    ("U+180E", "\u180e"),
    ("U+200B", "\u200b"),
    ("U+2060", "\u2060"),
]
#: TOK-2's strip set, from integers: U+0001-U+0008, U+000B, U+000C, U+000E-U+001F.
STRIPPED = [*range(0x01, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20)]


def test_TOK2_feff_is_a_member_and_collapses():
    """U+FEFF is in JavaScript's \\s and Python's misses it — an under-collapse."""
    assert normalize_whitespace("a\ufeffb") == "a b"


def test_TOK2_feff_is_trimmed_leading_and_trailing():
    """The half-fix detector: a fix to the collapse alone passes the internal case and still
    keeps a leading one, because trimming is a second site."""
    assert normalize_whitespace("\ufeffBuy now\ufeff") == "Buy now"


@pytest.mark.parametrize(("name", "ch"), NON_MEMBERS, ids=[n for n, _ in NON_MEMBERS])
def test_TOK2_non_members_survive_the_collapse(name, ch):
    assert normalize_whitespace("a" + ch + "b") == "a" + ch + "b"


@pytest.mark.parametrize(("name", "ch"), NON_MEMBERS, ids=[n for n, _ in NON_MEMBERS])
def test_TOK2_non_members_survive_trimming(name, ch):
    """U+0085 is the over-collapse: Python's str.strip() removes it as whitespace."""
    assert normalize_whitespace(ch + "Buy" + ch) == ch + "Buy" + ch


def test_TOK2_control_real_members_still_collapse():
    """Without this, a class that collapses nothing passes every non-member row."""
    assert normalize_whitespace("a \t\n\u00a0\u3000b") == "a b"


@pytest.mark.parametrize("cp", STRIPPED, ids=lambda cp: f"U+{cp:04X}")
def test_TOK2_the_28_c0_controls_are_removed_not_mapped_to_a_space(cp):
    assert len(STRIPPED) == 28
    assert normalize_whitespace("a" + chr(cp) + "b") == "ab"


@pytest.mark.parametrize("cp", [0x09, 0x0A, 0x0D], ids=["TAB", "LF", "CR"])
def test_TOK2_tab_lf_and_cr_are_not_stripped_they_collapse(cp):
    assert normalize_whitespace("a" + chr(cp) + "b") == "a b"


@pytest.mark.parametrize("cp", [0x00, 0x7F, 0x85], ids=["NUL", "DEL", "NEL"])
def test_TOK2_nul_del_and_the_c1_range_are_kept(cp):
    assert normalize_whitespace("a" + chr(cp) + "b") == "a" + chr(cp) + "b"


def test_TOK2_the_order_is_strip_then_collapse_then_trim():
    """A control between two spaces leaves a run the collapse still has to see, and a control at
    the edge leaves whitespace the trim still has to see."""
    fs = chr(0x1C)
    assert normalize_whitespace(fs + " a " + fs + " b " + fs) == "a b"


def test_TOK2_a_code_registered_key_is_stripped_on_lookup_and_on_register():
    """`t()` keys are id inputs too: a key carrying a control resolves to the markup's entry, and a
    miss registers the stripped key."""
    fs = chr(0x1C)
    client = _client()
    catalog = {"UI": {"Along": "Lungo"}}
    with patch.object(client._catalog, "get", return_value=CatalogFetch(catalog, ok=True)):
        assert client.translate("A" + fs + "long", category="UI", locale="it-it") == "Lungo"
        client.translate("New" + fs + "phrase", category="UI", locale="it-it")
    assert client.pending_phrases == [{"phrase": "Newphrase", "category": "UI"}]


def test_TOK2_count_case_a_feff_only_node_produces_no_token():
    """The count case, and the first to re-row: a block's id derives from its phrases in order,
    so whether a node produces a token at all changes the id of every block containing it."""
    assert extract_phrases("<p>\ufeff</p>") == []


@needs_214
@pytest.mark.parametrize(("name", "ch"), NON_MEMBERS, ids=[n for n, _ in NON_MEMBERS])
def test_TOK2_count_case_a_non_member_only_node_is_one_token_on_libxml2_2_14(name, ch):
    assert extract_phrases(f"<p>{ch}</p>") == [ch]


@needs_214
def test_TOK2_through_the_dom_on_libxml2_2_14():
    """Secondary, version-labelled: the fixture's own rows, through the parser."""
    assert extract_phrases("<p>A\ufefflong   description</p>") == ["A long description"]
    assert extract_phrases("<p>A\x85long   description</p>") == ["A\x85long description"]


# -- TOK-1: math out, svg in, on every path -----------------------------------

INLINE_ICON = '<p>Click <svg><text>go</text><path d="M0 0"/></svg> to continue</p>'
STANDALONE_SVG = '<svg><text>SvgLabel</text><path d="M0 0"/></svg>'


def test_TOK1_the_spec_document_yields_exactly_the_ordinary_phrase():
    """Same sentence in script, style, noscript and math, plus once in ordinary markup. The
    ordinary-markup control is the whole test."""
    html = (
        "<div><script>Plans</script><style>Plans</style><noscript>Plans</noscript>"
        "<math><mi>Plans</mi></math><p>Plans</p></div>"
    )
    assert extract_phrases(html) == ["Plans"]


def test_TOK1_math_is_excluded_and_the_surrounding_text_survives():
    html = "<p>Area <math><mi>x</mi><mo>+</mo><mn>2</mn></math> units</p>"
    assert extract_phrases(html) == ["Area", "units"]


def test_TOK1_inline_svg_on_the_block_path_keeps_the_blocks_own_text():
    assert extract_phrases(INLINE_ICON) == ["Click", "go", "to continue"]


def test_TOK1_inline_svg_on_the_page_path_yields_the_same_tokens():
    """CONF-1: a page path and a block path over the same markup must agree."""
    _, _, blocks = render_page(INLINE_ICON)
    assert blocks == [["Click", "go", "to continue"]]


def test_TOK1_inline_svg_translates_in_place_with_its_path_intact_on_a_real_render():
    """A nearest-element textContent write gives correct TOKENS and a destroyed drawing, so the
    assertion is on the rendered markup, not the phrase list."""
    cid = generate_custom_id("CAT", extract_phrases(INLINE_ICON))
    out, _, _ = render_page(
        INLINE_ICON, {"CAT": {cid: {"Click": "Pulsa", "go": "ir", "to continue": "para seguir"}}}
    )
    assert '<path d="M0 0">' in out, "the drawing was destroyed"
    assert "<text>ir</text>" in out
    assert "Pulsa" in out and "para seguir" in out


def test_TOK1_a_standalone_svg_is_tokenized_on_the_page_path():
    """The discriminating vector for the page-path half: a page path that skipped svg at the
    top level while the block path tokenized it read as met until something exercised it."""
    _, phrases, blocks = render_page(STANDALONE_SVG)
    assert phrases + [t for b in blocks for t in b] == ["SvgLabel"], (phrases, blocks)


def test_TOK1_a_standalone_svg_translates_in_place_with_its_path_intact():
    out, _, _ = render_page(STANDALONE_SVG, {"CAT": {"SvgLabel": "Etiqueta"}})
    assert "<text>Etiqueta</text>" in out
    assert '<path d="M0 0">' in out, "the drawing was destroyed"


def test_TOK1_svg_only_text_on_the_simple_phrase_route_renders_in_place():
    out, _, _ = render_page('<p><svg><text>Only</text><path d="M0 0"/></svg></p>', {"CAT": {"Only": "Solo"}})
    assert "<text>Solo</text>" in out and '<path d="M0 0">' in out


def test_TOK1_svg_inside_a_declared_block_is_tokenized():
    _, _, blocks = render_page('<div data-langsys-contentblock="1"><p>A</p><svg><text>B</text></svg></div>')
    assert blocks == [["A", "B"]]


def test_TOK1_template_exclusion_is_load_bearing_on_lxml():
    """libxml2 puts <template> children in the ordinary tree, so removing the exclusion changes
    the result on the SAME markup. Asserted by removing it, not by comparing to markup without a
    template — the second shows only that the markup differs."""
    import langsys.html.parser as parser

    html = "<div><template><p>x</p></template><p>y</p></div>"
    assert len(LH.fragment_fromstring(html).find(".//template")) == 1
    assert extract_phrases(html) == ["y"]
    original = parser.SKIP_TAGS
    parser.SKIP_TAGS = frozenset(original - {"template"})
    try:
        assert extract_phrases(html) == ["x", "y"]
    finally:
        parser.SKIP_TAGS = original


# -- TOK-5: normalise %name% at capture, on register AND lookup ---------------


def test_TOK5_percent_form_in_markup_is_captured_as_the_brace_form():
    assert extract_phrases("<p>Hello %name%</p>") == ["Hello {name}"]


def test_TOK5_percent_and_brace_markup_share_one_id():
    assert generate_custom_id("", extract_phrases("<p>Hello %name%</p>")) == generate_custom_id(
        "", extract_phrases("<p>Hello {name}</p>")
    )


def test_TOK5_percent_form_in_an_attribute_is_captured_as_the_brace_form():
    assert extract_phrases('<input placeholder="Hi %name%">') == ["Hi {name}"]


def test_TOK5_capture_and_lookup_agree_so_the_translation_is_found():
    """CONF-1's register/lookup pair: normalising on capture alone registers `Hello {name}` and
    then looks up `Hello %name%`, which misses forever and re-registers every render."""
    out = apply_block_translations("<p>Hello %name%</p>", {"Hello {name}": "Hola {name}"})
    assert "Hola {name}" in out


@pytest.mark.parametrize("prose", ["100% of 50%", "%a b%", "50%-75% off"])
def test_TOK5_prose_percents_are_not_rewritten_at_capture(prose):
    assert extract_phrases(f"<p>{prose}</p>") == [prose]


# -- CONF-1: every register/lookup pair ---------------------------------------


def test_CONF1_an_attribute_is_looked_up_the_way_it_was_registered():
    src = '<p title="Buy   now">x</p>'
    assert extract_phrases(src) == ["Buy now", "x"]
    assert 'title="Comprar"' in apply_block_translations(src, {"Buy now": "Comprar", "x": "X"})


def test_CONF1_an_nbsp_attribute_is_looked_up_the_way_it_was_registered():
    src = '<p title="Buy\u00a0now">x</p>'
    assert 'title="Comprar"' in apply_block_translations(src, {"Buy now": "Comprar", "x": "X"})


def test_CONF1_a_button_value_is_looked_up_the_way_it_was_registered():
    src = '<button value="Save   it">b</button>'
    assert extract_phrases(src) == ["Save it", "b"]
    assert 'value="Guardar"' in apply_block_translations(src, {"Save it": "Guardar", "b": "B"})


def test_CONF1_the_title_route_registers_the_collapsed_phrase():
    _, phrases, _ = render_page("<p>Body</p>", head="<title>  Buy   now  </title>")
    assert "Buy now" in phrases and "Buy   now" not in phrases, phrases


def test_CONF1_the_meta_route_registers_the_collapsed_phrase():
    """The spec names 'a meta path that did nothing to the text at all' — invisible to a search
    for what a path does wrong, because it does nothing."""
    _, phrases, _ = render_page("<p>Body</p>", head='<meta name="description" content="Buy   now">')
    assert "Buy now" in phrases and "Buy   now" not in phrases, phrases


def test_CONF1_a_head_miss_keeps_the_markup_as_authored():
    """TOK-2 governs identity, not output: normalising the registration key must not rewrite the
    text of a title that has no translation yet."""
    out, _, _ = render_page("<p>Body</p>", head="<title>Buy   now</title>")
    assert "<title>Buy   now</title>" in out


# -- SRV-5: once per subtree, counted -----------------------------------------


def test_SRV5_a_depth_3_nested_phrase_is_registered_exactly_once():
    calls = count_queue_calls("<div><div><div><p>Deep miss</p></div></div></div>")
    assert calls["phrase"].count("Deep miss") == 1, calls


def test_SRV5_a_depth_3_nested_block_is_registered_exactly_once():
    calls = count_queue_calls("<div><div><div><p>Deep <b>block</b> miss</p></div></div></div>")
    assert calls["block"].count(("Deep", "block", "miss")) == 1, calls


# -- residuals, parser behaviour, labelled ------------------------------------


@needs_214
@pytest.mark.parametrize(
    ("label", "html", "expected"),
    [
        ("raw-NUL", "<p>a\x00b</p>", ["U+0061", "U+FFFD", "U+0062"]),
        ("ref-0", "<p>a&#0;b</p>", ["U+0061", "U+FFFD", "U+0062"]),
        ("lone-CR", "<p>a\rb</p>", ["U+0061", "U+000A", "U+0062"]),
        ("CRLF", "<p>a\r\nb</p>", ["U+0061", "U+000A", "U+0062"]),
    ],
    ids=["raw-NUL", "ref-0", "lone-CR", "CRLF"],
)
def test_RESIDUAL_parser_text_normalisation_on_libxml2_2_14(label, html, expected):
    assert cps(LH.fragment_fromstring(html).text) == expected


@needs_214
@pytest.mark.parametrize(
    "html", ['<p title="a\x1cb">x</p>', '<p title="a&#x1C;b">x</p>'], ids=["raw", "char-ref"]
)
def test_RESIDUAL_c0_is_kept_in_attributes_on_libxml2_2_14(html):
    assert LH.fragment_fromstring(html).get("title") == "a\x1cb"


def test_RESIDUAL_bytes_without_a_charset_decode_as_latin1():
    """Both call sites pass str. This pins why they must keep doing so."""
    assert LH.fragment_fromstring("<p>a\x85b</p>".encode()).text == "a\xc2\x85b"


# -- parse-model agreement: the PARSER doing the work, labelled ---------------
#
# Vectors from langsys-php-sdk tests/fixtures/parse-model-reference.json, blob
# 741c8cfc7f49dc0eb3242afa30a307880771d9aa (feature/838_write_key_gating_reland), with the
# JS-family answers it records from real Chromium 153 against parse5. The block ids are that
# fixture's `js_family_block_id`, hashed under category `UI` - the category is part of the hash.
#
# These agree because of libxml2 2.14.6, not because of anything this SDK does: PHP on libxml2
# 2.9.13 diverges on both raw-text rows and both foster rows. So the rows skip below 2.14 rather
# than claiming a guarantee an older lxml wheel would silently break.

PARSE_MODEL = [
    ("raw-text-textarea", "<p>Keep</p><textarea>a <b>b</b></textarea>",
     ["Keep", "a <b>b</b>"], "526f61b19c29148767952483d9f5538b"),
    ("raw-text-title", "<title>a <b>b</b></title><p>Keep</p>",
     ["a <b>b</b>", "Keep"], "196856a6c25d7713fbb976c1a1ab0ff5"),
    ("foster-stray-element", "<table><b>stray</b><tr><td>cell</td></tr></table>",
     ["stray", "cell"], "c9a556e320152d0bd3fc239bbb2b6d40"),
    ("foster-loose-text", "<table>loose text<tr><td>x</td></tr></table>",
     ["loose text", "x"], "0a1f1ae92500780beac5287122a3d74c"),
    ("implied-close-p", "<p>one<p>two",
     ["one", "two"], "499d5997c918fd2997c9baea33d52dc6"),
    ("implied-close-li", "<ul><li>one<li>two</ul>",
     ["one", "two"], "499d5997c918fd2997c9baea33d52dc6"),
    ("implied-close-option", "<select><option>one<option>two</select>",
     ["one", "two"], "499d5997c918fd2997c9baea33d52dc6"),
]


@needs_214
@pytest.mark.parametrize(
    ("case_id", "html", "js_tokens", "js_block_id"), PARSE_MODEL, ids=[c[0] for c in PARSE_MODEL]
)
def test_PARSE_MODEL_agrees_with_the_js_family_on_libxml2_2_14(case_id, html, js_tokens, js_block_id):
    tokens = extract_phrases(html)
    assert tokens == js_tokens, f"{case_id}: {tokens} (libxml2 {LIBXML_VERSION})"
    assert generate_custom_id("UI", tokens) == js_block_id


# -- CONF-1: attribute and option LOOKUP sites, pinned per whitespace shape --------------------
#
# The TS lane found attribute and <option> lookups keyed with trim() while registration
# collapsed whitespace, so every lookup silently missed. Each vector registers under the
# collapsed key and must be FOUND under it on the path named. A lookup site reverted to trim() or
# to the raw value turns its rows red. NBSP is built from its codepoint: an escape for it has been
# decoded into an invisible literal in transit before.

NBSP = chr(0xA0)
LOOKUP_SHAPES = {
    "line-break": "A long\n   description",
    "doubled-space": "A long  description",
    "nbsp": "A long" + NBSP + "description",
}
COLLAPSED = "A long description"
TRANSLATED = "Una descrizione lunga"


def _block_catalog(fragment):
    phrases = extract_phrases(fragment)
    assert COLLAPSED in phrases, f"control: registration did not collapse the value: {phrases}"
    entries = {phrase: f"[{phrase}]" for phrase in phrases}
    entries[COLLAPSED] = TRANSLATED
    return {"CAT": {generate_custom_id("CAT", phrases): entries}}


@pytest.mark.parametrize("shape", sorted(LOOKUP_SHAPES))
@pytest.mark.parametrize("tag, attr", [("img", "alt"), ("input", "placeholder")])
def test_CONF1_attribute_lookup_on_the_block_path(tag, attr, shape):
    html = f'<div><p>Label</p><{tag} {attr}="{LOOKUP_SHAPES[shape]}"></div>'
    client = _client()
    catalog = _block_catalog(html)
    with patch.object(client._catalog, "get", return_value=CatalogFetch(catalog, ok=True)):
        out = client.translate_content_block(html, category="CAT")
    assert client.pending_content_blocks == [], "the block missed and re-registered"
    assert f'{attr}="{TRANSLATED}"' in out, out


@pytest.mark.parametrize("shape", sorted(LOOKUP_SHAPES))
@pytest.mark.parametrize("tag, attr", [("img", "alt"), ("input", "placeholder")])
def test_CONF1_attribute_lookup_on_the_page_path(tag, attr, shape):
    """The page path reaches an attribute only inside a leaf block (a top-level void element is
    not tokenized there at all - measured, and held for the registration-shape ruling)."""
    inner = f'Label <{tag} {attr}="{LOOKUP_SHAPES[shape]}">'
    out, phrases, blocks = render_page(f"<p>{inner}</p>", catalog=_block_catalog(inner))
    assert (phrases, blocks) == ([], []), f"the lookup missed and re-registered: {phrases} {blocks}"
    assert f'{attr}="{TRANSLATED}"' in out, out


@pytest.mark.parametrize("shape", sorted(LOOKUP_SHAPES))
def test_CONF1_option_text_lookup_on_the_block_path(shape):
    html = f"<div><select><option>{LOOKUP_SHAPES[shape]}</option><option>Other</option></select></div>"
    client = _client()
    catalog = _block_catalog(html)
    with patch.object(client._catalog, "get", return_value=CatalogFetch(catalog, ok=True)):
        out = client.translate_content_block(html, category="CAT")
    assert client.pending_content_blocks == [], "the block missed and re-registered"
    assert f"<option>{TRANSLATED}</option>" in out, out


@pytest.mark.parametrize("shape", sorted(LOOKUP_SHAPES))
def test_CONF1_option_text_lookup_on_the_page_path(shape):
    body = f"<form><select><option>{LOOKUP_SHAPES[shape]}</option></select></form>"
    out, phrases, blocks = render_page(body, catalog={"CAT": {COLLAPSED: TRANSLATED}})
    assert (phrases, blocks) == ([], []), f"the lookup missed and re-registered: {phrases} {blocks}"
    assert f"<option>{TRANSLATED}</option>" in out, out
