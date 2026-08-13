"""HTML phrase extraction and translation-application.

A faithful port of the PHP SDK's ``HtmlParser`` + ``walkAndTranslateBlock``: it walks
the DOM, collects translatable text (text nodes, a fixed set of attributes, and
button/submit values), skips ``translate="no"`` / ``data-notrans`` subtrees, and
normalizes whitespace. ``apply_block_translations`` walks the same way and substitutes
translations back, preserving leading/trailing whitespace.

Requires lxml (``pip install langsys[html]``).
"""

from __future__ import annotations

import re
from typing import Optional, Sequence, cast

from .attributes import DEFAULT_TRANSLATABLE_ATTRIBUTES

try:
    from lxml import html as lxml_html
    from lxml.etree import _Element
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "HTML translation requires lxml. Install with: pip install langsys[html]"
    ) from exc

__all__ = [
    "DEFAULT_TRANSLATABLE_ATTRIBUTES",
    "extract_phrases",
    "apply_block_translations",
    "apply_element",
    "inner_html",
    "text_content",
    "normalize_whitespace",
]

_WS = re.compile(r"\s+")


def normalize_whitespace(text: Optional[str]) -> str:
    return _WS.sub(" ", text).strip() if text else ""


def _parse_fragment(html: str) -> _Element:
    return cast("_Element", lxml_html.fragment_fromstring(html, create_parent="div"))


def _skip(el: _Element) -> bool:
    return el.get("translate") == "no" or bool(el.get("data-notrans"))


def _tag(el: _Element) -> str:
    return el.tag.lower() if isinstance(el.tag, str) else ""


def _button_value(el: _Element) -> Optional[str]:
    tag = _tag(el)
    if tag == "button" and el.get("value"):
        return normalize_whitespace(el.get("value"))
    if (
        tag == "input"
        and el.get("value")
        and (el.get("type") or "").lower() in ("submit", "button")
    ):
        return normalize_whitespace(el.get("value"))
    return None


def extract_phrases(html: str, attributes: Optional[Sequence[str]] = None) -> list[str]:
    """Extract ordered translatable phrases (duplicates preserved), like the PHP SDK."""
    if not html:
        return []
    attrs = tuple(attributes) if attributes is not None else DEFAULT_TRANSLATABLE_ATTRIBUTES
    phrases: list[str] = []
    _walk_extract(_parse_fragment(html), attrs, phrases)
    return phrases


def _walk_extract(el: _Element, attrs: Sequence[str], out: list[str]) -> None:
    if not isinstance(el.tag, str):  # comments / processing instructions
        return
    if _skip(el):
        return
    for attr in attrs:
        value = el.get(attr)
        if value:
            normalized = normalize_whitespace(value)
            if normalized:
                out.append(normalized)
    button = _button_value(el)
    if button:
        out.append(button)
    if el.text:
        text = normalize_whitespace(el.text)
        if text:
            out.append(text)
    for child in el:
        _walk_extract(child, attrs, out)
        if child.tail:
            tail = normalize_whitespace(child.tail)
            if tail:
                out.append(tail)


def apply_block_translations(
    html: str, translations: dict[str, Optional[str]], attributes: Optional[Sequence[str]] = None
) -> str:
    """Return ``html`` with translated text/attributes substituted from ``translations``."""
    if not html:
        return html
    attrs = tuple(attributes) if attributes is not None else DEFAULT_TRANSLATABLE_ATTRIBUTES
    root = _parse_fragment(html)
    _walk_apply(root, translations, attrs)
    return _inner_html(root)


def _walk_apply(el: _Element, translations: dict[str, Optional[str]], attrs: Sequence[str]) -> None:
    if not isinstance(el.tag, str) or _skip(el):
        return
    for attr in attrs:
        value = el.get(attr)
        if value and translations.get(value):
            el.set(attr, translations[value] or value)
    button_attr = _button_value_raw(el)
    if button_attr is not None and translations.get(button_attr):
        el.set("value", translations[button_attr] or button_attr)
    el.text = _translate_text(el.text, translations)
    for child in el:
        _walk_apply(child, translations, attrs)
        child.tail = _translate_text(child.tail, translations)


def _button_value_raw(el: _Element) -> Optional[str]:
    tag = _tag(el)
    if tag == "button" and el.get("value"):
        return el.get("value")
    if (
        tag == "input"
        and el.get("value")
        and (el.get("type") or "").lower() in ("submit", "button")
    ):
        return el.get("value")
    return None


def _translate_text(text: Optional[str], translations: dict[str, Optional[str]]) -> Optional[str]:
    if not text:
        return text
    normalized = normalize_whitespace(text)
    if not normalized or normalized not in translations:
        return text
    translated = translations[normalized]
    if not translated or translated == normalized:
        return text
    lead = " " if re.match(r"^\s", text) else ""
    trail = " " if re.search(r"\s$", text) else ""
    return f"{lead}{translated}{trail}"


def _inner_html(root: _Element) -> str:
    parts: list[str] = [root.text or ""]
    for child in root:
        parts.append(lxml_html.tostring(child, encoding="unicode"))
    return "".join(parts)


# -- helpers used by full-page translation -----------------------------------


def apply_element(
    el: _Element, translations: dict[str, Optional[str]], attributes: Optional[Sequence[str]] = None
) -> None:
    """Apply a translation map in place to an element and its subtree."""
    attrs = tuple(attributes) if attributes is not None else DEFAULT_TRANSLATABLE_ATTRIBUTES
    _walk_apply(el, translations, attrs)


def inner_html(el: _Element) -> str:
    """Serialize an element's inner HTML."""
    return _inner_html(el)


def text_content(el: _Element) -> str:
    """Normalized text content of an element (all descendant text, whitespace-collapsed)."""
    return normalize_whitespace("".join(str(t) for t in el.itertext()))
