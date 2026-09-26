"""MSG-1..8 and MSG-11 - translation for a framework's own error messages.

The SDK follows the framework's conventions: an entry needs only the framework's unfilled sentence
(`template`) and its `params`; its `field` and `code` are the framework's own, passed through; the
framework's error body is left as it is, with the entries attached beside it. The shared vectors
(`server-message-vectors.json`) pin marker extraction, fill, entry resolution and rendering with
every other SDK. Registration rows run against the contract double.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from contract import IP_WRITE_KEY, PROJECT, READ_KEY, WRITE_KEY, ContractDouble, world

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch
from langsys.messages import (
    DEFAULT_ATTACH_KEY,
    DEFAULT_MESSAGE_CATEGORY,
    TemplateList,
    TemplateProblem,
    TemplateRefused,
    attach_server_messages,
    fill_template,
    resolve_server_messages,
    run_listing,
    server_message,
    template_markers,
)
from langsys.scope import request_scope

VECTORS_FILE = Path(__file__).parent / "fixtures" / "server-message-vectors.json"
#: THE CHECK: the blob. THE PROVENANCE: the ref.
VECTORS_BLOB = "7333e3919dac43af81c6c20bfdba974efd79725b"
VECTORS_REF = "langsys-js-typescript 239166a tests/fixtures/server-message-vectors.json (spec 8.2.18)"
VECTORS = json.loads(VECTORS_FILE.read_text(encoding="utf-8"))
ids = lambda row: row["id"]  # noqa: E731


def offline(catalog=None, **kw) -> tuple[LangsysClient, object]:
    c = LangsysClient("k", "p", api_url="https://api.test/api", cache=MemoryCache(),
                      base_locale="en-us", debounce=0, auto_flush=False, **kw)
    fetch = CatalogFetch(catalog or {}, ok=catalog is not None)
    return c, patch.object(c._catalog, "get", return_value=fetch)


def test_the_vendored_vectors_are_the_pinned_blob():
    data = VECTORS_FILE.read_bytes()
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == VECTORS_BLOB  # noqa: S324


# -- the shared vectors --------------------------------------------------------------------------


@pytest.mark.parametrize("row", VECTORS["markers"], ids=ids)
def test_MSG3_marker_extraction_matches_the_vectors(row):
    assert template_markers(row["template"]) == row["expected"]


@pytest.mark.parametrize("row", VECTORS["fill"], ids=ids)
def test_MSG4_fill_matches_the_vectors(row):
    assert fill_template(row["template"], row["params"]) == row["expected"]


@pytest.mark.parametrize("row", VECTORS["resolve"], ids=ids)
def test_MSG1_entry_resolution_matches_the_vectors(row):
    body = copy.deepcopy(row["body"])
    assert resolve_server_messages(body, **row["options"]) == row["expected"]
    assert body == row["body"], "resolving changed the body"


@pytest.mark.parametrize("row", VECTORS["render"], ids=ids)
def test_MSG5_rendering_matches_the_vectors(row):
    """MSG-5 binds clients; a server core that renders a received entry does it the same way."""
    c, patched = offline(row["catalog"])
    with patched:
        assert c.render_server_message(row["entry"], row["category"], row["locale"]) == row["expected"]
    assert not c.has_pending, "rendering a received entry registered something"


WIRE_PIECES = ("template", "params", "message", "field", "code")


@pytest.mark.parametrize("entry", VECTORS["canonical_entries"],
                         ids=[f"canonical-{i}" for i in range(len(VECTORS["canonical_entries"]))])
def test_MSG4_every_canonical_entry_fills_to_its_message(entry):
    """`framework` and `source` annotate the row; the entry is its wire pieces."""
    assert fill_template(entry["template"], entry.get("params") or {}) == entry["message"]
    built = server_message(entry["template"], entry.get("params"), field=entry.get("field"),
                           code=entry.get("code"))
    assert built == {k: v for k, v in entry.items() if k in WIRE_PIECES}


# -- MSG-1: the entry, and the framework's own error body ---------------------------------------

DJANGO_BODY = {"email": ["Enter a valid email address."], "name": ["This field is required."]}
PYDANTIC_BODY = {"detail": [{"type": "string_too_short", "loc": ["body", "name"],
                             "msg": "String should have at least 3 characters",
                             "input": "Al", "ctx": {"min_length": 3}}]}
ENTRIES = [
    server_message("Enter a valid email address.", field="email", code="invalid"),
    server_message("The name field is required.", field="name", code="required"),
]


@pytest.mark.parametrize("native", [DJANGO_BODY, PYDANTIC_BODY], ids=["django", "pydantic"])
def test_MSG1_entries_attach_beside_the_frameworks_body_which_is_otherwise_unchanged(native):
    body = copy.deepcopy(native)
    attach_server_messages(body, ENTRIES)
    assert {k: v for k, v in body.items() if k != DEFAULT_ATTACH_KEY} == native
    assert resolve_server_messages(body, key=DEFAULT_ATTACH_KEY) == ENTRIES
    assert resolve_server_messages(body, resolver=lambda b: b[DEFAULT_ATTACH_KEY]) == ENTRIES


def test_MSG1_the_attach_key_is_configurable_and_never_overwrites():
    body = attach_server_messages({"errors": {}}, ENTRIES, key="translated")
    assert resolve_server_messages(body, key="translated") == ENTRIES
    with pytest.raises(ValueError):
        attach_server_messages({DEFAULT_ATTACH_KEY: 1}, ENTRIES)


def test_MSG1_the_default_key_is_the_fleets():
    assert DEFAULT_ATTACH_KEY == "langsys_errors"


def test_MSG1_an_entry_needs_only_a_template_and_its_params():
    entry = server_message("At least {min} characters.", {"min": 3})
    assert entry == {"template": "At least {min} characters.", "params": {"min": 3},
                     "message": "At least 3 characters."}
    c, patched = offline({"Errors": {}})
    with patched:
        shown = c.render_server_message({"template": "At least {min}.", "params": {"min": 3}})
    assert shown == "At least 3.", "with no message on the wire, the fallback is the fill"


def test_MSG1_an_entry_with_no_template_is_not_looked_up_and_shows_its_message():
    c, patched = offline({"Errors": {"Bad.": "Male."}})
    with patched:
        assert c.render_server_message({"message": "Bad."}) == "Bad."
    assert resolve_server_messages({"e": [{"message": "Bad.", "code": "x"}]}, key="e") == [
        {"message": "Bad.", "code": "x"}
    ]


def test_MSG1_a_json_string_body_resolves_and_garbage_resolves_to_nothing():
    assert resolve_server_messages(json.dumps({"e": ENTRIES}), key="e") == ENTRIES
    assert resolve_server_messages("<html>500</html>", key="e") == []


def test_MSG1_the_body_is_never_searched_by_shape():
    with pytest.raises(TypeError):
        resolve_server_messages({DEFAULT_ATTACH_KEY: ENTRIES})
    assert resolve_server_messages({"errors": ENTRIES}, key=DEFAULT_ATTACH_KEY) == []
    assert resolve_server_messages({"errors": ENTRIES}, key="errors") == ENTRIES, "control"


# -- MSG-2: the code is the framework's own ------------------------------------------------------


def test_MSG2_the_frameworks_code_and_field_path_pass_through_unchanged():
    entry = server_message("String should have at least {min_length} characters",
                           {"min_length": 3}, field=["body", "name"], code="string_too_short")
    assert entry["code"] == "string_too_short" and entry["field"] == ["body", "name"]
    assert resolve_server_messages({"langsys_errors": [entry]}, key=DEFAULT_ATTACH_KEY) == [entry]


def test_MSG2_a_failure_with_no_identifier_carries_no_code():
    assert "code" not in server_message("Something failed.")
    assert "field" not in server_message("Something failed.")


def test_MSG2_the_code_is_unchanged_across_locales_and_never_chooses_text():
    c, patched = offline({"Errors": {"The name field is required.": "Il campo nome è obbligatorio."}})
    with patched:
        entry = c.server_message("The name field is required.", code="required", field="name")
        c.set_locale("es-es")
        again = c.server_message("The name field is required.", code="required", field="name")
        rendered = c.render_server_message(dict(entry, code="something_else"), locale="it-it")
    assert entry["code"] == again["code"] == "required"
    assert rendered == "Il campo nome è obbligatorio.", "the code chose the text"


def test_MSG2_the_sdk_imposes_no_vocabulary_or_wording():
    import langsys.messages as messages

    for gone in ("MESSAGE_CODES", "size_code", "WORDINGS", "with_label", "LABEL_MARKERS"):
        assert not hasattr(messages, gone), f"{gone} is still exported"


# -- MSG-3 / MSG-11 ------------------------------------------------------------------------------


def test_MSG3_one_template_per_field_is_two_phrases():
    c, patched = offline({"Errors": {}})
    with patched:
        c.server_message("The password field is required.", field="password", code="required")
        c.server_message("The name field is required.", field="name", code="required")
    assert [p["phrase"] for p in c.pending_phrases] == [
        "The password field is required.", "The name field is required."
    ]


def test_MSG3_a_template_with_no_marker_is_its_own_message_and_carries_no_params():
    entry = server_message("This field is required.", {"min": 3})
    assert entry["message"] == entry["template"] and "params" not in entry


@pytest.mark.parametrize("template", [
    "%(model_name)s with this %(field_label)s already exists.",
    "%(model_name)s with this %(field_labels)s already exists.",
    "%(field_label)s must be unique for %(date_field_label)s %(lookup_type)s.",
    "The %(field)s is required.",
    "The {{ field }} is required.",
], ids=["field-label", "field-labels", "date-field-label", "field", "template-field"])
def test_MSG11_a_framework_label_placeholder_is_refused_when_added(template):
    with pytest.raises(TemplateRefused):
        TemplateList().add(template)


@pytest.mark.parametrize("template", [
    "Ensure this value has at least {limit_value} characters.",
    "The email with this Email already exists.",
    "String should have at least {min_length} characters",
    "A 5% discount applies to 3 items.",
], ids=["django-limit", "label-written-in", "pydantic", "percent-prose"])
def test_MSG11_control_the_frameworks_sentences_with_labels_written_in_are_accepted(template):
    listing = TemplateList()
    listing.add(template)
    assert list(listing) == [template]


def test_MSG11_a_binding_names_its_own_frameworks_placeholders():
    """Pydantic's messages carry no label placeholder, so a FastAPI binding passes none."""
    TemplateList(label_placeholders=()).add("The %(field)s is required.")
    with pytest.raises(TemplateRefused):
        TemplateList(label_placeholders=(":attribute",)).add("The :attribute field is required.")


