import json
import re

import pytest

from langsys import AuthorizationError, LangsysClient
from langsys.cache import MemoryCache

API = "https://api.test/api"
AUTH = f"{API}/authorize-project/proj-1"
ITEMS = f"{API}/translatable-items"
TRANS = re.compile(r"https://api\.test/api/translations")


def make(**kw):
    return LangsysClient(
        "k", "proj-1", api_url=API, cache=MemoryCache(), debounce=0, auto_flush=False, **kw
    )


def auth(key_type):
    return {
        "status": True,
        "data": {
            "id": "proj-1",
            "title": "T",
            "base_locale": "en-us",
            "target_locales": [],
            "default_locales": {},
            "key_type": key_type,
            "write_enabled": key_type == "write",
            "langsys_settings": {"translatable_items": {"batch_limit": 200}},
        },
    }


def _items_body(httpx_mock):
    req = next(r for r in httpx_mock.get_requests() if r.url.path.endswith("translatable-items"))
    return json.loads(req.content)


def test_register_requires_write_key(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("read"), is_reusable=True)
    with pytest.raises(AuthorizationError):
        make().register_phrases(["x"])


def test_register_phrases_posts_items(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("write"), is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True})
    make().register_phrases([{"phrase": "Save", "category": "UI"}])
    body = _items_body(httpx_mock)
    assert body["project_id"] == "proj-1"
    assert body["translatable_items"][0]["phrase"] == "Save"


def test_flush_pending_registers_and_clears_on_write(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("write"), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}})
    httpx_mock.add_response(url=ITEMS, json={"status": True})
    client = make()
    assert client.translate("Save", category="UI", locale="en-US") == "Save"  # missing -> queued
    assert client.has_pending
    result = client.flush_pending()
    assert result["phrases"] == 1 and result["success"] is True
    assert client.has_pending is False
    assert _items_body(httpx_mock)["translatable_items"][0]["phrase"] == "Save"


def test_flush_pending_skips_on_read_key(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("read"), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {}}})
    client = make()
    client.translate("Save", category="UI", locale="en-US")
    result = client.flush_pending()
    assert result.get("skipped") is True
    assert client.has_pending is False


def test_sync_registers_only_new_phrases(httpx_mock):
    httpx_mock.add_response(url=AUTH, json=auth("write"), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json={"status": True, "data": {"UI": {"Existing": "E"}}})
    httpx_mock.add_response(url=ITEMS, json={"status": True})
    httpx_mock.add_response(
        url=TRANS, json={"status": True, "data": {"UI": {"Existing": "E", "New": None}}}
    )
    client = make()
    result = client.sync(
        [{"phrase": "Existing", "category": "UI"}, {"phrase": "New", "category": "UI"}],
        locale="en-US",
    )
    assert result["new_phrases"] == ["New"] and result["synced"] is True
    assert _items_body(httpx_mock)["translatable_items"][0]["phrase"] == "New"
