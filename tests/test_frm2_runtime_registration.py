"""FRM-2's runtime switch: a binding that registers through its sync command turns runtime
registration off, and then the translate function and message emission register nothing while a
request is served. The page walk and rendered fragments still collect, and FRM-7's one exception
still registers (`test_frm7_value_sets`).
"""

from __future__ import annotations

import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch

API = "https://api.test/api"


def make(runtime_registration):
    client = LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), base_locale="en-us",
                           debounce=0, auto_flush=False, runtime_registration=runtime_registration)
    client.set_locale("es-cr")
    client._catalog.get = lambda locale, use_cache=True: CatalogFetch(  # type: ignore[method-assign]
        {"Errors": {}}, ok=True)
    return client


def runtime_calls(client):
    client.translate("Welcome back")
    client.translate("Hello {name}", category="Home", params={"name": "Ana"})
    client.resolve("Your basket")
    client.translate_legacy("%(count)d file", entry_point="ngettext", plural="%(count)d files",
                            params={"count": 2})
    client.server_message("Archived is not a valid status.", field="status")


@pytest.mark.parametrize(("on", "expected"), [
    (False, []),
    (True, ["Welcome back", "Hello {name}", "Your basket",
            "{count, plural, =1 {# file} other {# files}}", "Archived is not a valid status."]),
], ids=["off", "on"])
def test_FRM2_with_runtime_registration_off_the_translate_function_registers_nothing(on, expected):
    client = make(on)
    runtime_calls(client)
    assert [p["phrase"] for p in client.pending_phrases] == expected


def test_FRM2_with_runtime_registration_off_the_page_walk_still_collects():
    client = make(False)
    client.translate_page("<html><body><p>Free returns</p></body></html>")
    client.translate_content_block("<div><span>Buy now</span><span>Pay later</span></div>")
    client.translate_content_block("<p>One phrase</p>")
    assert [p["phrase"] for p in client.pending_phrases] == ["Free returns", "One phrase"]
    assert len(client.pending_content_blocks) == 1
