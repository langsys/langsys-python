"""MIG-9: a one-time import pairs each key's existing translation with the same key's source value
and registers them together through the `translations` list on `POST /api/translatable-items`.

The first half reads target files alone: what is and is not a translation, and how a gettext plural's
`msgstr[n]` become CLDR categories through the file's `Plural-Forms` (the Reviewer's ruling on
spec 8.8.1: every count of a category takes one form, several categories may share a form, every
form is taken, and a category reached only by fractions has no form, so the entry is skipped). The
second half runs the import against a transport that records what was sent.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest

from langsys import LangsysClient, NotConfiguredError
from langsys.cache import MemoryCache
from langsys.exceptions import ConfigurationError
from langsys.migrate import LegacyFile, LegacyTranslations, gettext_plural_categories

API = "https://api.test/api"
AUTH = f"{API}/authorize-project/proj-1"
ITEMS = f"{API}/translatable-items"
TRANS = re.compile(r"https://api\.test/api/translations.*")

FR_FORMS = "nplurals=2; plural=(n > 1);"
RU_FORMS = ("nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
            "(n%100<10 || n%100>=20) ? 1 : 2);")


def po(path, plural_forms, body):
    header = f'msgid ""\nmsgstr ""\n"Plural-Forms: {plural_forms}\\n"\n\n' if plural_forms else ""
    path.write_text(header + body, encoding="utf-8")
    return path


PLURAL = '''msgid "One file"
msgid_plural "%(count)d files"
{forms}
'''


def plural_entry(*forms):
    return PLURAL.format(forms="\n".join(f'msgstr[{i}] "{f}"' for i, f in enumerate(forms)))


# -- gettext plurals ------------------------------------------------------------------------------


def test_MIG9_a_french_plural_imports_as_one_many_other(tmp_path):
    """French `many` (a million) and `other` both take msgstr[1]: a positional mapping would put
    nothing under `other`."""
    path = po(tmp_path / "fr.po", FR_FORMS, plural_entry("%(count)d fichier", "%(count)d fichiers"))
    value = LegacyTranslations("fr-fr", [path]).get((None, "One file"))
    assert value.translation == "{count, plural, one {# fichier} many {# fichiers} other {# fichiers}}"


def test_MIG9_each_category_takes_the_form_its_counts_select_not_its_position(tmp_path):
    path = po(tmp_path / "de.po", "nplurals=2; plural=(n == 1 ? 1 : 0);",
              plural_entry("%(count)d Dateien", "eine Datei"))
    value = LegacyTranslations("de-de", [path]).get((None, "One file"))
    assert value.translation == "{count, plural, one {eine Datei} other {# Dateien}}"


def test_MIG9_an_arabic_plural_maps_all_six_forms(tmp_path):
    forms = ("nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : "
             "n%100>=11 ? 4 : 5);")
    path = po(tmp_path / "ar.po", forms, plural_entry("z", "o", "t", "f", "m", "x"))
    assert LegacyTranslations("ar", [path]).get((None, "One file")).translation == (
        "{count, plural, zero {z} one {o} two {t} few {f} many {m} other {x}}")


def test_MIG9_a_russian_plural_is_skipped_with_a_warning_and_its_singulars_still_import(
    tmp_path, caplog
):
    """Russian `other` is reached only by fractions, which a gettext count never is."""
    path = po(tmp_path / "ru.po", RU_FORMS,
              plural_entry("%(count)d файл", "%(count)d файла", "%(count)d файлов")
              + '\nmsgid "Save"\nmsgstr "Сохранить"\n')
    with caplog.at_level("WARNING", logger="langsys"):
        reader = LegacyTranslations("ru-ru", [path])
    value = reader.get((None, "One file"))
    assert (value.translation, value.reason) == (None, "plural-forms")
    (warning,) = [r.getMessage() for r in caplog.records if "plural forms" in r.getMessage()]
    for named in (str(path), "ru-ru", "'One file'", "'other'"):
        assert named in warning, (named, warning)
    assert reader.get((None, "Save")).translation == "Сохранить", "control: a singular imports"


def test_MIG9_control_a_plural_forms_that_does_not_fit_its_language_is_skipped(tmp_path):
    """A Russian file carrying French's expression: counts CLDR calls `one` (1 and 21) take
    two forms. The check can fail on a file whose categories all have whole-number counts."""
    path = po(tmp_path / "ru.po", FR_FORMS, plural_entry("a", "b"))
    value = LegacyTranslations("ru-ru", [path]).get((None, "One file"))
    assert value.reason == "plural-forms" and "'one'" in value.detail, value


@pytest.mark.parametrize(("forms", "why"), [
    (None, "declares no Plural-Forms"),
    ("nplurals=3; plural=(n > 1);", "takes msgstr[2]"),
    ("nplurals=2; plural=(n > 1 ? 2 : 0);", "nplurals is 2"),
    ("nplurals=2; plural=(n >>> 1);", "does not parse"),
], ids=["no-header", "a-form-no-count-takes", "an-index-past-nplurals", "unparsable"])
def test_MIG9_a_plural_forms_header_that_cannot_be_mapped_skips_the_entry(tmp_path, forms, why):
    path = po(tmp_path / "fr.po", forms, plural_entry("a", "b"))
    value = LegacyTranslations("fr-fr", [path]).get((None, "One file"))
    assert value.reason == "plural-forms" and why in value.detail, value


def test_MIG9_an_entry_whose_form_count_differs_from_its_plural_forms_is_skipped(tmp_path):
    path = po(tmp_path / "de.po", "nplurals=2; plural=(n != 1);", plural_entry("a", "b", "c"))
    value = LegacyTranslations("de-de", [path]).get((None, "One file"))
    assert value.reason == "plural-forms" and "has 3 forms" in value.detail, value
    control = po(tmp_path / "de2.po", "nplurals=2; plural=(n != 1);", plural_entry("a", "b"))
    assert LegacyTranslations("de-de", [control]).get((None, "One file")).translation


def test_MIG9_the_counts_evaluated_reach_every_categorys_whole_number_samples():
    mapping, why = gettext_plural_categories(f"Plural-Forms: {FR_FORMS}", "fr")
    assert mapping == {"one": 0, "many": 1, "other": 1}, why
    mapping, why = gettext_plural_categories(f"Plural-Forms: {RU_FORMS}", "ru")
    assert mapping is None and "'other' has no whole-number count" in why


# -- what is not a translation -------------------------------------------------------------------


def test_MIG9_an_empty_fuzzy_or_unconvertible_gettext_value_is_not_a_translation(tmp_path):
    path = po(tmp_path / "it.po", FR_FORMS, '''msgid "Empty"
msgstr ""

#, fuzzy
msgid "Fuzzy"
msgstr "Sfocato"

msgid "Price %(amount).2f"
msgstr "Prezzo %(amount).2f"

msgctxt "UI"
msgid "Hello %(name)s"
msgstr "Ciao %(name)s"

msgid "One file"
msgid_plural "%(count)d files"
msgstr[0] "%(count)d file"
msgstr[1] ""
''')
    reader = LegacyTranslations("it-it", [path])
    assert reader.get((None, "Empty")).reason == "empty"
    assert reader.get((None, "Fuzzy")).reason == "fuzzy"
    assert reader.get((None, "Price %(amount).2f")).reason == "not-converted"
    assert reader.get((None, "One file")).reason == "empty"
    assert reader.get((None, "Absent")).reason == "missing"
    assert reader.get(("UI", "Hello %(name)s")).translation == "Ciao {name}", "control"


def test_MIG9_a_plain_files_values_convert_as_the_source_files_do(tmp_path):
    path = tmp_path / "it.json"
    path.write_text(json.dumps({"checkout": {"submit": "Paga {{total}}", "items": "1 | :count",
                                             "blank": "  "}, "hello": "Ciao :name"}))
    reader = LegacyTranslations("it-it", [LegacyFile(path)])
    assert reader.get((None, "checkout.submit")).translation == "Paga {total}"
    assert reader.get((None, "hello")).translation == "Ciao {name}"
    assert reader.get((None, "checkout.items")).reason == "not-converted"
    assert reader.get((None, "checkout.blank")).reason == "empty"


# -- the import ----------------------------------------------------------------------------------


def authorized(write_enabled=True, batch_limit=200):
    return {"status": True, "data": {
        "id": "proj-1", "title": "T", "base_locale": "en-us",
        # The base locale is listed too, so its refusal is the import's own and not the list's.
        "target_locales": ["en-us", "it-it", "fr-fr", "ru-ru"], "default_locales": {},
        "key_type": "write", "write_enabled": write_enabled,
        "langsys_settings": {"translatable_items": {"batch_limit": batch_limit}}}}


class Items:
    """Records each registration POST and answers it with the quota split given."""

    def __init__(self, httpx_mock, saved=0, skipped=0, status=200):
        self.bodies: list[dict] = []
        self.saved, self.skipped, self.status = saved, skipped, status
        httpx_mock.add_callback(self, url=ITEMS, is_reusable=True, is_optional=True)

    def __call__(self, request):
        self.bodies.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, json={"status": False, "error": {"message": "no"}})
        return httpx.Response(200, json={"status": True, "data": {
            "human_translations_saved": self.saved, "human_translations_skipped": self.skipped}})

    def items(self):
        return [item for body in self.bodies for item in body["translatable_items"]]


@pytest.fixture
def files(tmp_path):
    source = tmp_path / "en.json"
    source.write_text(json.dumps({"checkout": {"submit": "Place order {{total}}"},
                                  "welcome": "Welcome back"}))
    italian = tmp_path / "it.json"
    italian.write_text(json.dumps({"checkout": {"submit": "Effettua l'ordine {{total}}"},
                                   "stale": "Vecchio"}))
    french = tmp_path / "fr.json"
    french.write_text(json.dumps({"checkout": {"submit": "Commander {{total}}"},
                                  "welcome": ""}))
    return {"source": source, "it": italian, "fr": french}


def make(files, **kw):
    return LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                         debounce=0, auto_flush=False, legacy_files=[files["source"]], **kw)


def test_MIG9_an_import_registers_each_key_with_its_translations(httpx_mock, files):
    httpx_mock.add_response(url=AUTH, json=authorized(), is_reusable=True)
    sent = Items(httpx_mock, saved=2)
    result = make(files).import_legacy_translations({"it-IT": [files["it"]], "fr-fr": [files["fr"]]})
    assert sent.items() == [
        {"type": "phrase", "phrase": "Place order {total}", "category": "checkout",
         "translatable": True, "translations": [
             {"locale": "it-it", "translation": "Effettua l'ordine {total}"},
             {"locale": "fr-fr", "translation": "Commander {total}"}]},
        {"type": "phrase", "phrase": "Welcome back", "category": None, "translatable": True},
    ]
    assert (result["success"], result["phrases"], result["translations"]) == (True, 2, 2)
    assert result["human_translations_saved"] == 2
    assert {(n["key"], n["locale"], n["reason"]) for n in result["not_imported"]} == {
        ("welcome", "it-it", "missing"), ("welcome", "fr-fr", "empty"),
        ("stale", "it-it", "no-source-key")}


def test_MIG9_an_imported_phrase_is_the_one_translate_looks_up(httpx_mock, files):
    """A catalog of exactly what the import sent answers the key's lookup, with nothing queued."""
    httpx_mock.add_response(url=AUTH, json=authorized(), is_reusable=True)
    sent = Items(httpx_mock)
    client = make(files)
    client.import_legacy_translations({"it-it": [files["it"]]})
    catalog: dict = {}
    for item in sent.items():
        for t in item.get("translations", []):
            catalog.setdefault(item["category"] or "__uncategorized__", {})[item["phrase"]] = t["translation"]
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": catalog})
    assert client.translate("checkout.submit", locale="it-it") == "Effettua l'ordine {total}"
    assert client.has_pending is False


