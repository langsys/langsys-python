"""FRM-2, the core's share of the sync command: the source scanner and the planner.

The command itself (a Django management command, `--watch`, `--strict`), finding the app's files and
scanning its templates are the binding's. The core reads Python source with `ast` for the configured
translate functions, and decides each phrase against the catalog: in it (nothing), in the framework's
files (registered with their translations, MIG-9), or new. A phrase registers exactly as the running
call looks it up, because the planner converts it with the function `translate_legacy()` uses.
"""

from __future__ import annotations

import enum
import json
import textwrap

import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch
from langsys.messages import TemplateList, message_params, rule_template, run_listing
from langsys.sync import Planner, scan_source
from langsys.values import translates_as

API = "https://api.test/api"
U = "__uncategorized__"


def scan(source, **options):
    return scan_source(textwrap.dedent(source), "app/views.py", **options)


def texts(hits):
    return [h.text for h in hits]


def phrases(items):
    return [(i.phrase, i.category) for i in items]


@pytest.fixture
def files(tmp_path):
    """The app's source-language keys and one target language's lines."""
    source = tmp_path / "en.json"
    source.write_text(json.dumps({"checkout": {"submit": "Pay now", "cancel": "Cancel order"},
                                  "orders": {"shipped": "Shipped today"}}))
    spanish = tmp_path / "es.json"
    spanish.write_text(json.dumps({"checkout": {"submit": "Pagar ahora"}}))
    return {"legacy_files": [source], "targets": {"es-cr": [spanish]}}


# -- the scanner ---------------------------------------------------------------------------------


def test_FRM2_every_entry_point_is_found():
    hits = scan('''
        from django.utils import translation
        a = _("One")
        b = gettext("Two")
        c = translation.gettext_lazy("Three")
        d = pgettext("menu", "Four")
        e = ngettext("%(count)d file", "%(count)d files", n)
        f = npgettext("inbox", "%(count)d message", "%(count)d messages", n)
        print(render(title=_("Five")))
        g = gettext(message="Six")
    ''')
    assert texts(hits) == ["One", "Two", "Three", "Four", "%(count)d file", "%(count)d message",
                           "Five", "Six"]
    assert [h.line for h in hits] == [3, 4, 5, 6, 7, 8, 9, 10]
    assert hits[3].category == "menu" and hits[5].category == "inbox"
    assert hits[4].entry_point == "ngettext" and hits[4].plural == "%(count)d files"


def test_FRM2_only_the_configured_functions_count():
    source = '''
        cache.get("Not a phrase")
        translate("Configured")
        def _(message):
            return message
        label = "_"
    '''
    assert texts(scan(source)) == [], "a call to an unconfigured function was collected"
    assert texts(scan(source, functions={"translate": "t"})) == ["Configured"]


def test_FRM2_literals_are_read_as_python_reads_them():
    hits = scan(r'''
        _("Tab\there")
        _("café")
        _("Hello "
          "world")
        _("Pay " + "now")
    ''')
    assert texts(hits) == ["Tab\there", "café", "Hello world", "Pay now"]


@pytest.mark.parametrize("call", [
    '_(name)', '_(f"Hello {name}")', '_("Hello %s" % name)', '_("Hello " + name)', '_()',
    '_(*args)', 'ngettext("%(count)d file", plural, n)', 'pgettext(context, "Save")',
], ids=["variable", "f-string", "percent", "concatenation", "no-argument", "unpacked",
        "plural-variable", "context-variable"])
def test_FRM2_a_non_literal_call_is_reported_with_its_line(call):
    [hit] = scan(f"x = 1\nvalue = {call}\n")
    assert hit.text is None and hit.reason and hit.origin == "app/views.py:2"


@pytest.mark.parametrize(("call", "group"), [
    ('_(f"validation.{key}")', "validation"),
    ('_("auth." + key)', "auth"),
    ('_(f"auth.errors.{key}")', "auth.errors"),
    ('_(f"{group}.{key}")', None),
    ('_(f"Hello {name}")', None),
    ('_(f"Hello. {name}")', None),
    ('_(key)', None),
], ids=["f-string", "concatenation", "nested", "runtime-group", "sentence", "sentence-stop",
        "variable"])
