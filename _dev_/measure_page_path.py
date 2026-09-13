#!/usr/bin/env python3
"""Measure the page path against the shared canonicalization fixture. Read-only.

Runs every fixture row through `translate_page`, reads what is actually queued for
registration, and compares it to the row's expected tokens; then probes top-level shapes the
fixture does not carry. The block path is covered by `tests/test_canonicalization.py`. This is
a measurement for CONFORMANCE.md's *Page path, measured* section, not a test: the divergences
it reports are held for a ruling, not asserted.

    python3 _dev_/measure_page_path.py
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import lxml.etree

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch
from langsys.html.parser import extract_phrases

FIXTURE = Path(__file__).resolve().parent.parent / "tests/fixtures/canonicalization-reference.json"

PROBES = {
    "textarea": "<textarea>Write here</textarea>",
    "select/option": "<select><option>First choice</option><option>Second</option></select>",
    "bare link": '<a href="/x">Read more</a>',
    "bare text": "Loose body text",
    "leaf host title": '<p title="Tooltip">Hello</p>',
    "top-level img alt": '<img alt="Alt text">',
    "link inside a leaf": '<p><a href="/x">Read more</a></p>',
    "option inside a form": "<form><select><option>First choice</option></select></form>",
    "textarea inside a div": "<div><textarea>Write here</textarea></div>",
}


def page_tokens(fragment: str, category: str) -> list[str]:
    """Everything the page path queues, in the order it queued it."""
    client = LangsysClient(
        "k", "p", api_url="https://api.test/api", cache=MemoryCache(), base_locale="en-us",
        debounce=0, auto_flush=False,
    )
    client.set_locale("it-it")
    order: list[str] = []
    queue_phrase, queue_block = client._queue_missing, client._queue_content_block

    def spy_phrase(phrase, category, catalog_category=None):  # type: ignore[no-untyped-def]
        order.append(phrase)
        return queue_phrase(phrase, category, catalog_category)

    def spy_block(html, category, custom_id, phrases):  # type: ignore[no-untyped-def]
        order.extend(phrases)
        return queue_block(html, category, custom_id, phrases)

    page = f"<!doctype html><html><head></head><body>{fragment}</body></html>"
    with patch.object(client._catalog, "get", return_value=CatalogFetch({}, ok=True)), \
            patch.object(client, "_queue_missing", side_effect=spy_phrase), \
            patch.object(client, "_queue_content_block", side_effect=spy_block):
        client.translate_page(page, category=category)
    return order


def main() -> int:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print(f"libxml2 {lxml.etree.LIBXML_VERSION}, lxml {lxml.etree.LXML_VERSION}")
    diverged = []
    for row in data["cases"]:
        got = page_tokens(row["html"], row["category"])
        if got != row["expected_tokens"]:
            diverged.append(row["id"])
            print(f"DIVERGE {row['id']}: expected {row['expected_tokens']!r}, page path {got!r}")
    print(f"page path: {len(data['cases']) - len(diverged)}/{len(data['cases'])} rows match")
    print()
    for name, fragment in PROBES.items():
        print(f"{name:22} page path {page_tokens(fragment, 'UI')!r:34} "
              f"block path {extract_phrases(fragment)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
