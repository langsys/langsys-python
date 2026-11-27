"""WIRE-4: a core built without credentials is unconfigured, and constructing it does not throw.

A fresh clone, a CI job and a test suite all run without a key. An unconfigured core renders source
text through the failed-catalog path, so nothing is fetched, queued or recorded, and it says once
per process which settings are missing. An explicit write throws. `require_credentials` restores
the exception at the point of first use.

Every "no request was sent" below is read from the transport (`httpx_mock.get_requests()`), and the
same call on a configured client is the positive control that the call does send.
"""

from __future__ import annotations

import re

import pytest

import langsys.client as client_module
from langsys import LangsysClient, NotConfiguredError
from langsys.cache import MemoryCache
from langsys.exceptions import ConfigurationError

API = "https://api.test/api"
TRANS = re.compile(r"https://api\.test/api/translations.*")
PAGE = "<html><body><p>Hello, world</p><p title=\"Greeting\">Welcome back</p></body></html>"
BLOCK = "<div><p>Block one</p><p>Block two</p></div>"


@pytest.fixture(autouse=True)
def _no_env_credentials(monkeypatch):
    for name in ("LANGSYS_API_KEY", "LANGSYS_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(client_module, "_UNCONFIGURED_NOTICE", set(), raising=False)


def unconfigured(**kw):
    return LangsysClient(api_url=API, cache=MemoryCache(), base_locale="en-us", debounce=0,
                         auto_flush=False, **kw)


def configured(**kw):
    return LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                         debounce=0, auto_flush=False, **kw)


def _notices(caplog):
    """The process's one unconfigured-client warning, by its own wording."""
    return [r for r in caplog.records
            if r.levelname == "WARNING" and "until the client is configured" in r.getMessage()]


# -- construction and reads ---------------------------------------------------------------------


@pytest.mark.parametrize("credentials", [{}, {"api_key": "", "project_id": ""}])
def test_WIRE4_constructing_without_credentials_does_not_throw(credentials):
    client = unconfigured(**credentials)
    assert client.is_configured() is False


def test_WIRE4_control_a_client_with_credentials_is_configured():
    assert configured().is_configured() is True


def test_WIRE4_credentials_from_the_environment_configure_the_client(monkeypatch):
    monkeypatch.setenv("LANGSYS_API_KEY", "k")
    monkeypatch.setenv("LANGSYS_PROJECT_ID", "proj-1")
    assert unconfigured().is_configured() is True


def test_WIRE4_an_unconfigured_translate_renders_source_with_its_params_filled(httpx_mock):
    client = unconfigured()
    out = client.translate("Hello, {name}!", category="UI", params={"name": "Sarah"}, locale="it-it")
    assert out == "Hello, Sarah!"
    assert httpx_mock.get_requests() == [], "an unconfigured client sent a request"
    assert client.has_pending is False, "an unconfigured client queued a miss"


def test_WIRE4_control_a_configured_translate_fetches_and_queues(httpx_mock):
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True, "data": {"UI": {}}})
    client = configured()
    assert client.translate("Hello, {name}!", category="UI", params={"name": "Sarah"},
                            locale="it-it") == "Hello, Sarah!"
    assert len(httpx_mock.get_requests()) == 1
    assert client.has_pending is True


def test_WIRE4_an_unconfigured_page_translation_returns_the_page_as_given(httpx_mock):
    client = unconfigured()
    client.set_locale("it-it")
    assert client.translate_page(PAGE, category="UI") == PAGE
    assert client.translate_content_block(BLOCK, category="UI") == BLOCK
    assert httpx_mock.get_requests() == []
    assert client.has_pending is False


def test_WIRE4_a_missing_project_id_alone_leaves_the_client_unconfigured(httpx_mock, caplog):
    client = unconfigured(api_key="k")
    assert client.is_configured() is False
    with caplog.at_level("WARNING", logger="langsys"):
        assert client.translate("Save", category="UI", locale="it-it") == "Save"
    assert httpx_mock.get_requests() == []
    (notice,) = [r.getMessage() for r in _notices(caplog)] or [""]
    assert "project id" in notice and "API key" not in notice, notice