def test_FRM2_a_runtime_key_in_a_literal_group_names_the_group(call, group):
    [hit] = scan(f"value = {call}\n")
    assert hit.text is None and hit.group == group


@pytest.mark.parametrize(("call", "passed"), [
    ('_("Hi %(name)s") % {"name": n}', ("name",)),
    ('_("Hi %(name)s")', ()),
    ('_("Hi %(a)s %(b)s") % values', None),
    ('_("Hi %(a)s") % {"a": 1, **more}', None),
    ('_("Hi %s") % (name,)', ()),
    ('_("Hi %(name)s").upper()', ()),
], ids=["mapping", "none", "variable", "unpacked", "positional", "method"])
def test_FRM2_the_values_a_call_fills(call, passed):
    [hit] = scan(f"value = {call}\n")
    assert hit.passed == passed


def test_FRM2_a_t_style_call_reads_its_category_and_params():
    functions = {"t": "t"}
    hits = scan('''
        t("Hi {name}", params={"name": n})
        t("Hi {name}", params=values)
        t("Save", category="Toolbar")
        t("Save", category=where)
    ''', functions=functions)
    assert [h.passed for h in hits[:3]] == [("name",), None, ()]
    assert hits[2].category == "Toolbar"
    assert hits[3].text is None and "category" in hits[3].reason


# -- the planner ---------------------------------------------------------------------------------


def test_FRM2_each_phrase_is_decided_against_the_catalog(files):
    hits = scan('''
        _("Already there")
        _("Brand new")
        _("checkout.submit")
    ''')
    catalog = {U: {"Already there": None}, "orders": {"Shipped today": "Enviado hoy"}}
    plan = Planner(**files).plan(hits, catalog)
    assert phrases(plan.in_catalog) == [("Already there", None), ("Shipped today", "orders")]
    assert [(i.phrase, i.category, i.translations) for i in plan.with_translations] == [
        ("Pay now", "checkout", {"es-cr": "Pagar ahora"})]
    assert phrases(plan.new) == [("Brand new", None), ("Cancel order", "checkout")]
    assert not plan.fails_strict()


def test_FRM2_a_key_style_call_registers_under_its_group_and_a_literal_uncategorised(files):
    plan = Planner(**files).plan(scan('_("checkout.cancel")\n_("Cancel order")\n'), {})
    origins = {(i.phrase, i.category): i.origins for i in plan.new}
    assert "app/views.py:1" in origins[("Cancel order", "checkout")]
    assert origins[("Cancel order", None)] == ["app/views.py:2"]


@pytest.mark.parametrize(("call", "runtime"), [
    ('_("Hi %(name)s") % {"name": n}',
     {"text": "Hi %(name)s", "params": {"name": "Ana"}}),
    ('_("Hi %(name)s")', {"text": "Hi %(name)s"}),
    ('_("Hi %(a)s and %(b)s") % values',
     {"text": "Hi %(a)s and %(b)s", "params": {"a": 1, "b": 2}}),
    ('ngettext("%(count)d file", "%(count)d files", n)',
     {"text": "%(count)d file", "entry_point": "ngettext", "plural": "%(count)d files",
      "params": {"count": 2}}),
    ('pgettext("menu", "Open")', {"text": "Open", "category": "menu"}),
], ids=["filled", "unfilled", "dynamic", "plural", "context"])
def test_FRM2_a_phrase_registers_as_the_running_call_looks_it_up(call, runtime):
    plan = Planner().plan(scan(f"value = {call}\n"), {})
    client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                           debounce=0, auto_flush=False)
    client._catalog.get = lambda locale, use_cache=True: CatalogFetch(  # type: ignore[method-assign]
        {}, ok=True)
    client.translate_legacy(runtime.pop("text"), **runtime)
    [looked_up] = client.pending_phrases
    category = None if looked_up["category"] == U else looked_up["category"]
    assert phrases(plan.new) == [(looked_up["phrase"], category)]