def test_MIG9_the_quota_split_is_summed_across_batches(httpx_mock, files):
    httpx_mock.add_response(url=AUTH, json=authorized(batch_limit=1), is_reusable=True)
    sent = Items(httpx_mock, saved=1, skipped=2)
    result = make(files).import_legacy_translations({"it-it": [files["it"]]})
    assert len(sent.bodies) == 2
    assert (result["human_translations_saved"], result["human_translations_skipped"]) == (2, 4)


@pytest.mark.parametrize("locale", ["de-de", "en-US"], ids=["not-a-target", "the-base-locale"])
def test_MIG9_a_locale_the_project_does_not_target_is_refused_before_anything_is_sent(
    httpx_mock, files, locale
):
    httpx_mock.add_response(url=AUTH, json=authorized(), is_reusable=True)
    sent = Items(httpx_mock)
    with pytest.raises(ConfigurationError, match=locale):
        make(files).import_legacy_translations({"it-it": [files["it"]], locale: [files["fr"]]})
    assert sent.bodies == []
    make(files).import_legacy_translations({"it-it": [files["it"]]})
    assert len(sent.bodies) == 1, "control: a target locale's import is sent"


def test_MIG9_a_read_key_imports_nothing_and_says_why(httpx_mock, files):
    httpx_mock.add_response(url=AUTH, json=authorized(write_enabled=False), is_reusable=True)
    sent = Items(httpx_mock)
    result = make(files).import_legacy_translations({"it-it": [files["it"]]})
    assert (result["success"], result["skipped"], result["reason"]) == (False, True, "not-write-enabled")
    assert sent.bodies == []