def _marker_warnings(caplog):
    return [r for r in caplog.records if "is itself a phrase in the catalog" in r.getMessage()]


def test_MSG11_a_marker_filled_with_a_catalogued_phrase_warns_once(caplog):
    catalog = {"Orders": {"Shipped": "Enviado"}, "Errors": {}}
    c, patched = offline(catalog)
    with patched, caplog.at_level(logging.WARNING, logger="langsys"):
        for _ in range(3):
            c.server_message("The order is {status}.", {"status": "Shipped"})
    assert len(_marker_warnings(caplog)) == 1


def test_MSG11_control_a_value_the_catalog_has_never_seen_stays_silent(caplog):
    c, patched = offline({"Orders": {"Shipped": "Enviado"}, "Errors": {}})
    with patched, caplog.at_level(logging.WARNING, logger="langsys"):
        c.server_message("The order is {status}.", {"status": "ORD-1234"})
        c.server_message("At least {min}.", {"min": 3})
    assert _marker_warnings(caplog) == []


# -- MSG-4 ---------------------------------------------------------------------------------------


def test_MSG4_a_present_but_null_param_stays_its_marker():
    assert server_message("At least {min} characters.", {"min": None})["message"] == \
        "At least {min} characters."


def test_MSG4_a_numeric_param_is_a_json_number():
    wire = json.dumps(server_message("At least {min} characters.", {"min": 12}))
    assert '"params": {"min": 12}' in wire


