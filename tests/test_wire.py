"""WIRE-1, WIRE-2 and WIRE-5 - the request contract at the transport boundary.

WIRE-3 and WIRE-4 live beside the rules they compose with (`test_gating`,
`test_wire4_degradation`).
"""

from __future__ import annotations

import re
from pathlib import Path

from langsys import LangsysClient
from langsys.cache import MemoryCache

API = "https://api.test/api"
AUTH = f"{API}/authorize-project/proj-1"
ITEMS = f"{API}/translatable-items"
TRANS = re.compile(r"https://api\.test/api/translations")
DOUBLE = "https://double.test/api"
KEY = "raw-key-123"


def make(**kw):
    kw.setdefault("api_url", API)
    return LangsysClient(
        KEY, "proj-1", cache=MemoryCache(), base_locale="en-us", debounce=0, auto_flush=False, **kw
    )


def auth(write_enabled=True):
    return {
        "status": True,
        "data": {
            "id": "proj-1", "title": "T", "base_locale": "en-us",
            "target_locales": ["es-es"], "default_locales": {},
            "key_type": "write", "write_enabled": write_enabled,
            "langsys_settings": {"translatable_items": {"batch_limit": 200}},
        },
    }


# -- WIRE-1 -------------------------------------------------------------------


def test_WIRE1_every_request_authenticates_with_the_x_authorization_header(httpx_mock):
    """A header is not an ambient credential - a third-party page cannot attach it, which is
    why API-key requests are exempt from CSRF server-side. Raw key, no scheme, never a cookie
    or a query parameter, on every endpoint this SDK calls rather than the first one."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)
    client = make()
    client.translate("Save", category="UI", locale="en-us")
    client.flush_pending()

    requests = httpx_mock.get_requests()
    paths = {r.url.path for r in requests}
    assert len(paths) == 3, f"control: authorize, catalog and registration must all be reached: {paths}"
    for request in requests:
        assert request.headers.get("X-Authorization") == KEY, request.url
        assert "authorization" not in request.headers, "a scheme-bearing Authorization header was sent"
        assert "cookie" not in request.headers, "the key travelled as a cookie"
        assert KEY not in str(request.url), "the key travelled in the URL"


# -- WIRE-2 -------------------------------------------------------------------


def test_WIRE2_an_empty_204_is_a_success_not_a_parse_error(httpx_mock):
    """Some endpoints answer 204 with no content-type and a zero-length body. Parsing it
    unconditionally throws on a SUCCESSFUL response - and on the registration path that reads
    as a failure: the queue is kept and the SDK backs off from a server that said yes."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, status_code=204, content=b"")
    client = make()
    client.translate("Save", category="UI", locale="en-us")
    result = client.flush_pending()
    assert result["success"] is True, result
    assert not client.has_pending


def test_WIRE2_control_an_empty_error_body_is_still_a_failure(httpx_mock):
    """Without this, "never raise on an empty body" passes against an implementation that
    reads every empty response as success."""
    httpx_mock.add_response(url=AUTH, json=auth(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}}, is_reusable=True)
    httpx_mock.add_response(url=ITEMS, status_code=500, content=b"")
    client = make()
    client.translate("Save", category="UI", locale="en-us")
    assert client.flush_pending()["success"] is False
    assert client.has_pending


# -- WIRE-5 -------------------------------------------------------------------


def test_WIRE5_the_environment_redirects_the_base_and_a_request_arrives_there(httpx_mock, monkeypatch):
    """"Redirectable" is not testable on its own: proving a seam exists proves nothing about
    whether using it had an effect. So the assertion is that a request ARRIVES at the double."""
    monkeypatch.setenv("LANGSYS_API_URL", DOUBLE)
    httpx_mock.add_response(url=f"{DOUBLE}/authorize-project/proj-1", json=auth())
    client = LangsysClient(KEY, "proj-1", cache=MemoryCache(), debounce=0, auto_flush=False)
    client.authorize()
    assert [str(r.url) for r in httpx_mock.get_requests()] == [f"{DOUBLE}/authorize-project/proj-1"]


def test_WIRE5_an_explicit_api_url_wins_over_the_environment(httpx_mock, monkeypatch):
    monkeypatch.setenv("LANGSYS_API_URL", "https://wrong.test/api")
    httpx_mock.add_response(url=AUTH, json=auth())
    make().authorize()
    assert [r.url.host for r in httpx_mock.get_requests()] == ["api.test"]


def test_WIRE5_the_base_is_read_at_construction_so_a_late_redirect_has_no_effect(httpx_mock, monkeypatch):
    """The ordering half. There is no setter here to call too late; the only late redirect
    available is changing the environment after the client exists, and it is ignored. Pinned
    so that is a stated property rather than a surprise: redirect before constructing."""
    monkeypatch.setenv("LANGSYS_API_URL", API)
    httpx_mock.add_response(url=AUTH, json=auth())
    client = LangsysClient(KEY, "proj-1", cache=MemoryCache(), debounce=0, auto_flush=False)
    monkeypatch.setenv("LANGSYS_API_URL", DOUBLE)
    client.authorize()
    assert [r.url.host for r in httpx_mock.get_requests()] == ["api.test"]


def test_WIRE5_the_seam_is_documented_where_an_integrator_reads():
    """A capability nobody can find is not a capability."""
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    assert "LANGSYS_API_URL" in readme and "`api_url`" in readme