def test_FRM2_a_non_literal_call_is_reported_not_registered_and_fails_strict():
    plan = Planner().plan(scan('_(name)\n_("Fine")\n'), {})
    assert phrases(plan.new) == [("Fine", None)]
    assert [h.origin for h in plan.reported] == ["app/views.py:1"]
    assert plan.fails_strict()


def test_FRM2_a_runtime_key_in_a_covered_group_passes_strict(files):
    plan = Planner(**files).plan(scan('_(f"checkout.{key}")\n'), {})
    assert [h.group for h in plan.covered] == ["checkout"] and not plan.reported
    assert not plan.fails_strict()
    uncovered = Planner(**files).plan(scan('_(f"billing.{key}")\n'), {})
    assert [h.group for h in uncovered.reported] == ["billing"] and uncovered.fails_strict()


def test_FRM2_a_named_covered_group_covers():
    hits = scan('_(f"validation.{key}")\n_(f"validation.custom.{key}")\n'
                '_(f"validations.{key}")\n')
    plan = Planner(covered_groups=["validation"]).plan(hits, {})
    assert [h.group for h in plan.covered] == ["validation", "validation.custom"]
    assert [h.group for h in plan.reported] == ["validations"], \
        "a group covered another its name merely starts with"


def test_FRM2_a_line_holding_a_label_placeholder_registers_only_through_the_validation_listing(
        tmp_path):
    source = tmp_path / "en.json"
    source.write_text(json.dumps({"validation": {"required": "%(field_label)s is required.",
                                                 "short": "Too short."}}))
    hits = scan('''
        _("%(field_label)s is required.")
        _("%(field_label)s is too long.") % {"field_label": label}
        _("%(fieldname)s is required.")
    ''')
    plan = Planner(legacy_files=[source]).plan(hits, {})
    assert phrases(plan.via_validation) == [("%(field_label)s is required.", None),
                                            ("{field_label} is too long.", None),
                                            ("{field_label} is required.", "validation")]
    assert phrases(plan.new) == [("%(fieldname)s is required.", None), ("Too short.", "validation")]
    assert not plan.fails_strict()


def test_FRM2_a_sentence_the_listing_lists_is_covered_wherever_it_sits(files):
    listing = TemplateList()
    listing.add("Invalid API key")
    listing.add("Pay now")
    hits = scan('_("Invalid API key")\n_("Something else")\n_("checkout.submit")\n')
    plan = Planner(listing=listing, **files).plan(hits, {})
    assert phrases(plan.via_message_listing) == [("Invalid API key", None)]
    assert ("Something else", None) in phrases(plan.new)
    assert ("Pay now", "checkout") in phrases(plan.with_translations), \
        "a key's line under its group is not the listing's"


@translates_as("status")
class Status(enum.Enum):
    SHIPPED = "Shipped"
    DELIVERED = "Delivered"


def test_FRM2_sync_registers_one_sentence_per_value():
    hits = scan('_("The order is %(status)s.") % {"status": status}\n')
    plan = Planner(value_sets=[Status]).plan(hits, {})
    assert phrases(plan.new) == [("The order is Shipped.", None), ("The order is Delivered.", None)]


def test_FRM2_an_offline_plan_reports_what_a_strict_run_fails_on(files):
    hits = scan('_("Already there")\n_(name)\n_(f"checkout.{k}")\n'
                '_("%(field_label)s is required.")\n_("The order is %(status)s.") % {"status": s}\n')
    plan = Planner.offline(hits, value_sets=[Status], **files)
    assert plan.offline
    assert ("Already there", None) in phrases(plan.new), "an offline plan read a catalog"
    assert ("The order is Shipped.", None) in phrases(plan.new)
    assert ("Pay now", "checkout") in phrases(plan.with_translations)
    assert [h.origin for h in plan.reported] == ["app/views.py:2"]
    assert [h.group for h in plan.covered] == ["checkout"]
    assert phrases(plan.via_validation) == [("%(field_label)s is required.", None)]
    assert plan.fails_strict()