def test_WIRE4_one_warning_per_process_names_the_missing_settings(caplog):
    with caplog.at_level("WARNING", logger="langsys"):
        for _ in range(2):
            client = unconfigured()
            client.translate("Save", category="UI", locale="it-it")
            client.translate("Cancel", category="UI", locale="it-it")
    notices = _notices(caplog)
    assert len(notices) == 1, [r.getMessage() for r in caplog.records]
    message = notices[0].getMessage()
    assert "API key" in message and "project id" in message, message


# -- writes ------------------------------------------------------------------------------------


@pytest.mark.parametrize("call", [
    lambda c: c.register_phrases(["Save"]),
    lambda c: c.register_content_block(BLOCK, ["Block one", "Block two"], category="UI"),
    lambda c: c.sync(["Save"], locale="it-it"),
    lambda c: c.register_templates(["The {attribute} field is required."]),
], ids=["register_phrases", "register_content_block", "sync", "register_templates"])
def test_WIRE4_an_explicit_write_throws_saying_the_client_is_not_configured(httpx_mock, call):
    client = unconfigured()
    with pytest.raises(NotConfiguredError, match="not configured"):
        call(client)
    assert httpx_mock.get_requests() == []


def test_WIRE4_the_not_configured_error_is_a_configuration_error():
    assert issubclass(NotConfiguredError, ConfigurationError)


def test_WIRE4_the_capability_settles_as_not_writable_and_a_flush_reports_why(httpx_mock, caplog):
    client = unconfigured()
    with caplog.at_level("WARNING", logger="langsys"):
        assert client.can_write is False
    assert [r.getMessage() for r in caplog.records
            if r.levelname == "WARNING" and r not in _notices(caplog)] == [], "the answer was asked for"
    result = client.flush_pending()
    assert (result["success"], result["skipped"], result["reason"]) == (False, True, "not-configured")
    assert httpx_mock.get_requests() == []


# -- require_credentials -------------------------------------------------------------------------


def test_WIRE4_require_credentials_still_constructs():
    assert unconfigured(require_credentials=True).is_configured() is False


@pytest.mark.parametrize("call", [
    lambda c: c.translate("Save", category="UI", locale="it-it"),
    lambda c: c.translate_page(PAGE, category="UI"),
    lambda c: c.translate_content_block(BLOCK, category="UI"),
    lambda c: c.get_translations("it-it"),
], ids=["translate", "translate_page", "translate_content_block", "get_translations"])
def test_WIRE4_require_credentials_throws_at_the_first_use(httpx_mock, call):
    client = unconfigured(require_credentials=True)
    client.set_locale("it-it")
    with pytest.raises(NotConfiguredError, match="API key"):
        call(client)
    assert httpx_mock.get_requests() == []


def test_WIRE4_control_require_credentials_with_credentials_translates(httpx_mock):
    httpx_mock.add_response(url=TRANS, json={"status": True, "write_enabled": True,
                                             "data": {"UI": {"Save": "Salva"}}})
    assert configured(require_credentials=True).translate("Save", category="UI", locale="it-it") == "Salva"


def test_WIRE4_resolving_a_request_locale_unconfigured_sends_nothing_and_adds_no_warning(
    httpx_mock, caplog
):
    client = unconfigured()
    with caplog.at_level("WARNING", logger="langsys"):
        choice = client.resolve_request_locale(url="it-it", accept_language="it-IT,it;q=0.9")
    assert choice.locale == "en-us", choice
    assert httpx_mock.get_requests() == []
    others = [r.getMessage() for r in caplog.records
              if r.levelname == "WARNING" and r not in _notices(caplog)]
    assert others == []
