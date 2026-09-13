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
from .attributes import classify_block_attribute
from .parser import (
    apply_element,
    extract_phrases,
    inner_html,
    normalize_phrase,
    text_content,
)

#: MARK-2 — a host carrying one of these is already identified. Both spellings.
PHRASE_HOST_ATTRS = ("data-ls-phrase", "data-langsys-phrase")
CONTENT_BLOCK_ATTRS = ("data-ls-contentblock", "data-langsys-contentblock")



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
#: TOK-1 (8.0.1) - `svg` is NOT here: its text is translated on every path, and the walker
#: gives it its own handling below. Removing it from this set alone would make the walker
#: recurse into it and never tokenize its `<text>`, which is the drop PHP hit.
SKIP_ELEMENTS = frozenset({"script", "style", "noscript", "template", "math"})
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
    if title is not None and title.text:
        # CONF-1 / TOK-2 - the head route registers the canonical phrase like every other
        # path. It used to pass strip()ed raw text straight through, so a title registered
        # `Buy   now` where the body registered `Buy now`: the "path that did nothing to the
        # text" the spec names. The authored text is left alone on a miss, because the rule
        # governs identity, not output.
        key = normalize_phrase(title.text)
        if key:
            translated = client.translate(key, category=category, locale=locale)
            if translated != key:
                title.text = translated

    for meta in head.findall("meta"):
        content = meta.get("content")
        if not content:
            continue
        name = meta.get("name") or ""
        prop = meta.get("property") or ""
        if name in META_NAMES or name in TWITTER_PROPERTIES:
            _translate_meta(client, meta, content, category, locale)
        elif prop:
            if prop == "og:locale":
                meta.set("content", _og_locale(locale))
            elif prop in OG_PROPERTIES or prop in TWITTER_PROPERTIES:
                _translate_meta(client, meta, content, category, locale)


def _translate_meta(
    client: "LangsysClient",
    meta: _Element,
    content: str,
    category: Optional[str],
    locale: str,
) -> None:
    """Meta routes register the canonical phrase and leave authored content alone on a miss."""
    key = normalize_phrase(content)
    if not key:
        return
    translated = client.translate(key, category=category, locale=locale)
    if translated != key:
        meta.set("content", translated)


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
        # MARK-2 — EXCISION. A `<Phrase>` host rendered by another SDK already has an
        # id; descending into it registers its text a second time under a new one, and
        # on a leaf block it would also shift the parent block's id. Skipping the
        # subtree leaves both alone.
        if any(child.get(attr) is not None for attr in PHRASE_HOST_ATTRS):
            continue
        # MARK-2 — the same for a block host carrying a foreign identity. Walking into
        # it re-tokenizes content that already has an id, files it under *this* page's
        # category, queues it as a new block, and overwrites the other SDK's stamp with
        # ours — one block, two ids, and the Translation Manager showing it twice.
        if _is_identified_block_host(child):
            continue

        effective = _effective_category(child, inherited, selmap)

        if _has_content_block_attr(child):
            _handle_block(client, child, attrs, locale, _item_category(effective, default_category))
            continue

        # TOK-1 (8.0.1) - svg text is translated, and svg is handled as its own unit rather
        # than as a block element. Adding svg to BLOCK_ELEMENTS is the retracted mechanism: a
        # parent `<p>` holding an inline icon would then "contain a nested block", the walker
        # would recurse into it, and the paragraph's own words would be dropped. Inline svg
        # therefore stays inside its leaf block and is tokenized there; only an svg the walker
        # reaches directly, with no leaf around it, arrives here.
        if tag == "svg":
            _translate_leaf(client, child, attrs, locale, default_category, effective)
            continue

        if tag in BLOCK_ELEMENTS:
            if _contains_nested_blocks(child):
                _walk(client, child, attrs, locale, default_category, effective, selmap)
                continue
            _translate_leaf(client, child, attrs, locale, default_category, effective)
        else:
            _walk(client, child, attrs, locale, default_category, effective, selmap)


def _translate_leaf(
    client: "LangsysClient",
    el: _Element,
    attrs: list[str],
    locale: str,
    default_category: Optional[str],
    effective: Optional[str],
) -> None:
    """One leaf: a single phrase when its whole text is one token, otherwise a content block."""
    inner = inner_html(el)
    phrases = extract_phrases(inner, attrs)
    if not phrases:
        return
    item_cat = _item_category(effective, default_category)
    text = text_content(el)
    if len(phrases) == 1 and phrases[0] == text:
        category = None if item_cat == UNCATEGORIZED else item_cat
        translated = client.translate(text, category=category, locale=locale)
        apply_element(el, {text: translated}, attrs)
    else:
        _apply_or_queue_block(client, el, attrs, item_cat, phrases, inner)


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
    # MARK-1 — stamp whichever way it went. The id is what the block IS, not what the
    # catalog held, and an unstamped miss is the case most needing inspection. Set on
    # the element in place: this path is already re-serialising the whole document, so
    # there is no original string to preserve as there is on the block path.
    el.set("data-ls-contentblock", custom_id)


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


def _block_attribute_kind(el: _Element) -> str:
    """How this element's block attribute reads. One classifier, one answer."""
    for attr in CONTENT_BLOCK_ATTRS:
        kind = classify_block_attribute(el.get(attr))
        if kind != "absent":
            return kind
    return "absent"


def _is_identified_block_host(el: _Element) -> bool:
    """True when a block attribute carries another SDK's id rather than a declaration."""
    return _block_attribute_kind(el) == "identity"


def _has_content_block_attr(el: _Element) -> bool:
    """True only for an authoring *declaration*. An opt-out, a bare attribute, or
    another SDK's identity are all handled elsewhere — see `classify_block_attribute`."""
    return _block_attribute_kind(el) == "declaration"


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
