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
    "SKIP_TAGS",
    "extract_phrases",
    "apply_block_translations",
    "stamp_content_block",
    "apply_element",
    "inner_html",
    "text_content",
    "normalize_whitespace",
]

_WS = re.compile(r"\s+")

#: TOK-1 — elements whose content is never tokenized. They hold code, inert content, or
#: content no implementation can agree on.
#:
#: `noscript` is here on a REVERSAL. Its text does render to a visitor with scripting
#: off, but with scripting ON — the HTML spec's default — a parser treats the body as
#: raw text, so the token is a markup string, and sending markup to machine translation
#: is the failure this family exists to prevent. libxml2 (what lxml uses) has no
#: scripting flag and parses the children as elements, so excluding it is also what
#: makes every parser agree by construction.
#:
#: `template` is a no-op for walkers that never reach fragment content — browsers hang
#: it off `HTMLTemplateElement.content`. lxml is NOT one of those: it parses template
#: children into the ordinary tree, so here it is a live vector rather than a free pass.
#:
#: Deliberately NOT `svg` or `math`: the revision in force names neither, and the
#: announced 8.0.1 makes SVG text explicitly translatable. The page walker still skips
#: both; that split is measured and reported rather than silently reconciled.
SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})


def normalize_whitespace(text: Optional[str]) -> str:
    return _WS.sub(" ", text).strip() if text else ""


def _parse_fragment(html: str) -> _Element:
    return cast("_Element", lxml_html.fragment_fromstring(html, create_parent="div"))


def _skip(el: _Element) -> bool:
    if _tag(el) in SKIP_TAGS:
        return True
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


def stamp_content_block(html: str, custom_id: str) -> str:
    """MARK-1 — put the resolved ``custom_id`` on the rendered host.

    An identity you cannot observe from the DOM is one nobody can debug: whether this
    block resolved to the id you think, whether two SDKs derived the same one, whether
    it is the block the Translation Manager is showing you — all answerable in a
    devtools inspector if the id is on the host, and only by reasoning about source if
    it is not. The audit and parity probes read it directly.

    Writers emit the ``data-ls-*`` spelling; only readers accept both (MARK-2).

    Returns ``html`` unchanged when the fragment has no single host element to stamp —
    a bare text run, or several siblings with no common parent inside this call. There
    is nothing to carry the attribute there, and inventing a wrapper would change the
    markup the caller handed us.
    """
    if not html or not custom_id:
        return html
    root = _parse_fragment(html)
    children = [c for c in root if isinstance(c.tag, str)]
    if len(children) != 1 or (root.text or "").strip():
        return html
    children[0].set("data-ls-contentblock", custom_id)
    return _inner_html(root)


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
