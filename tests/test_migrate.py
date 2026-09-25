"""MIG-1..7 - legacy-key migration: a keyed app keeps its source file, and `t()` resolves keys in it.

Python's formats are `gettext` (`.po`) and `plain` JSON (MIG-7). `mig-vectors.json` is not
authored yet, so these rows are the spec's own examples. MIG-8 is the framework's entry point
(Django's gettext family) and belongs to the binding; `convert_literal` is the converter it calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch
from langsys.exceptions import ConfigurationError
from langsys.migrate import (
    LegacyFile,
    LegacyKeys,
    convert_literal,
    convert_value,
    gettext_plural,
    main,
)
from langsys.registration import generate_custom_id

PO = r'''# Source-language catalog
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"

msgid "Save"
msgstr ""

msgctxt "cart"
msgid "Remove"
msgstr ""

msgid "Hello %(name)s"
msgstr ""

msgid "%(count)s item"
msgid_plural "%(count)s items"
msgstr[0] ""
msgstr[1] ""

msgid "Line one "
"and line two"
msgstr ""
'''


@pytest.fixture
def files(tmp_path: Path):
    source = tmp_path / "en.json"
    source.write_text(json.dumps({
        "checkout": {"submit": "Pay now", "greeting": "Hello {{name}}"},
        "flat_key": "Plain value",
        "cars": "car | cars",
    }), encoding="utf-8")
    po = tmp_path / "django.po"
    po.write_text(PO, encoding="utf-8")
    return tmp_path, source, po


def client(legacy_files=None, catalog=None):
    c = LangsysClient("k", "p", api_url="https://api.test/api", cache=MemoryCache(),
                      base_locale="en-us", debounce=0, auto_flush=False, legacy_files=legacy_files)
    return c, patch.object(c._catalog, "get", return_value=CatalogFetch(catalog or {}, ok=True))


def queued(c):
    return [(p["phrase"], p["category"]) for p in c.pending_phrases]


# -- MIG-1 -----------------------------------------------------------------------------------------


def test_MIG1_unset_reads_no_file_and_looks_up_no_key():
    """Load-bearing unset case: a lookup that ran unconditionally would pass every test that
    configures the mode."""
    with patch.object(Path, "read_text", side_effect=AssertionError("a legacy file was read")), \
            patch.object(LegacyKeys, "resolve", side_effect=AssertionError("a key lookup ran")):
        c, patched = client()
        with patched:
            c.translate("checkout.submit", locale="it-it")
    assert queued(c) == [("checkout.submit", "__uncategorized__")]


def test_MIG1_control_set_it_reads_the_file(files):
    _, source, _ = files
    c, patched = client([source])
    with patched:
        c.translate("checkout.submit", locale="it-it")
    assert queued(c) == [("Pay now", "checkout")]


# -- MIG-2 / MIG-3 ---------------------------------------------------------------------------------


def test_MIG2_a_hit_takes_the_source_value_and_a_miss_is_literal_source(files):
    _, source, _ = files
    c, patched = client([source])
    with patched:
        c.translate("flat_key", locale="it-it")
        c.translate("Already written source text", category="UI", locale="it-it")
    assert queued(c) == [("Plain value", "__uncategorized__"), ("Already written source text", "UI")]


def test_MIG3_a_key_registers_the_same_phrase_and_id_as_its_source_text_and_never_itself(files):
    _, source, _ = files
    keyed, patched = client([source])
    with patched:
        keyed.translate("checkout.submit", locale="it-it")
    direct, patched = client()
    with patched:
        direct.translate("Pay now", category="checkout", locale="it-it")
    assert queued(keyed) == queued(direct)
    assert all("checkout.submit" not in phrase for phrase, _ in queued(keyed))
    assert generate_custom_id("checkout", ["Pay now"]) == generate_custom_id("checkout", [queued(keyed)[0][0]])


def test_MIG2_a_hit_renders_its_translation(files):
    _, source, _ = files
    c, patched = client([source], {"checkout": {"Pay now": "Paga ora"}})
    with patched:
        assert c.translate("checkout.submit", locale="it-it") == "Paga ora"


@pytest.mark.parametrize(("text", "entry_point", "passed", "expected"), [
    # Written in another framework's syntax on purpose: a Langsys-syntax vector cannot tell
    # "converts nothing" from "converts".
    ("Hello %(name)s and {{ name }}", "t", ["name"], "Hello %(name)s and {{ name }}"),
    ("Hello %(name)s", "gettext", ["name"], "Hello {name}"),
    ("Hello %(name)s and %(other)s", "gettext", ["name"], "Hello {name} and %(other)s"),
    ("Hello {{ name }}", "blocktranslate", ["name"], "Hello {name}"),
    ("Hello {{ name }}", "blocktranslate", [], "Hello {{ name }}"),
], ids=["t-converts-nothing", "gettext-passed", "gettext-unpassed-stays", "block-passed", "block-unpassed"])
def test_MIG2_a_literal_converts_under_its_entry_points_syntax(text, entry_point, passed, expected):
    assert convert_literal(text, entry_point, passed) == expected


def test_MIG2_djangos_gettext_and_langsys_t_register_one_phrase():
    assert convert_literal("Hello %(name)s", "gettext", ["name"]) == "Hello {name}"


def test_MIG2_ngettext_registers_the_gettext_plural():
    assert gettext_plural("%(count)s item", "%(count)s items", literal=True, passed=["count"]) == (
        "{count, plural, =1 {# item} other {# items}}"
    )


def test_MIG2_an_unknown_entry_point_is_refused():
    with pytest.raises(ValueError):
        convert_literal("x", "i18next")


# -- MIG-4 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [
    ("Hello {{name}}", "Hello {name}"),
    ("Hello {name}", "Hello {name}"),
    ("Hello :name", "Hello {name}"),
    ("Hello %{name}", "Hello {name}"),
    ("Hello %(name)s, %(n)d left", "Hello {name}, {n} left"),
    ("100%% sure", "100% sure"),
    ("Note:done", "Note:done"),
], ids=["double-brace", "brace", "colon", "rails", "python", "percent-percent", "no-colon-mid-word"])
def test_MIG4_placeholders_convert_the_same_way_whatever_the_format(value, expected):
    assert convert_value(value) == expected


@pytest.mark.parametrize("value", ["Total %(price).2f", "Total %<price>.2f", "Hello %s", "Hello :Name"],
                         ids=["python-formatted", "rails-formatted", "positional", "laravel-cased"])
def test_MIG4_a_form_name_cannot_express_registers_verbatim_and_warns(value, caplog):
    with caplog.at_level(logging.WARNING, logger="langsys"):
        assert convert_value(value, where="en.json key 'x'") == value
    assert any("registered verbatim" in r.getMessage() for r in caplog.records)


def test_MIG4_a_pipe_in_a_plain_file_registers_verbatim_and_warns(files, caplog):
    _, source, _ = files
    with caplog.at_level(logging.WARNING, logger="langsys"):
        keys = LegacyKeys([source])
    assert keys.resolve("cars", None)[0] == "car | cars"
    assert any("'cars'" in r.getMessage() and "verbatim" in r.getMessage() for r in caplog.records)


def test_MIG4_a_gettext_plural_is_exact_one_and_other_with_the_count_as_hash(files):
    _, _, po = files
    phrase = LegacyKeys([po]).resolve("%(count)s item", None)[0]
    assert phrase == "{count, plural, =1 {# item} other {# items}}"
    assert "{count}" not in phrase.split(",", 2)[2]


# -- MIG-5 -----------------------------------------------------------------------------------------


def test_MIG5_the_namespace_is_the_category_unless_the_call_passes_one(files):
    _, source, _ = files
    keys = LegacyKeys([source])
    assert keys.resolve("checkout.submit", None)[1] == "checkout"
    assert keys.resolve("checkout.submit", "Buttons")[1] == "Buttons"


def test_MIG5_a_gettext_context_is_the_category(files):
    _, _, po = files
    keys = LegacyKeys([po])
    assert keys.resolve("Remove", None) == ("Remove", "cart", True)
    assert keys.resolve("Save", None) == ("Save", None, True)


# -- MIG-6 -----------------------------------------------------------------------------------------


def test_MIG6_an_absent_key_warns_at_debug_and_registers_its_argument(files, caplog):
    _, source, _ = files
    c, patched = client([source])
    with patched, caplog.at_level(logging.DEBUG, logger="langsys"):
        c.translate("checkout.sumbit", locale="it-it")
    assert queued(c) == [("checkout.sumbit", "__uncategorized__")]
    assert any("not a key in the legacy files" in r.getMessage() for r in caplog.records)


def test_MIG6_a_changed_value_is_simply_a_new_phrase(tmp_path):
    source = tmp_path / "en.json"
    source.write_text(json.dumps({"k": "Old wording"}), encoding="utf-8")
    before = LegacyKeys([source]).resolve("k", None)[0]
    source.write_text(json.dumps({"k": "New wording"}), encoding="utf-8")
    assert (before, LegacyKeys([source]).resolve("k", None)[0]) == ("Old wording", "New wording")


# -- MIG-7 -----------------------------------------------------------------------------------------


def test_MIG7_nested_keys_resolve_by_path_in_json_and_po_reads_multiline_strings(files):
    _, source, po = files
    keys = LegacyKeys([source, po])
    assert keys.resolve("checkout.greeting", None)[0] == "Hello {name}"
    assert keys.resolve("Line one and line two", None)[0] == "Line one and line two"
    assert keys.resolve("Hello %(name)s", None)[0] == "Hello {name}"


def test_MIG7_the_first_configured_file_wins_and_the_duplicate_is_reported(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"k": "From A"}), encoding="utf-8")
    b.write_text(json.dumps({"k": "From B"}), encoding="utf-8")
    keys = LegacyKeys([a, b])
    assert keys.resolve("k", None)[0] == "From A"
    assert any("'k'" in p and "b.json" in p for p in keys.problems())


def test_MIG7_a_compiled_mo_is_refused_naming_its_po(tmp_path):
    mo = tmp_path / "django.mo"
    mo.write_bytes(b"\xde\x12\x04\x95")
    with pytest.raises(ConfigurationError, match="django.po"):
        client([mo])


@pytest.mark.parametrize(("name", "fmt"), [("en.php", None), ("en.yml", None), ("en.json", "vue-i18n"),
                                           ("en.json", "laravel")])
def test_MIG7_a_format_this_sdk_does_not_read_is_refused_naming_format_and_file(tmp_path, name, fmt):
    path = tmp_path / name
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigurationError) as refused:
        client([LegacyFile(path, fmt)])
    assert name in str(refused.value) and "gettext" in str(refused.value)


def test_MIG7_the_listing_names_duplicates_and_verbatim_values(files, capsys):
    _, source, po = files
    assert main([str(source), str(po)]) == 1
    out = capsys.readouterr().out
    assert "registered verbatim" in out and "'cars'" in out
    clean = source.parent / "clean.json"
    clean.write_text(json.dumps({"a": "b"}), encoding="utf-8")
    assert main([str(clean)]) == 0
