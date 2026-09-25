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

import copy
from typing import TYPE_CHECKING, Optional, Union, cast

from lxml import html as lxml_html
from lxml.cssselect import CSSSelector
from lxml.etree import _Element

from ..locale import normalize_locale
from ..registration import generate_custom_id
from ..translate import lookup_block
from ..types import UNCATEGORIZED
from .markup import encode_phrase_host, render_phrase_host
from .parser import (
    BLOCK_HOST_ATTRS,
    PHRASE_HOST_ATTRS,
    apply_block_translations,
    apply_element,
    block_marker_kind,
    inner_html,
    is_marked_host,
    is_phrase_host,
    is_phrase_unit,
    normalize_phrase,
    own_tokens,
    unit_tokens,
)
from .parser import (
    _parse_fragment as parse_fragment,
)

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
#: TOK-1 - `svg` is NOT here: its `<text>` is visible copy, translated on every path. An svg is
#: an ordinary unit, or part of one; it changes nothing about a unit's shape (TOK-6).
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
    _mark_resolved(client, doc, locale)

    body = doc.find("body")
    root = body if body is not None else doc
    _walk(client, root, attrs, locale, default_category, inherited=None, selmap=selmap)

    return str(lxml_html.tostring(doc, encoding="unicode"))


def _mark_resolved(client: "LangsysClient", doc: _Element, locale: str) -> None:
    """GATE-10 (producing) - mark the root resolved only when this render is not in the base locale.

    A page served translated is output, not source: a browser SDK on it must not register its
    text as new phrases. A base-locale render IS source and stays discoverable, so it is never
    marked. When the base locale cannot be known the page is left unmarked, because a wrong mark
    hides exactly the text discovery exists to find.
    """
    base = client._project_base_locale()
    rendered = normalize_locale(locale)
    if base and rendered and rendered != normalize_locale(base):
        doc.set("data-ls-resolved", rendered)


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
#
# TOK-6 - the walk registers UNITS. A container of blocks is walked; any other element it
# reaches - a leaf block, or a void or inline element directly under a container, such as an
# `<img alt>` or `<a title>` under `<body>` - is a unit. A unit is a phrase when its one token is
# its one text node, and a content block otherwise. A marked host (MARK-4) is a unit of its own,
# excised from whatever encloses it, and handled here on its own terms.


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
        if not isinstance(child.tag, str) or _excluded(child):
            continue
        effective = _effective_category(child, inherited, selmap)
        if is_marked_host(child):
            _process_host(client, child, attrs, locale, default_category, effective, selmap)
            continue
        if _contains_nested_blocks(child):
            _walk(client, child, attrs, locale, default_category, effective, selmap)
            continue
        _process_nested_hosts(client, child, attrs, locale, default_category, effective, selmap)
        _process_unit(client, child, attrs, locale, _item_category(effective, default_category))


def _excluded(el: _Element) -> bool:
    return (
        el.tag.lower() in SKIP_ELEMENTS
        or el.get("translate") == "no"
        or bool(el.get("data-notrans"))
    )


def _process_unit(
    client: "LangsysClient",
    el: _Element,
    attrs: list[str],
    locale: str,
    item_cat: str,
    *,
    declared: bool = False,
) -> None:
    """One unit (TOK-6): a phrase written back into its one text node, or a content block."""
    tokens, text_nodes = unit_tokens(el, attrs)
    if not tokens:
        return
    if not declared and is_phrase_unit(tokens, text_nodes):
        category = None if item_cat == UNCATEGORIZED else item_cat
        translated = client.translate(tokens[0], category=category, locale=locale)
        apply_element(el, {tokens[0]: translated}, attrs)
        return
    _apply_or_queue_block(client, el, attrs, item_cat, tokens, _registered_content(el, attrs))


def _registered_content(el: _Element, attrs: list[str]) -> str:
    """The markup a block registers with. A unit whose own attributes carry tokens registers with
    its tag, so the content re-tokenizes to the block's tokens; the markers are not content."""
    if not own_tokens(el, attrs):
        return inner_html(el)
    shell = copy.deepcopy(el)
    shell.tail = None
    for marker in (*PHRASE_HOST_ATTRS, *BLOCK_HOST_ATTRS):
        if marker in shell.attrib:
            del shell.attrib[marker]
    return str(lxml_html.tostring(shell, encoding="unicode"))