def test_MIG9_a_refused_send_is_a_failure_not_an_exception(httpx_mock, files):
    httpx_mock.add_response(url=AUTH, json=authorized(), is_reusable=True)
    Items(httpx_mock, status=500)
    result = make(files).import_legacy_translations({"it-it": [files["it"]]})
    assert (result["success"], result["reason"]) == (False, "registration-failed")
    assert "error" in result


def test_MIG9_after_an_import_the_next_read_fetches_the_catalog(httpx_mock, files):
    httpx_mock.add_response(url=AUTH, json=authorized(), is_reusable=True)
    Items(httpx_mock)
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True,
                                             "data": {"checkout": {}}})
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {
        "checkout": {"Place order {total}": "Effettua l'ordine {total}"}}})
    client = make(files)
    assert client.translate("checkout.submit", locale="it-it") == "Place order {total}"
    client.clear_pending()
    client.import_legacy_translations({"it-it": [files["it"]]})
    assert client.translate("checkout.submit", locale="it-it") == "Effettua l'ordine {total}"


def test_MIG9_an_import_with_no_source_files_is_refused(httpx_mock, files):
    client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), auto_flush=False)
    with pytest.raises(ConfigurationError, match="legacy_files"):
        client.import_legacy_translations({"it-it": [files["it"]]})
    assert httpx_mock.get_requests() == []