# -- applying a plan -----------------------------------------------------------------------------


class Registrar:
    def __init__(self):
        self.sent: list[list[dict]] = []

    def register_phrases(self, items):
        self.sent.append(list(items))
        return [{"status": True}]


@pytest.fixture
def make_client(monkeypatch):
    def build(catalog, *, writes=True, **options):
        client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(),
                               base_locale="en-us", debounce=0, auto_flush=False, **options)
        reads: list[bool] = []

        def get(locale, use_cache=True):
            reads.append(use_cache)
            return CatalogFetch(catalog, ok=True)

        client._catalog.get = get  # type: ignore[method-assign]
        client._resolve_write_enabled = lambda: writes  # type: ignore[method-assign]
        registrar = Registrar()
        monkeypatch.setattr(LangsysClient, "_reg", property(lambda self: registrar))
        return client, registrar.sent, reads

    return build


def test_FRM2_apply_registers_only_what_the_catalog_lacks(files, make_client):
    client, sent, reads = make_client({U: {"Already there": None}},
                                      legacy_files=files["legacy_files"])
    plan = client.plan_sync(scan('_("Already there")\n_("Brand new")\n_(name)\n'),
                            targets=files["targets"])
    assert reads == [False], "the plan was decided against a cached catalog"
    result = client.apply_sync(plan)
    assert result["success"] is True
    assert sent == [[
        {"phrase": "Pay now", "category": "checkout", "translations": {"es-cr": "Pagar ahora"}},
        {"phrase": "Brand new", "category": None},
        {"phrase": "Cancel order", "category": "checkout"},
        {"phrase": "Shipped today", "category": "orders"},
    ]]


def test_FRM2_a_read_key_applies_nothing_and_says_why(make_client):
    client, sent, _ = make_client({}, writes=False)
    result = client.apply_sync(client.plan_sync(scan('_("Brand new")\n')))
    assert sent == [] and result["success"] is False and result["reason"] == "not-write-enabled"


def test_FRM2_an_offline_plan_is_not_applied(make_client):
    client, sent, _ = make_client({})
    with pytest.raises(ValueError, match="offline"):
        client.apply_sync(Planner.offline(scan('_("Brand new")\n')))
    assert sent == []


# -- the message-template contracts --------------------------------------------------------------


class MaxLength:
    """A rule implementing the core's message-template contract."""

    def __init__(self, max):
        self.max = max
        self.strip = True

    def template(self):
        return "%(field_label)s may hold at most {max} characters."


class MinLength:
    """A rule without it: its message arrives filled."""

    def __init__(self, min):
        self.min = min


class Between:
    def __init__(self):
        self._low = 1

    def template(self):
        return "%(field_label)s must be between {low} and {high}."


class InvalidApiKey:
    """A message the app defines itself, through the app-message-template contract."""

    code = "invalid_api_key"

    @classmethod
    def template(cls):
        return "Invalid API key"


class UniqueSlug:
    """Both a rule and an app message: listed per field only."""

    code = "unique_slug"

    def template(self):
        return "%(field_label)s is taken."


def test_FRM2_a_rule_is_listed_with_its_marker_per_field():
    listing = TemplateList()
    listing.add_rule(MaxLength(10), label="Name", message="Name may hold at most 10 characters.",
                     source="app/forms.py", field="name")
    listing.add_rule(MaxLength(80), label="Email", message="Email may hold at most 80 characters.",
                     source="app/forms.py", field="email")
    assert list(listing) == ["Name may hold at most {max} characters.",
                             "Email may hold at most {max} characters."]
    assert listing.problems == []


def test_FRM2_a_rule_without_a_template_is_listed_from_its_message_and_reported():
    listing = TemplateList()
    listing.add_rule(MinLength(3), label="Name", message="Name needs at least 3 characters.",
                     source="app/forms.py", field="name")
    assert list(listing) == ["Name needs at least 3 characters."]
    [problem] = listing.problems
    assert "HasMessageTemplate" in str(problem) and problem.field == "name"