def _process_host(
    client: "LangsysClient",
    el: _Element,
    attrs: list[str],
    locale: str,
    default_category: Optional[str],
    effective: Optional[str],
    selmap: _SelMap,
) -> None:
    """A marked host, on its own terms (MARK-2, MARK-3, MARK-4). Hosts nested inside it go first,
    so what this one renders around them is already theirs."""
    _process_nested_hosts(client, el, attrs, locale, default_category, effective, selmap)
    item_cat = _item_category(effective, default_category)
    if is_phrase_host(el):
        # MARK-2 - the host's content is ONE phrase, kept whole: registered on a miss as the one
        # string the host defines, never re-split.
        text, slots = encode_phrase_host(el)
        if text:
            category = None if item_cat == UNCATEGORIZED else item_cat
            translated = client.translate(text, category=category, locale=locale)
            if translated != text:
                render_phrase_host(el, translated, slots)
        return
    if block_marker_kind(el) == "identity":
        # MARK-3 - a stamped id is this host's custom_id. Render the catalog entry under it, or
        # leave the source; register nothing.
        custom_id = next((v for v in (el.get(a) for a in BLOCK_HOST_ATTRS) if v is not None), "")
        fetch = client._catalog.get(client._effective_locale(None))
        block = fetch.catalog.get(item_cat, {}) if fetch.ok else {}
        entry = block.get(custom_id.strip()) if isinstance(block, dict) else None
        if isinstance(entry, dict):
            apply_element(el, entry, attrs)
        return
    _process_unit(client, el, attrs, locale, item_cat, declared=True)


def _process_nested_hosts(
    client: "LangsysClient",
    el: _Element,
    attrs: list[str],
    locale: str,
    default_category: Optional[str],
    inherited: Optional[str],
    selmap: _SelMap,
) -> None:
    """Every outermost marked host below `el`, each as a unit of its own (MARK-4)."""
    for child in el:
        if not isinstance(child.tag, str) or _excluded(child):
            continue
        effective = _effective_category(child, inherited, selmap)
        if is_marked_host(child):
            _process_host(client, child, attrs, locale, default_category, effective, selmap)
        else:
            _process_nested_hosts(client, child, attrs, locale, default_category, effective, selmap)


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
    # catalog held, and an unstamped miss is the case most needing inspection.
    for marker in BLOCK_HOST_ATTRS:
        if marker in el.attrib:
            del el.attrib[marker]
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


# -- explicit block calls -------------------------------------------------------


def translate_fragment(client: "LangsysClient", html: str, category: Optional[str]) -> str:
    """`translate_content_block` - one fragment as one unit (TOK-6), marked hosts on their own.

    A fragment whose one element is a marked host is that host (MARK-3). Otherwise the fragment's
    content is the unit: a phrase when its one token is its one text node, a content block
    otherwise, and any marked host inside it is excised and handled as a unit of its own (MARK-4).
    A fragment with no marked host is translated on the caller's own string, so it comes back
    byte for byte apart from the translation and the stamp.
    """
    attrs = client._translatable_attributes
    locale = client._effective_locale(None)
    root = parse_fragment(html)
    elements = [c for c in root if isinstance(c.tag, str)]
    has_hosts = any(
        is_marked_host(e) for top in elements for e in top.iter() if isinstance(e.tag, str)
    )
    lone_host = (
        len(elements) == 1
        and is_marked_host(elements[0])
        and not (root.text or "").strip()
        and not (elements[0].tail or "").strip()
    )
    if lone_host:
        _process_host(client, elements[0], attrs, locale, None, category, {})
        return inner_html(root)
    if has_hosts:
        _process_nested_hosts(client, root, attrs, locale, None, category, {})

    tokens, text_nodes = unit_tokens(root, attrs)
    if not tokens:
        return inner_html(root) if has_hosts else html
    if is_phrase_unit(tokens, text_nodes):
        translated = client.translate(tokens[0], category=category, locale=locale)
        if not has_hosts:
            return apply_block_translations(html, {tokens[0]: translated}, attrs)
        apply_element(root, {tokens[0]: translated}, attrs)
        return inner_html(root)
    return client._render_block(inner_html(root) if has_hosts else html, category, tokens)