def test_MIG9_an_unconfigured_client_refuses_the_import(httpx_mock, files, monkeypatch):
    for name in ("LANGSYS_API_KEY", "LANGSYS_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    client = LangsysClient(api_url=API, cache=MemoryCache(), auto_flush=False,
                           legacy_files=[files["source"]])
    with pytest.raises(NotConfiguredError):
        client.import_legacy_translations({"it-it": [files["it"]]})
    assert httpx_mock.get_requests() == []


def test_MIG9_a_gettext_import_reports_the_plurals_left_for_machine_translation(httpx_mock, tmp_path):
    source = po(tmp_path / "django.pot", FR_FORMS, plural_entry("", "") + '\nmsgid "Save"\nmsgstr ""\n')
    french = po(tmp_path / "fr.po", FR_FORMS, plural_entry("%(count)d fichier", "%(count)d fichiers")
                + '\nmsgid "Save"\nmsgstr "Enregistrer"\n')
    russian = po(tmp_path / "ru.po", RU_FORMS, plural_entry("a", "b", "c")
                 + '\nmsgid "Save"\nmsgstr "Сохранить"\n')
    httpx_mock.add_response(url=AUTH, json=authorized(), is_reusable=True)
    sent = Items(httpx_mock)
    client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                           auto_flush=False, legacy_files=[LegacyFile(source, "gettext")])
    result = client.import_legacy_translations({"fr-fr": [french], "ru-ru": [russian]})
    by_phrase = {item["phrase"]: item.get("translations", []) for item in sent.items()}
    assert by_phrase["{count, plural, =1 {One file} other {# files}}"] == [
        {"locale": "fr-fr",
         "translation": "{count, plural, one {# fichier} many {# fichiers} other {# fichiers}}"}]
    assert by_phrase["Save"] == [{"locale": "fr-fr", "translation": "Enregistrer"},
                                 {"locale": "ru-ru", "translation": "Сохранить"}]
    assert result["plural_entries_unmapped"] == 1
