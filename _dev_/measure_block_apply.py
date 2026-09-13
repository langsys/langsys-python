#!/usr/bin/env python3
"""Measure whether block translations land on their own nodes. Read-only.

A fleet SDK substituted translations by POSITION in a second walk that skipped neither marked
nor excluded subtrees and never applied attribute or value tokens, so translations landed in
the wrong place silently. Every token here translates to `[token]`, through the three paths
that apply a block; CONFORMANCE.md's *Block apply path, measured* records the output.

    python3 _dev_/measure_block_apply.py
"""

from __future__ import annotations

import re
from unittest.mock import patch

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.catalog import CatalogFetch
from langsys.html.parser import apply_block_translations, extract_phrases
from langsys.registration import generate_custom_id

VECTORS = [
    '<p><img alt="Hi there"> Body text</p>',
    '<p>Before <button value="Go">Click</button> after</p>',
    '<p><input type="submit" value="Send"> Tail</p>',
    '<p><img alt="A" title="T"> Body</p>',
    '<p>Lead <img alt="Pic"> Trail</p>',
    '<p>Intro <span data-ls-phrase>Marked phrase</span> outro</p>',
    '<p>Intro <span translate="no">Kept</span> outro</p>',
    '<p>One <b>Two</b> Three</p>',
]
STAMP = re.compile(r' data-ls-contentblock="[0-9a-f]+"')


def client() -> LangsysClient:
    return LangsysClient(
        "k", "p", api_url="https://api.test/api", cache=MemoryCache(), base_locale="en-us",
        debounce=0, auto_flush=False,
    )


def main() -> int:
    for vector in VECTORS:
        tokens = extract_phrases(vector)
        bracketed = {token: f"[{token}]" for token in tokens}
        catalog = {"UI": {generate_custom_id("UI", tokens): bracketed, **bracketed}}

        rendered = {"apply_block_translations": apply_block_translations(vector, bracketed)}
        block_client = client()
        served = CatalogFetch(catalog, ok=True)
        with patch.object(block_client._catalog, "get", return_value=served):
            rendered["translate_content_block"] = block_client.translate_content_block(vector, "UI")
        page_client = client()
        with patch.object(page_client._catalog, "get", return_value=served):
            page = page_client.translate_page(f"<html><body>{vector}</body></html>", "UI")
        rendered["translate_page"] = page.split("<body>", 1)[1].split("</body>", 1)[0]

        outputs = sorted({STAMP.sub("", html) for html in rendered.values()})
        notes = []
        if len(outputs) > 1:
            notes.append("PATHS DISAGREE")
        if block_client.has_pending or page_client.has_pending:
            notes.append("MISSED")
        print(f"{vector}\n  tokens   {tokens}")
        print(f"  rendered {' / '.join(outputs)}  {' '.join(notes)}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