@pytest.mark.parametrize("value, printed", [(3.0, "3"), (2.5, "2.5"), (True, "true"), (0, "0")])
def test_MSG4_a_param_prints_as_the_reference_prints_it(value, printed):
    assert fill_template("At least {min}.", {"min": value}) == f"At least {printed}."


# -- MSG-6 ---------------------------------------------------------------------------------------


def test_MSG6_the_category_defaults_to_errors_and_is_configurable():
    assert DEFAULT_MESSAGE_CATEGORY == "Errors"
    c, patched = offline({"Errors": {}, "Validation": {}}, message_category="Validation")
    with patched:
        c.server_message("The name field is required.")
    assert c.pending_phrases == [{"phrase": "The name field is required.", "category": "Validation"}]


# -- MSG-7 / MSG-8 against the contract double -----------------------------------------------------


def _client(double: ContractDouble, key: str = WRITE_KEY) -> LangsysClient:
    return LangsysClient(key, PROJECT, api_url=double.base_url, cache=MemoryCache(),
                         base_locale="en-us", debounce=0, auto_flush=False)


def _provider():
    return ["The password field is required.",
            "Ensure this value has at least {limit_value} characters."]


def test_MSG7_the_listing_registers_every_template_and_a_second_run_nothing_new(double):
    double.seed(world())
    out = io.StringIO()
    assert run_listing([_provider], client=_client(double), register=True, out=out) == 0
    assert double.phrases() == sorted(("Errors", t) for t in _provider())
    again = io.StringIO()
    assert run_listing([_provider], client=_client(double), register=True, out=again) == 0
    assert "0 newly registered" in again.getvalue()