def test_FRM2_a_marker_without_a_public_attribute_is_reported():
    listing = TemplateList()
    listing.add_rule(Between(), label="Age", message="Age must be between 1 and 9.", field="age")
    assert sorted(p.message.split("{")[1].split("}")[0] for p in listing.problems) == \
        ["high", "low"], "a private attribute filled a marker, or a missing one was not reported"


def test_FRM2_a_listing_problem_fails_strict():
    listing = TemplateList()
    listing.add_rule(MinLength(3), label="Name", message="Name needs at least 3 characters.")
    assert Planner(listing=listing).plan([], {}).fails_strict()
    assert not Planner(listing=TemplateList()).plan([], {}).fails_strict()


def test_FRM2_the_runtime_entry_matches_the_listing():
    rule = MaxLength(10)
    listing = TemplateList()
    listing.add_rule(rule, label="Name", message="Name may hold at most 10 characters.")
    template = rule_template(rule, "Name")
    assert [template] == list(listing)
    assert message_params(rule, template) == {"max": 10}, "a public attribute no marker names"


def test_MSG7_an_app_message_is_listed_once_with_its_code():
    listing = TemplateList()
    listing.add_app_message(InvalidApiKey, source="app/errors.py")
    listing.add_app_message(InvalidApiKey, source="app/errors.py")
    assert list(listing) == ["Invalid API key"] and listing.codes == {
        "Invalid API key": "invalid_api_key"}


@pytest.mark.parametrize("order", ["message-first", "rule-first"])
def test_MSG7_a_class_that_is_a_rule_and_a_message_is_listed_per_field_only(order):
    listing = TemplateList()
    if order == "message-first":
        listing.add_app_message(UniqueSlug())
    listing.add_rule(UniqueSlug(), label="Slug", message="Slug is taken.", field="slug")
    listing.add_rule(UniqueSlug(), label="Handle", message="Handle is taken.", field="handle")
    if order == "rule-first":
        listing.add_app_message(UniqueSlug())
    assert list(listing) == ["Slug is taken.", "Handle is taken."]
    assert listing.problems == [] and listing.codes == {}, \
        "the rule was also listed, or refused, as an app message"


class SlugTaken:
    """A rule whose sentence names no field, also declared as an app message."""

    code = "slug_taken"

    def template(self):
        return "This slug is taken."


def test_MSG7_a_message_later_seen_as_a_rule_loses_its_message_listing():
    listing = TemplateList()
    listing.add_app_message(SlugTaken())
    assert listing.codes == {"This slug is taken.": "slug_taken"}
    listing.add_rule(SlugTaken(), label="Slug", message="This slug is taken.", field="slug")
    assert list(listing) == ["This slug is taken."] and listing.codes == {}, \
        "the class kept its app-message listing beside its per-field one"


def test_MSG7_an_app_message_with_a_label_placeholder_is_refused():
    listing = TemplateList()
    listing.add_app_message(UniqueSlug())
    assert list(listing) == [] and len(listing.problems) == 1


def test_FRM2_the_spec_sample_app_registers_neither_listed_sentence_uncategorised():
    """An error class states `Invalid API key` through the contract, and a rule lists its sentence
    per field: the sync against an empty project registers neither uncategorised, and strict
    passes."""
    listing = TemplateList()
    listing.add_app_message(InvalidApiKey)
    listing.add_rule(MaxLength(10), label="Name", message="Name may hold at most 10 characters.")
    hits = scan('''
        class InvalidApiKey:
            @classmethod
            def template(cls):
                return _("Invalid API key")
        class MaxLength:
            def template(self):
                return _("%(field_label)s may hold at most {max} characters.")
        name_error = _("Name may hold at most {max} characters.")
    ''')
    plan = Planner(listing=listing).plan(hits, {})
    assert plan.new == [] and plan.with_translations == []
    assert not plan.fails_strict()
    assert run_listing([lambda: list(listing)], strict=True, out=_Sink()) == 0


class _Sink:
    def write(self, text):
        return len(text)
