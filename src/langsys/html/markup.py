"""A phrase host's inline markup, encoded so its content registers as ONE phrase.

`<p data-ls-phrase>Based on {n} <strong>reviews</strong></p>` registers
`Based on {n} {m0o}reviews{m0c}`: one catalog entry, so the count and the noun it inflects are in
the same phrase and an ICU plural can select the right form. The wire format is the JS core's
`<Phrase>` and the PHP SDK's, so an entry registered by any of them is read by the others.

Slots are numbered by one counter across the phrase, depth-first pre-order, claimed before
recursing. A slot holds the element's tag and attributes only, never its children, so a stored
phrase carries no build-specific markup. A subtree that is not this phrase's prose - `<script>`,
`translate="no"`, a nested marked host (MARK-4) - is kept whole as an empty token pair and
contributes nothing to the phrase.
"""

from __future__ import annotations

import copy
import re
from typing import Optional

from lxml.etree import _Element

from .parser import SKIP_TAGS, is_marked_host, normalize_phrase

__all__ = ["encode_phrase_host", "render_phrase_host"]

_TOKEN = re.compile(r"\{m(\d+)([oc])\}")


def _opaque(el: _Element) -> bool:
    tag = el.tag.lower() if isinstance(el.tag, str) else ""
    if tag in SKIP_TAGS or el.get("translate") == "no" or el.get("data-notrans"):
        return True
    return is_marked_host(el)


def encode_phrase_host(el: _Element) -> tuple[str, list[_Element]]:
    """The one phrase this host's children form, and the slots that rebuild its markup."""
    slots: list[_Element] = []
    return normalize_phrase(_encode_children(el, slots)), slots


def _encode_children(node: _Element, slots: list[_Element]) -> str:
    out = node.text or ""
    for child in node:
        if isinstance(child.tag, str):
            index = len(slots)
            if _opaque(child):
                kept = copy.deepcopy(child)
                kept.tail = None
                slots.append(kept)
                out += f"{{m{index}o}}{{m{index}c}}"
            else:
                attrib = {str(k): str(v) for k, v in child.attrib.items()}
                slots.append(child.makeelement(child.tag, attrib))
                out += f"{{m{index}o}}" + _encode_children(child, slots) + f"{{m{index}c}}"
        out += child.tail or ""
    return out


def render_phrase_host(el: _Element, translated: str, slots: list[_Element]) -> None:
    """Write a translated phrase back into its host, placing each element where its tokens now sit.

    A translation that reorders tokens works by construction. One that drops a token, leaves them
    unbalanced or names a slot that does not exist loses the markup and keeps the meaning: the
    host gets the text with every token removed.
    """
    rebuilt = _rebuild(translated, slots)
    for child in list(el):
        el.remove(child)
    if rebuilt is None:
        el.text = _TOKEN.sub("", translated)
        return
    text, children = rebuilt
    el.text = text
    for child in children:
        el.append(child)


def _append_text(container: _Element, text: str) -> None:
    if not text:
        return
    if len(container):
        last = container[-1]
        last.tail = (last.tail or "") + text
    else:
        container.text = (container.text or "") + text


def _rebuild(text: str, slots: list[_Element]) -> Optional[tuple[str, list[_Element]]]:
    root = slots[0].makeelement("root", {}) if slots else None
    if root is None:
        return (_TOKEN.sub("", text), []) if _TOKEN.search(text) else (text, [])
    stack: list[tuple[int, _Element]] = []
    offset = 0
    for match in _TOKEN.finditer(text):
        container = stack[-1][1] if stack else root
        _append_text(container, text[offset:match.start()])
        offset = match.end()
        index, kind = int(match.group(1)), match.group(2)
        if index >= len(slots):
            return None
        if kind == "o":
            element = copy.deepcopy(slots[index])
            container.append(element)
            stack.append((index, element))
        else:
            if not stack or stack[-1][0] != index:
                return None
            stack.pop()
    if stack:
        return None
    _append_text(root, text[offset:])
    return root.text or "", list(root)
