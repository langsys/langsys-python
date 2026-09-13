#!/usr/bin/env python3
"""Measure catalog GETs made by repeated lookups while the catalog fetch fails. Read-only.

A failed fetch is deliberately not cached (WIRE-4: caching an outage would serve source text for
the whole TTL). The cost of that choice is what this measures: how many catalog GETs N lookups
make when the answer is a 404, a 500, a 422, a refused connection or a hung upstream, against a
200 control, through `translate()` and through `translate_page`.

    python3 _dev_/measure_catalog_refetch.py
    LANGSYS_API_URL=... LANGSYS_API_KEY=... LANGSYS_PROJECT_ID=... \
        python3 _dev_/measure_catalog_refetch.py
"""

from __future__ import annotations

import os
import socket
import time
from typing import Any, Callable, Optional

import httpx

from langsys import LangsysClient
from langsys.cache import MemoryCache

LOOKUPS = 5
PAGE_TOKENS = 11
PAGE = "<html><body>" + "".join(f"<p>Token {i}</p>" for i in range(PAGE_TOKENS)) + "</body></html>"
Handler = Callable[[httpx.Request], httpx.Response]


def build(api_url: str, handler: Optional[Handler], timeout: float, key: str = "k",
          project: str = "p") -> tuple[LangsysClient, list[float]]:
    client = LangsysClient(key, project, api_url=api_url, cache=MemoryCache(), base_locale="en-us",
                           timeout=timeout, debounce=0, auto_flush=False)
    if handler is not None:
        real = client._http._client
        client._http._client = httpx.Client(transport=httpx.MockTransport(handler),
                                            headers=real.headers, timeout=real.timeout)
        real.close()
    gets: list[float] = []
    send = client._http.get

    def counting_get(path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if path == "translations":
            gets.append(time.monotonic())
        return send(path, params)

    client._http.get = counting_get  # type: ignore[method-assign]
    return client, gets


def answering(status: int) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if status == 200:
            return httpx.Response(200, json={"status": True, "data": {"UI": {}}})
        return httpx.Response(status, json={"error": f"HTTP {status}"})
    return handler


def measure(label: str, make: Callable[[], tuple[LangsysClient, list[float]]], locale: str) -> None:
    client, gets = make()
    started = time.monotonic()
    for i in range(LOOKUPS):
        client.translate(f"Phrase {i}", category="UI", locale=locale)
    lookups_s, lookup_gets = time.monotonic() - started, len(gets)
    queued = client.has_pending
    client.close()

    page_client, page_gets = make()
    page_client.set_locale(locale)
    started = time.monotonic()
    page_client.translate_page(PAGE, category="UI")
    page_s = time.monotonic() - started
    page_client.close()
    print(f"  {label:28} translate x{LOOKUPS}: {lookup_gets:2} GETs {lookups_s:5.2f}s  "
          f"| translate_page ({PAGE_TOKENS} tokens): {len(page_gets):2} GETs {page_s:5.2f}s  "
          f"| queued a miss: {queued}")


def main() -> int:
    api = "https://api.test/api"
    print("catalog GETs per scenario (lookups each use a distinct phrase)")
    measure("200 (control)", lambda: build(api, answering(200), 30.0), "it-it")
    for status in (404, 422, 500):
        measure(f"HTTP {status}",
                lambda status=status: build(api, answering(status), 30.0), "it-it")
    measure("connection refused", lambda: build("http://127.0.0.1:9/api", None, 30.0), "it-it")

    hung = socket.socket()
    hung.bind(("127.0.0.1", 0))
    hung.listen(128)  # accepts into the backlog and never answers
    port = hung.getsockname()[1]
    hung_url = f"http://127.0.0.1:{port}/api"
    measure("hung upstream, timeout 0.5s",
            lambda: build(hung_url, None, 0.5), "it-it")
    hung.close()

    live = os.environ.get("LANGSYS_API_URL")
    if live and os.environ.get("LANGSYS_API_KEY") and os.environ.get("LANGSYS_PROJECT_ID"):
        key, project = os.environ["LANGSYS_API_KEY"], os.environ["LANGSYS_PROJECT_ID"]
        def live_client() -> tuple[LangsysClient, list[float]]:
            return build(live, None, 30.0, key, project)

        measure("live, supported locale", live_client, "es-es")
        measure("live, unsupported locale", live_client, "zz-zz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
