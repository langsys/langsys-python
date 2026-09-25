"""MSG-1..8 and MSG-11 - server messages: entries, fill, the template list, registration.

The shared vectors (`server-message-vectors.json`, authored by the JS core) pin marker extraction,
fill, entry resolution and rendering byte-for-byte with every other SDK. Registration rows run
against the contract double and assert on accepted state.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from contract import IP_WRITE_KEY, PROJECT, READ_KEY, WRITE_KEY, ContractDouble, world

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch
from langsys.messages import (
    DEFAULT_MESSAGE_CATEGORY,
    MESSAGE_CODES,
    TemplateList,
    TemplateProblem,
    TemplateRefused,
    fill_template,
    resolve_server_messages,
    run_listing,
    server_message,
    size_code,
    template_markers,
)
from langsys.scope import request_scope

VECTORS_FILE = Path(__file__).parent / "fixtures" / "server-message-vectors.json"
#: THE CHECK: the blob. THE PROVENANCE: the ref.
VECTORS_BLOB = "c8125549cfee0f5286f79a8cbc194cd30ccd446e"
VECTORS_REF = "langsys-js-typescript tests/fixtures/server-message-vectors.json (spec 8.2.9)"
VECTORS = json.loads(VECTORS_FILE.read_text(encoding="utf-8"))
ids = lambda row: row["id"]  # noqa: E731


def slug(text: str) -> str:
    """A parametrize id with no spaces: the mutation runner reads names up to the first one."""
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


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
    assert resolve_server_messages(row["body"], key=row.get("key")) == row["expected"]


@pytest.mark.parametrize("row", VECTORS["render"], ids=ids)
def test_MSG5_rendering_matches_the_vectors(row):
    """MSG-5 binds clients; a server core that renders a received entry does it the same way."""
    c, patched = offline(row["catalog"])
    with patched:
        assert c.render_server_message(row["entry"], row["category"], row["locale"]) == row["expected"]
    assert not c.has_pending, "rendering a received entry registered something"


@pytest.mark.parametrize("entry", VECTORS["canonical_entries"], ids=lambda e: e["code"])
def test_MSG4_every_canonical_entry_fills_to_its_message(entry):
    assert fill_template(entry["template"], entry.get("params") or {}) == entry["message"]
    assert server_message(entry["code"], entry["template"], entry.get("params"),
                          entry.get("field")) == entry


# -- MSG-1 ---------------------------------------------------------------------------------------


def test_MSG1_the_default_envelope_and_a_foreign_one_resolve_to_the_same_entries():
    entries = VECTORS["canonical_entries"][2:]
    langsys_body = {"status": False, "error": {"code": "validation_failed",
                    "message": "Failed.", "template": "Failed.", "errors": entries}}
    house = {"problems": [{"path": e.get("field"), "slug": e["code"], "text": e["message"],
                           "sentence": e["template"], "values": e.get("params")} for e in entries]}

    def resolver(body):
        return [{"field": p["path"], "code": p["slug"], "message": p["text"],
                 "template": p["sentence"], "params": p["values"]} for p in body["problems"]]

    assert resolve_server_messages(house, resolver=resolver) == resolve_server_messages(langsys_body)[1:]


def test_MSG1_a_json_string_body_resolves_and_garbage_resolves_to_nothing():
    body = json.dumps({"errors": VECTORS["canonical_entries"][2:3]})
    assert resolve_server_messages(body) == VECTORS["canonical_entries"][2:3]
    assert resolve_server_messages("<html>500</html>") == []


# -- MSG-2 ---------------------------------------------------------------------------------------


def test_MSG2_the_vocabulary_is_the_spec_list_in_order():
    assert MESSAGE_CODES[0] == "required" and MESSAGE_CODES[-1] == "invalid"
    assert len(MESSAGE_CODES) == len(set(MESSAGE_CODES)) == 21


@pytest.mark.parametrize("value, small, large", [
    ("abc", "too_short", "too_long"),
    (3, "too_small", "too_large"),
    (2.5, "too_small", "too_large"),
    ([1], "too_few", "too_many"),
])
def test_MSG2_a_size_rule_picks_its_code_by_the_field_type(value, small, large):
    assert (size_code(value, "small"), size_code(value, "large")) == (small, large)


def test_MSG2_the_code_does_not_move_with_the_locale_or_the_wording():
    c, patched = offline({"Errors": {}})
    with patched:
        before = c.server_message("too_short", "The password must be at least {min} characters.", {"min": 8})
        c.set_locale("es-es")
        after = c.server_message("too_short", "Your password needs {min} characters or more.", {"min": 8})
    assert before["code"] == after["code"] == "too_short"


# -- MSG-3 / MSG-11: the template list refuses what it can see is wrong --------------------------


def test_MSG3_one_template_per_field_is_two_phrases():
    c, patched = offline({"Errors": {}})
    with patched:
        c.server_message("required", "The password is required.", field="password")
        c.server_message("required", "The name is required.", field="name")
    assert [p["phrase"] for p in c.pending_phrases] == ["The password is required.", "The name is required."]


def test_MSG3_a_template_with_no_marker_is_its_own_message_and_carries_no_params():
    entry = server_message("required", "The password is required.", {"min": 3})
    assert entry["message"] == entry["template"] and "params" not in entry


@pytest.mark.parametrize("template", [
    "The {field} is required.",
    "{attribute} is invalid.",
    "Pick one of {values}.",
    "The :attribute field is required.",
    "The {{ field }} is required.",
    "The %(field)s is required.",
    "%s is required.",
    "The {0} is required.",
], ids=["label-field", "label-attribute", "label-values", "laravel-colon", "double-brace",
        "percent-named", "percent-positional", "str-format-positional"])
def test_MSG11_a_label_marker_or_a_leftover_placeholder_is_refused_when_added(template):
    with pytest.raises(TemplateRefused):
        TemplateList().add(template)


@pytest.mark.parametrize("template", [
    "The password must be at least {min} characters.",
    "Between {min} and {max} characters.",
    "It is 10:30 and the ratio is 3:1.",
    "Use a 100% unique name.",
    "A 5% discount applies to 3 items.",
    "Up to 50% off, 10% for members.",
    "{count, plural, one {Select # item.} other {Select # items.}}",
])
def test_MSG11_control_ordinary_templates_are_accepted(template):
    listing = TemplateList()
    listing.add(template)
    assert list(listing) == [template]


def _marker_warnings(caplog):
    return [r for r in caplog.records if "is itself a phrase in the catalog" in r.getMessage()]


def test_MSG11_a_marker_filled_with_a_catalogued_phrase_warns_once(caplog):
    catalog = {"Orders": {"Shipped": "Enviado"}, "Errors": {}}
    c, patched = offline(catalog)
    with patched, caplog.at_level(logging.WARNING, logger="langsys"):
        for _ in range(3):
            c.server_message("invalid_option", "The order is {status}.", {"status": "Shipped"})
    assert len(_marker_warnings(caplog)) == 1


def test_MSG11_control_a_value_the_catalog_has_never_seen_stays_silent(caplog):
    c, patched = offline({"Orders": {"Shipped": "Enviado"}, "Errors": {}})
    with patched, caplog.at_level(logging.WARNING, logger="langsys"):
        c.server_message("invalid_option", "The order is {status}.", {"status": "ORD-1234"})
        c.server_message("too_small", "At least {min}.", {"min": 3})
    assert _marker_warnings(caplog) == []


def test_MSG4_a_present_but_null_param_stays_its_marker():
    assert server_message("too_short", "At least {min} characters.", {"min": None})["message"] == \
        "At least {min} characters."


def test_MSG4_a_numeric_param_is_a_json_number():
    wire = json.dumps(server_message("too_short", "At least {min} characters.", {"min": 12}))
    assert '"params": {"min": 12}' in wire


# -- MSG-6 ---------------------------------------------------------------------------------------


def test_MSG6_the_category_defaults_to_errors_and_is_configurable():
    assert DEFAULT_MESSAGE_CATEGORY == "Errors"
    c, patched = offline({"Errors": {}, "Validation": {}}, message_category="Validation")
    with patched:
        c.server_message("required", "The name is required.")
    assert c.pending_phrases == [{"phrase": "The name is required.", "category": "Validation"}]


# -- MSG-7 / MSG-8 against the contract double -----------------------------------------------------


def _client(double: ContractDouble, key: str = WRITE_KEY) -> LangsysClient:
    return LangsysClient(key, PROJECT, api_url=double.base_url, cache=MemoryCache(),
                         base_locale="en-us", debounce=0, auto_flush=False)


def _provider():
    return ["The password is required.", "The password must be at least {min} characters."]


def test_MSG7_the_listing_registers_every_template_and_a_second_run_nothing_new(double):
    double.seed(world())
    out = io.StringIO()
    assert run_listing([_provider], client=_client(double), register=True, out=out) == 0
    assert double.phrases() == sorted(("Errors", t) for t in _provider())
    again = io.StringIO()
    assert run_listing([_provider], client=_client(double), register=True, out=again) == 0
    assert "0 newly registered" in again.getvalue()


def test_MSG7_an_unlistable_message_fails_the_run_with_an_actionable_line():
    def provider():
        yield "The email is required."
        yield TemplateProblem("closure rule has no template", source="app/forms.py:SignupForm",
                              field="email", fix="give the rule a template")
        yield {"template": "The {field} is required.", "source": "app/forms.py", "field": "name"}

    out = io.StringIO()
    assert run_listing([provider], out=out) == 1
    lines = [line for line in out.getvalue().splitlines() if line.startswith("PROBLEM")]
    assert len(lines) == 2, out.getvalue()
    assert "app/forms.py:SignupForm field 'email'" in lines[0] and "give the rule a template" in lines[0]
    assert "{field}" in lines[1]


def test_MSG8_an_unlisted_template_is_registered_after_the_response_not_before(double):
    double.seed(world())
    c = _client(double)
    with request_scope():
        c.server_message("invalid_option", "Archived is not a valid status.", field="status")
        c.flush_pending()
        assert double.phrases() == [], "registered on the request path"
    assert c.flush_pending()["success"] is True
    assert double.phrases() == [("Errors", "Archived is not a valid status.")]


def test_MSG8_a_listed_template_is_not_registered_again(double):
    double.seed(world(phrases=[{"category": "Errors", "phrase": "The name is required."}]))
    c = _client(double)
    c.server_message("required", "The name is required.", field="name")
    assert not c.has_pending


def test_MSG8_a_session_that_cannot_write_registers_nothing_even_once_it_could(double):
    """Drift: told no, the allow-list widens, and still nothing lands. Control acts in the new world."""
    double.seed(world())
    c = _client(double, IP_WRITE_KEY)
    c.server_message("invalid", "Something failed.")
    c.flush_pending()
    double.seed(world(ip_allowlist=["127.0.0.1"]))
    c.flush_pending(force=True)
    assert double.phrases() == []
    control = _client(double, IP_WRITE_KEY)
    control.server_message("invalid", "Something failed.")
    assert control.flush_pending()["success"] is True
    assert double.phrases() == [("Errors", "Something failed.")]


def test_MSG8_a_read_key_reports_the_skip(double):
    double.seed(world())
    c = _client(double, READ_KEY)
    c.server_message("invalid", "Something failed.")
    assert c.flush_pending()["success"] is False


@pytest.mark.parametrize("value, printed", [(3.0, "3"), (2.5, "2.5"), (True, "true"), (0, "0")])
def test_MSG4_a_param_prints_as_the_reference_prints_it(value, printed):
    """The server fills `message` with the reference's rules; a client disagreeing on how 3.0 or
    True print would fill a different sentence."""
    assert fill_template("At least {min}.", {"min": value}) == f"At least {printed}."


def test_MSG7_the_command_line_entry_point_lists_and_gates(capsys, monkeypatch):
    import sys
    import types

    from langsys.messages import main

    good = types.ModuleType("msg_provider_good")
    good.templates = lambda: ["The name is required."]  # type: ignore[attr-defined]
    bad = types.ModuleType("msg_provider_bad")
    bad.templates = lambda: ["The {label} is required."]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msg_provider_good", good)
    monkeypatch.setitem(sys.modules, "msg_provider_bad", bad)
    assert main(["--provider", "msg_provider_good:templates"]) == 0
    assert "The name is required." in capsys.readouterr().out
    assert main(["--provider", "msg_provider_good:templates", "--provider", "msg_provider_bad:templates"]) == 1
    assert "PROBLEM" in capsys.readouterr().out
