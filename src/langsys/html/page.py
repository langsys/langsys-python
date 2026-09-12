"""Full-page HTML translation — a faithful port of the PHP SDK's ``PageTranslator``.

Walks a document's ``<head>`` (title, description/keywords/author metas, OpenGraph and
Twitter cards, ``<html lang>``, ``og:locale``) and ``<body>``, classifying each leaf
block element as either a **simple phrase** (its whole text is one phrase) or a
**content block** (markup-bearing / multi-phrase). Honors ``data-langsys-category``,
``data-ls-contentblock`` / ``data-langsys-contentblock`` (both spellings, MARK-2),
``translate="no"``/``data-notrans``, and an optional
``selector_categories`` map. Missing items are queued for registration.

Requires lxml + cssselect (``pip install langsys[html]``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union, cast

from lxml import html as lxml_html
from lxml.cssselect import CSSSelector
from lxml.etree import _Element

from ..registration import generate_custom_id
from ..translate import lookup_block
from ..types import UNCATEGORIZED
from .parser import apply_element, extract_phrases, inner_html, text_content

if TYPE_CHECKING:
    from ..client import LangsysClient

BLOCK_ELEMENTS = frozenset(
    {
        "div", "section", "article", "header", "footer", "nav", "aside", "main",
        "p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "address",
        "ul", "ol", "li", "dl", "dt", "dd",
        "table", "tr", "th", "td", "thead", "tbody", "tfoot", "caption",
        "form", "fieldset", "legend", "figure", "figcaption",
        "details", "summary", "dialog",
    }
)
SKIP_ELEMENTS = frozenset({"script", "style", "noscript", "template", "svg", "math"})
META_NAMES = ("description", "keywords", "author")
OG_PROPERTIES = ("og:title", "og:description", "og:site_name")
TWITTER_PROPERTIES = ("twitter:title", "twitter:description")

SelectorSpec = Union[str, dict[str, object]]
_SelMap = dict[_Element, tuple[Optional[str], bool]]


def translate_page(
    client: "LangsysClient",
    html: str,
    default_category: Optional[str] = None,
    selector_categories: Optional[dict[str, SelectorSpec]] = None,
) -> str:
    if not html:
        return html
    locale = client._effective_locale(None)
    attrs = client._translatable_attributes
    doc = lxml_html.document_fromstring(html)
    selmap = _build_selector_map(doc, selector_categories or {})

    _process_head(client, doc, locale, default_category)

    body = doc.find("body")
    root = body if body is not None else doc
    _walk(client, root, attrs, locale, default_category, inherited=None, selmap=selmap)

    return str(lxml_html.tostring(doc, encoding="unicode"))


# -- head ---------------------------------------------------------------------


def _process_head(
    client: "LangsysClient", doc: _Element, locale: str, category: Optional[str]
) -> None:
    doc.set("lang", locale)
    head = doc.find("head")
    if head is None:
        return

    title = head.find("title")
    if title is not None and title.text and title.text.strip():
        title.text = client.translate(title.text.strip(), category=category, locale=locale)

    for meta in head.findall("meta"):
        content = meta.get("content")
        if not content:
            continue
        name = meta.get("name") or ""
        prop = meta.get("property") or ""
        if name in META_NAMES or name in TWITTER_PROPERTIES:
            meta.set("content", client.translate(content, category=category, locale=locale))
        elif prop:
            if prop == "og:locale":
                meta.set("content", _og_locale(locale))
            elif prop in OG_PROPERTIES or prop in TWITTER_PROPERTIES:
                meta.set("content", client.translate(content, category=category, locale=locale))


def _og_locale(locale: str) -> str:
    parts = locale.replace("-", "_").split("_")
    if len(parts) >= 2:
        return f"{parts[0].lower()}_{parts[1].upper()}"
    return f"{parts[0].lower()}_{parts[0].upper()}"


# -- body ---------------------------------------------------------------------


def _walk(
    client: "LangsysClient",
    node: _Element,
    attrs: list[str],
    locale: str,
    default_category: Optional[str],
    inherited: Optional[str],
    selmap: _SelMap,
) -> None:
    for child in node:
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()
        if tag in SKIP_ELEMENTS:
            continue
        if child.get("translate") == "no" or child.get("data-notrans"):
            continue

        effective = _effective_category(child, inherited, selmap)

        if _has_content_block_attr(child):
            _handle_block(client, child, attrs, locale, _item_category(effective, default_category))
            continue

        if tag in BLOCK_ELEMENTS:
            if _contains_nested_blocks(child):
                _walk(client, child, attrs, locale, default_category, effective, selmap)
                continue
            inner = inner_html(child)
            phrases = extract_phrases(inner, attrs)
            if not phrases:
                continue
            item_cat = _item_category(effective, default_category)
            text = text_content(child)
            if len(phrases) == 1 and phrases[0] == text:
                category = None if item_cat == UNCATEGORIZED else item_cat
                translated = client.translate(text, category=category, locale=locale)
                apply_element(child, {text: translated}, attrs)
            else:
                _apply_or_queue_block(client, child, attrs, item_cat, phrases, inner)
        else:
            _walk(client, child, attrs, locale, default_category, effective, selmap)


def _handle_block(
    client: "LangsysClient", el: _Element, attrs: list[str], locale: str, item_cat: str
) -> None:
    inner = inner_html(el)
    phrases = extract_phrases(inner, attrs)
    if not phrases:
        return
    _apply_or_queue_block(client, el, attrs, item_cat, phrases, inner)


def _apply_or_queue_block(
    client: "LangsysClient",
    el: _Element,
    attrs: list[str],
    item_cat: str,
    phrases: list[str],
    inner: str,
) -> None:
    # CID-2 — the sentinel is a cache-lookup namespace and never a hash input, so the
    # id is built from the raw category while the catalog is still keyed by the token.
    raw_category = None if item_cat == UNCATEGORIZED else item_cat
    custom_id = generate_custom_id(raw_category, phrases)
    fetch = client._catalog.get(client._effective_locale(None))
    block = lookup_block(fetch.catalog.get(item_cat), raw_category, custom_id, phrases)
    if isinstance(block, dict):
        apply_element(el, block, attrs)
    elif fetch.ok:
        # WIRE-4 — never queue off a catalog we could not read.
        client._queue_content_block(inner, item_cat, custom_id, phrases)


# -- category resolution ------------------------------------------------------


def _item_category(effective: Optional[str], default_category: Optional[str]) -> str:
    if effective is not None:
        return effective
    if default_category is not None:
        return default_category
    return UNCATEGORIZED


def _marked_attr(el: _Element, suffix: str) -> Optional[str]:
    """MARK-2 — read both ``data-ls-*`` and ``data-langsys-*``.

    The spellings already coexist in shipped code, and a page that mixes them is the
    ordinary case rather than an edge: a PHP-rendered page hosting a JS-rendered
    component is what a customer's site looks like. A reader that knows only one
    spelling does not see the other's host as marked, so it walks straight into it and
    splits a block that already had an id, producing a second registration for content
    that is already identified.
    """
    for prefix in ("data-ls-", "data-langsys-"):
        value = el.get(f"{prefix}{suffix}")
        if value is not None:
            return value
    return None


def _effective_category(el: _Element, inherited: Optional[str], selmap: _SelMap) -> Optional[str]:
    match = selmap.get(el)
    if match is not None and match[1]:  # selector override
        return match[0]
    attr = _marked_attr(el, "category")
    if attr:
        return attr
    if inherited is not None:
        return inherited
    if match is not None and not match[1]:  # selector, non-override
        return match[0]
    return None


def _has_content_block_attr(el: _Element) -> bool:
    value = _marked_attr(el, "contentblock")
    if value is None:
        return False
    return value != "" and value != "0" and value.lower() != "false"


def _contains_nested_blocks(el: _Element) -> bool:
    for child in el.iter():
        if child is el or not isinstance(child.tag, str):
            continue
        if child.tag.lower() in BLOCK_ELEMENTS:
            return True
    return False


def _build_selector_map(doc: _Element, selector_categories: dict[str, SelectorSpec]) -> _SelMap:
    result: _SelMap = {}
    for selector, spec in selector_categories.items():
        if isinstance(spec, str):
            category: Optional[str] = spec
            override = False
        else:
            category = spec.get("category")  # type: ignore[assignment]
            override = bool(
                spec.get("overrideParentElementCategory", spec.get("override", False))
            )
        try:
            matched = cast("list[_Element]", CSSSelector(selector)(doc))
        except Exception:
            continue
        for element in matched:
            result[element] = (category, override)
    return result
