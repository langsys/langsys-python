"""FRM-8: catalog text is escaped where the core prints markup, and a rich line's source markup is
rebuilt around its translation.

The render half: `translate_rich()` encodes a source line's inline elements as rich-phrase tokens
(`Read our {m0o}terms{m0c}`), looks the token form up, and rebuilds only the source's own elements,
tags and attributes as written, around the translated runs, each a text node. A language-file line
(the miss fallback's) comes back raw, as the framework prints it. A content block's markup is the
core's own raw-printing path, so its catalog text is escaped too. The sync half: a line from a
raw-printing function registers tokenized exactly as `translate_rich()` looks it up, with the
framework's translations tokenized onto the source's elements.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from langsys import LangsysClient, Miss
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch
from langsys.sync import Planner, scan_source

#: FRM-8 is not built yet: these tests are its red run, kept until the implementation lands with
#: them, when this mark goes.
pytestmark = pytest.mark.xfail(reason="FRM-8 is not built yet", strict=False)

API = "https://api.test/api"
U = "__uncategorized__"
TERMS = 'Read our <a href="/terms">terms</a>'


def make(catalog, files=None, **options):
    client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                           debounce=0, auto_flush=False, miss_fallback=files, **options)
    client.set_locale("es-cr")
    client._catalog.get = lambda locale, use_cache=True: CatalogFetch(  # type: ignore[method-assign]
        catalog, ok=True)
    return client


# -- a content block's catalog text is escaped ---------------------------------------------------


def test_FRM8_a_content_blocks_catalog_text_is_escaped():
    client = make({U: {"Fish & chips": "Pescado <img src=x onerror=alert(1)>"}})
    assert client.translate_content_block("Fish &amp; chips") == \
        "Pescado &lt;img src=x onerror=alert(1)&gt;"


def test_FRM8_an_untranslated_content_block_keeps_the_sources_entities():
    client = make({U: {}})
    assert client.translate_content_block("Tom &amp; Jerry &lt;3") == "Tom &amp; Jerry &lt;3"


def test_FRM8_control_text_inside_an_element_was_already_escaped():
    client = make({U: {"Fish & chips": "Pescado <img src=x>"}})
    assert client.translate_content_block("<p>Fish &amp; chips</p>") == \
        "<p>Pescado &lt;img src=x&gt;</p>"


# -- the render half -----------------------------------------------------------------------------


def test_FRM8_a_rich_line_rebuilds_only_the_sources_markup():
    client = make({U: {"Read our {m0o}terms{m0c}":
                       "Lee nuestros {m0o}términos{m0c} <script>alert(1)</script>"}})
    assert client.translate_rich(TERMS) == \
        'Lee nuestros <a href="/terms">términos</a> &lt;script&gt;alert(1)&lt;/script&gt;'


def test_FRM8_a_translation_cannot_add_a_tag_or_change_an_attribute():
    client = make({U: {"Read our {m0o}terms{m0c}":
                       '{m0o}<b onclick="x">Lee</b>{m0c} {m1o}esto{m1c}'}})
    assert client.translate_rich(TERMS) == \
        '<a href="/terms">&lt;b onclick="x"&gt;Lee&lt;/b&gt;</a> esto'


def test_FRM8_a_translation_places_the_sources_elements_in_its_own_order():
    source = '<a href="/a">Apples</a> and <em>pears</em>'
    client = make({U: {"{m0o}Apples{m0c} and {m1o}pears{m1c}": "{m1o}peras{m1c} y {m0o}manzanas{m0c}"}})
    assert client.translate_rich(source) == '<em>peras</em> y <a href="/a">manzanas</a>'


def test_FRM8_a_source_element_the_translation_omits_is_not_rendered():
    client = make({U: {"Read our {m0o}terms{m0c}": "Lee los términos"}})
    assert client.translate_rich(TERMS) == "Lee los términos"


def test_FRM8_a_language_file_line_stays_raw():
    def files(miss: Miss):
        return 'Lee <a href="/es/terms">esto</a>, {name}' if miss.argument == TERMS + ", {name}" \
            else None

    client = make({U: {}}, files=files)
    assert client.translate_rich(TERMS + ", {name}", params={"name": "Ana"}) == \
        'Lee <a href="/es/terms">esto</a>, Ana'


def test_FRM8_a_rich_lines_fallback_sees_the_line_as_written():
    asked: list[Miss] = []
    make({U: {}}, files=lambda miss: asked.append(miss)).translate_rich(TERMS)
    assert [(m.phrase, m.argument) for m in asked] == [("Read our {m0o}terms{m0c}", TERMS)]


def test_FRM8_with_neither_the_source_line_comes_back_with_values_as_text():
    client = make({U: {}})
    assert client.translate_rich('Hi {name}, read <a href="/t">this</a>',
                                 params={"name": "<b>Ana</b>"}) == \
        'Hi &lt;b&gt;Ana&lt;/b&gt;, read <a href="/t">this</a>'


def test_FRM8_a_value_never_becomes_an_element():
    client = make({U: {"Read {m0o}this{m0c}, {name}": "Lee {m0o}esto{m0c}, {name}"}})
    assert client.translate_rich('Read <a href="/t">this</a>, {name}',
                                 params={"name": "{m0o}x{m0c}"}) == \
        'Lee <a href="/t">esto</a>, x'


def test_FRM8_a_miss_registers_the_token_form():
    client = make({U: {}})
    client.translate_rich(TERMS)
    assert client.pending_phrases == [{"phrase": "Read our {m0o}terms{m0c}", "category": U}]


# -- the sync half -------------------------------------------------------------------------------

RICH = {"gettext_html": "gettext"}


def scan(source):
    return scan_source(textwrap.dedent(source), "app/views.py", rich_functions=RICH)


def phrases(items):
    return [(i.phrase, i.category) for i in items]


@pytest.fixture
def legal(tmp_path):
    source = tmp_path / "en.json"
    source.write_text(json.dumps({"legal": {
        "terms": TERMS,
        "two": '<a href="/a">A</a> or <a href="/b">B</a>',
        "odd": "Read <em>this</em>",
        "blank": "",
    }}))
    spanish = tmp_path / "es.json"
    spanish.write_text(json.dumps({"legal": {
        "terms": 'Lee nuestros <a href="/terms">términos</a>',
        "two": '<a href="/b">B</a> o <a href="/a">A</a>',
        "odd": "Lee <strong>esto</strong>",
    }}))
    return {"legacy_files": [source], "targets": {"es-cr": [spanish]}}


def test_FRM8_a_raw_printed_line_is_read_and_marked_rich():
    [rich, plain] = scan(f'gettext_html({TERMS!r})\n_("Plain")\n')
    assert rich.rich and rich.text == TERMS and not plain.rich


def test_FRM8_a_rich_line_registers_once_in_the_form_the_runtime_looks_up():
    plan = Planner().plan(scan(f'gettext_html({TERMS!r})\ngettext_html({TERMS!r})\n'), {})
    client = make({U: {}})
    client.translate_rich(TERMS)
    assert phrases(plan.new) == [(client.pending_phrases[0]["phrase"], None)]
    assert plan.new[0].origins == ["app/views.py:1", "app/views.py:2"]


def test_FRM8_a_rich_line_with_no_text_registers_and_reports_nothing():
    plan = Planner().plan(scan('gettext_html("<br>")\n'), {})
    assert plan.new == [] and plan.reported == []


def test_FRM8_a_rich_key_registers_tokenized_with_the_translations_that_line_up(legal):
    plan = Planner(**legal).plan(scan('gettext_html("legal.terms")\ngettext_html("legal.two")\n'), {})
    assert [(i.phrase, i.category, i.translations) for i in plan.with_translations] == [
        ("Read our {m0o}terms{m0c}", "legal", {"es-cr": "Lee nuestros {m0o}términos{m0c}"}),
        ("{m0o}A{m0c} or {m1o}B{m1c}", "legal", {"es-cr": "{m1o}B{m1c} o {m0o}A{m0c}"}),
    ]


def test_FRM8_a_translation_whose_elements_do_not_line_up_is_not_converted(legal):
    plan = Planner(**legal).plan(scan('gettext_html("legal.odd")\n'), {})
    assert ("Read {m0o}this{m0c}", "legal") in phrases(plan.new)
    assert [(n["key"], n["locale"]) for n in plan.not_converted] == [("legal.odd", "es-cr")]


def test_FRM8_a_rich_key_registers_no_raw_copy(legal):
    plan = Planner(**legal).plan(scan('gettext_html("legal.terms")\n'), {})
    assert (TERMS, "legal") not in phrases(plan.new + plan.with_translations)
    reached = Planner(**legal).plan(scan('gettext_html("legal.terms")\n_("legal.terms")\n'), {})
    assert (TERMS, "legal") in phrases(reached.with_translations), \
        "control: a plain call reaching the key keeps its raw line"


def test_FRM8_a_rich_line_building_its_key_at_runtime_registers_its_group_tokenized(legal):
    plan = Planner(**legal).plan(scan('gettext_html(f"legal.{key}")\n'), {})
    assert [h.group for h in plan.covered] == ["legal"] and not plan.reported
    registered = phrases(plan.new + plan.with_translations)
    assert ("Read our {m0o}terms{m0c}", "legal") in registered
    assert ("{m0o}A{m0c} or {m1o}B{m1c}", "legal") in registered
    assert (TERMS, "legal") not in registered


def test_FRM8_a_missing_translation_of_a_rich_key_is_reported_once(tmp_path):
    source = tmp_path / "en.json"
    source.write_text(json.dumps({"legal": {"terms": TERMS, "blank": ""}}))
    spanish = tmp_path / "es.json"
    spanish.write_text(json.dumps({"legal": {}}))
    plan = Planner(legacy_files=[source], targets={"es-cr": [spanish]}).plan(
        scan('gettext_html("legal.terms")\ngettext_html(f"legal.{k}")\n'), {})
    assert [(n["key"], n["locale"]) for n in plan.untranslated] == [("legal.terms", "es-cr")]