def _unlistable():
    yield "The email field is required."
    yield TemplateProblem("the message is built at runtime", source="app/forms.py:SignupForm",
                          field="email", fix="give the validator an unfilled message")
    yield {"template": "%(model_name)s with this %(field_label)s already exists.",
           "source": "app/models.py", "field": "email"}


def test_MSG7_an_unlistable_message_is_reported_and_the_run_still_exits_zero():
    """MSG-8 registers it the first time it is emitted, so it is advice, not a failure."""
    out = io.StringIO()
    assert run_listing([_unlistable], out=out) == 0
    lines = [line for line in out.getvalue().splitlines() if line.startswith("PROBLEM")]
    assert len(lines) == 2, out.getvalue()
    assert "app/forms.py:SignupForm field 'email'" in lines[0] and "unfilled message" in lines[0]
    assert "%(field_label)s" in lines[1]


def test_MSG7_strict_fails_the_run_on_any_unlistable_message():
    assert run_listing([_unlistable], strict=True, out=io.StringIO()) == 1
    assert run_listing([_provider], strict=True, out=io.StringIO()) == 0, "control"


def test_MSG7_the_command_line_entry_point_lists_and_gates(capsys, monkeypatch):
    import sys
    import types

    from langsys.messages import main

    good = types.ModuleType("msg_provider_good")
    good.templates = lambda: ["The name field is required."]  # type: ignore[attr-defined]
    bad = types.ModuleType("msg_provider_bad")
    bad.templates = lambda: ["The %(field)s is required."]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msg_provider_good", good)
    monkeypatch.setitem(sys.modules, "msg_provider_bad", bad)
    assert main(["--provider", "msg_provider_good:templates"]) == 0
    assert "The name field is required." in capsys.readouterr().out
    assert main(["--provider", "msg_provider_bad:templates"]) == 0
    assert "PROBLEM" in capsys.readouterr().out
    assert main(["--provider", "msg_provider_bad:templates", "--strict"]) == 1


def test_MSG8_an_unlisted_template_is_registered_after_the_response_not_before(double):
    double.seed(world())
    c = _client(double)
    with request_scope():
        c.server_message("Archived is not a valid status.", field="status")
        c.flush_pending()
        assert double.phrases() == [], "registered on the request path"
    assert c.flush_pending()["success"] is True
    assert double.phrases() == [("Errors", "Archived is not a valid status.")]


def test_MSG8_a_listed_template_is_not_registered_again(double):
    double.seed(world(phrases=[{"category": "Errors", "phrase": "The name field is required."}]))
    c = _client(double)
    c.server_message("The name field is required.", field="name")
    assert not c.has_pending


def test_MSG8_a_session_that_cannot_write_registers_nothing_even_once_it_could(double):
    """Drift: told no, the allow-list widens, and still nothing lands. Control acts in the new world."""
    double.seed(world())
    c = _client(double, IP_WRITE_KEY)
    c.server_message("Something failed.")
    c.flush_pending()
    double.seed(world(ip_allowlist=["127.0.0.1"]))
    c.flush_pending(force=True)
    assert double.phrases() == []
    control = _client(double, IP_WRITE_KEY)
    control.server_message("Something failed.")
    assert control.flush_pending()["success"] is True
    assert double.phrases() == [("Errors", "Something failed.")]


def test_MSG8_a_read_key_reports_the_skip(double):
    double.seed(world())
    c = _client(double, READ_KEY)
    c.server_message("Something failed.")
    assert c.flush_pending()["success"] is False
