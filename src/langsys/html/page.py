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
from typing import TYPE_CHECKING, Any, Mapping, Optional, Union, cast

from lxml import html as lxml_html
from lxml.cssselect import CSSSelector
from lxml.etree import _Element

from ..locale import normalize_locale
from ..registration import generate_custom_id
from ..translate import block_translated, lookup_block, previous_block
from ..types import UNCATEGORIZED
from .attributes import marker_is_on
from .markers import ValueMarkers, active
from .markup import _TOKEN as MARKUP_TOKEN
from .markup import encode_phrase_host, phrase_values, render_phrase_host
from .parser import (
    BLOCK_HOST_ATTRS,
    PHRASE_HOST_ATTRS,
    PREVIOUS_BLOCK_ATTRS,
    PREVIOUS_CATEGORY_ATTRS,
    RESOLVED_ATTRS,
    apply_block_translations,
    apply_element,
    block_marker_kind,
    inner_html,
    is_excluded,
    is_marked_host,
    is_phrase_host,
    is_phrase_unit,
    normalize_phrase,
    own_tokens,
    stamp_host,
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
#: A unit's category and the previous category it moved from (MOVE-1), as named on the page.
_Cat = tuple[Optional[str], Optional[str]]
_SelMap = dict[_Element, tuple[Optional[str], Optional[str], bool]]


def translate_page(
    client: "LangsysClient",
    html: str,
    default_category: Optional[str] = None,
    selector_categories: Optional[dict[str, SelectorSpec]] = None,
    previous_category: Optional[str] = None,
) -> str:
    if not html:
        return html
    default: _Cat = (default_category, previous_category)
    locale = client._effective_locale(None)
    attrs = client._translatable_attributes
    doc = lxml_html.document_fromstring(html)
    selmap = _build_selector_map(doc, selector_categories or {})

    _process_head(client, doc, locale, default)

    body = doc.find("body")
    root = body if body is not None else doc
    # VAR-3 - every marked value is its placeholder while the page is walked, and back in its
    # marker once it has been rendered.
    markers = ValueMarkers.read(root)
    with markers.rendering():
        _walk(client, root, attrs, locale, default, inherited=None, selmap=selmap)
    markers.finish(root, locale)
    # After the walk: this render's own mark is output, and MARK-3 reads the page it was given.
    _mark_resolved(client, doc, locale)

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


def _process_head(client: "LangsysClient", doc: _Element, locale: str, category: _Cat) -> None:
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
            translated = client.translate(key, category=category[0], locale=locale,
                                          previous_category=category[1])
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
    category: _Cat,
    locale: str,
) -> None:
    """Meta routes register the canonical phrase and leave authored content alone on a miss."""
    key = normalize_phrase(content)
    if not key:
        return
    translated = client.translate(key, category=category[0], locale=locale,
                                  previous_category=category[1])
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
    default_category: _Cat,
    inherited: Optional[_Cat],
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
    return el.tag.lower() in SKIP_ELEMENTS or is_excluded(el)


def _process_unit(
    client: "LangsysClient",
    el: _Element,
    attrs: list[str],
    locale: str,
    item: tuple[str, Optional[str]],
    *,
    declared: bool = False,
) -> None:
    """One unit (TOK-6): a phrase written back into its one text node, or a content block."""
    tokens, text_nodes = unit_tokens(el, attrs)
    if not tokens or not active().registers(el, tokens):
        return  # VAR-3/VAR-7: no text of its own, or a value it cannot name - as it was
    if not declared and is_phrase_unit(tokens, text_nodes):
        category = None if item[0] == UNCATEGORIZED else item[0]
        translated, current, prior = client._translate(tokens[0], category, None, locale, None,
                                                       item[1])
        apply_element(el, {tokens[0]: translated}, attrs)
        _stamp_phrase(el, current, prior)
        return
    _apply_or_queue_block(client, el, attrs, item, tokens, _registered_content(el, attrs))


def _registered_content(el: _Element, attrs: list[str]) -> str:
    """The markup a block registers with. A unit whose own attributes carry tokens registers with
    its tag, so the content re-tokenizes to the block's tokens; the markers are not content."""
    if not own_tokens(el, attrs):
        return inner_html(el)
    shell = copy.deepcopy(el)
    shell.tail = None
    for marker in (*PHRASE_HOST_ATTRS, *BLOCK_HOST_ATTRS, *PREVIOUS_BLOCK_ATTRS,
                   *PREVIOUS_CATEGORY_ATTRS):
        if marker in shell.attrib:
            del shell.attrib[marker]
    return str(lxml_html.tostring(shell, encoding="unicode"))


def _process_host(
    client: "LangsysClient",
    el: _Element,
    attrs: list[str],
    locale: str,
    default_category: _Cat,
    effective: Optional[_Cat],
    selmap: _SelMap,
) -> None:
    """A marked host, on its own terms (MARK-2, MARK-3, MARK-4). Hosts nested inside it go first,
    so what this one renders around them is already theirs."""
    _process_nested_hosts(client, el, attrs, locale, default_category, effective, selmap)
    item = _item_category(effective, default_category)
    item_cat = item[0]
    if is_phrase_host(el):
        # MARK-2 - the host's content is ONE phrase, kept whole: registered on a miss as the one
        # string the host defines, never re-split.
        text, slots = encode_phrase_host(el)
        markers = active()
        bare = MARKUP_TOKEN.sub("", text)
        # TOK-1/VAR-3 - a phrase of only excluded content or markers has no text: nothing.
        if bare.strip() and markers.registers(el, [bare]):
            category = None if item_cat == UNCATEGORIZED else item_cat
            values = phrase_values(markers, el)
            params = markers.params(values, text) if values else None
            translated, current, prior = client._translate(text, category, params, locale, None,
                                                           item[1])
            if translated != text:
                render_phrase_host(el, translated, slots)
            _stamp_phrase(el, current, prior)
        return
    if block_marker_kind(el) == "identity":
        # MARK-3 - a stamped id is this host's custom_id: render the catalog entry under it.
        # Inside a resolved scope a server already rendered the host, so a miss registers
        # nothing; outside one, a miss registers the host's content under the id.
        custom_id = _first(el, BLOCK_HOST_ATTRS)
        fetch = client._catalog.get(client._effective_locale(None))
        # CACHE-2 - a catalog that could not be refetched still renders what it last held.
        block = fetch.catalog.get(item_cat, {})
        entry = block.get(custom_id) if isinstance(block, dict) else None
        render, prior_id = entry, None
        moved = client._move_of(item_cat, item[1])
        if not block_translated(entry):
            # MOVE-5 - a host a server filled from its previous category names that entry's id;
            # an id the app fixed is the same id under the previous category (MOVE-2).
            prior_id = _first(el, PREVIOUS_BLOCK_ATTRS) or (custom_id if moved is not None else "")
            holder, earlier = _held_under(fetch.catalog, prior_id, moved)
            if block_translated(earlier):
                render, moved = earlier, holder
            else:
                prior_id = ""
        if isinstance(render, dict):
            apply_element(el, render, attrs)
        if not isinstance(entry, dict) and fetch.ok and not _in_resolved_scope(el):
            tokens, _ = unit_tokens(el, attrs)
            if tokens and active().registers(el, tokens):
                client._queue_content_block(
                    _registered_content(el, attrs), item_cat, custom_id, tokens,
                    previous_category=moved,
                )
        _stamp_previous_block(el, prior_id or None, block_translated(entry))
        return
    _process_unit(client, el, attrs, locale, item, declared=True)


def _first(el: _Element, names: tuple[str, ...]) -> str:
    """The first of a marker's spellings the element carries (MARK-2), trimmed; '' for none."""
    return next((v for v in (el.get(a) for a in names) if v is not None), "").strip()


def _held_under(
    catalog: Mapping[str, Any], block_id: str, preferred: Optional[str]
) -> tuple[Optional[str], Any]:
    """The category holding `block_id`, as a raw category (`''` for none), and its entry: the
    preferred category first, then whichever holds it, since a block id names one category."""
    if not block_id:
        return None, None
    names = [preferred or UNCATEGORIZED] if preferred is not None else []
    names += [name for name in catalog if name not in names]
    for name in names:
        bucket = catalog.get(name)
        entry = bucket.get(block_id) if isinstance(bucket, dict) else None
        if isinstance(entry, dict):
            return ("" if name == UNCATEGORIZED else name), entry
    return None, None


def _stamp_phrase(el: _Element, current: bool, prior: Optional[str]) -> None:
    """MOVE-5 - a phrase filled from its previous category names it; one filled from its current
    category carries no previous stamp; one left as source keeps what the author wrote."""
    if prior is not None:
        for marker in PREVIOUS_CATEGORY_ATTRS:
            if marker in el.attrib:
                del el.attrib[marker]
        el.set("data-ls-previous-category", prior)
    elif current:
        for marker in PREVIOUS_CATEGORY_ATTRS:
            if marker in el.attrib:
                del el.attrib[marker]


def _stamp_previous_block(el: _Element, prior_id: Optional[str], current: bool) -> None:
    """MOVE-5 - a block filled from its previous category carries that category's id beside its
    own; one filled from its current category carries none."""
    if prior_id is not None or current:
        for marker in PREVIOUS_BLOCK_ATTRS:
            if marker in el.attrib:
                del el.attrib[marker]
    if prior_id is not None:
        el.set("data-ls-previous-contentblock", prior_id)


def _in_resolved_scope(el: _Element) -> bool:
    """GATE-10's reading: the nearest element, the host included, that carries a resolved marker
    in either spelling decides, and `false`/`0` there opts back out."""
    node: Optional[_Element] = el
    while node is not None:
        for marker in RESOLVED_ATTRS:
            value = node.get(marker)
            if value is not None:
                return marker_is_on(value)
        node = node.getparent()
    return False


def _process_nested_hosts(
    client: "LangsysClient",
    el: _Element,
    attrs: list[str],
    locale: str,
    default_category: _Cat,
    inherited: Optional[_Cat],
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
    item: tuple[str, Optional[str]],
    phrases: list[str],
    inner: str,
) -> None:
    item_cat = item[0]
    # CID-2 — the sentinel is a cache-lookup namespace and never a hash input, so the
    # id is built from the raw category while the catalog is still keyed by the token.
    # MOVE-2 - from the current category alone; a previous category is never identity.
    raw_category = None if item_cat == UNCATEGORIZED else item_cat
    moved = client._move_of(raw_category, item[1])
    custom_id = generate_custom_id(raw_category, phrases)
    locale = client._effective_locale(None)
    held, preloaded = client._preloaded_entry(locale, item_cat, custom_id)
    if held and isinstance(preloaded, dict):
        render, prior_id = preloaded, None
        if moved is not None and not block_translated(preloaded):
            prior_key = generate_custom_id(moved, phrases)
            found, earlier = client._preloaded_entry(locale, moved or UNCATEGORIZED, prior_key)
            if found and block_translated(earlier):
                render, prior_id = earlier, prior_key
        apply_element(el, render, attrs)
        _stamp(el, custom_id, prior_id, block_translated(preloaded))
        return
    fetch = client._catalog.get(locale)
    block = lookup_block(fetch.catalog.get(item_cat), raw_category, custom_id, phrases)
    # MOVE-4 - every read under the current category first, then the previous category's.
    found_block, prior = previous_block(fetch.catalog, block, moved, phrases)
    if isinstance(found_block, dict):
        apply_element(el, found_block, attrs)
    if not isinstance(block, dict) and fetch.ok:
        # WIRE-4 — never queue off a catalog we could not read.
        client._queue_content_block(inner, item_cat, custom_id, phrases, previous_category=moved)
    # MARK-1 — stamp whichever way it went. The id is what the block IS, not what the
    # catalog held, and an unstamped miss is the case most needing inspection.
    _stamp(el, custom_id, prior, block_translated(block))


def _stamp(el: _Element, custom_id: str, prior_id: Optional[str], current: bool) -> None:
    for marker in BLOCK_HOST_ATTRS:
        if marker in el.attrib:
            del el.attrib[marker]
    el.set("data-ls-contentblock", custom_id)
    _stamp_previous_block(el, prior_id, current)


# -- category resolution ------------------------------------------------------


def _item_category(effective: Optional[_Cat], default: _Cat) -> tuple[str, Optional[str]]:
    """A unit's category (the sentinel for none) and its previous category. An element naming
    only a previous category moves whichever category applies to it."""
    if effective is not None and effective[0] is not None:
        return effective[0], effective[1]
    previous = effective[1] if effective is not None else default[1]
    category = default[0] if default[0] is not None else UNCATEGORIZED
    return category, previous


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


def _effective_category(
    el: _Element, inherited: Optional[_Cat], selmap: _SelMap
) -> Optional[_Cat]:
    """MOVE-4 - an element naming its own category takes its own previous category, or none; one
    naming no category takes its block's, the previous category with it. Its own
    `data-ls-previous-category` names the move of whichever category applies."""
    match = selmap.get(el)
    attr = _marked_attr(el, "category")
    found: Optional[_Cat]
    if match is not None and match[2]:  # selector override
        found = (match[0], match[1])
    elif attr:
        found = (attr, None)
    elif inherited is not None:
        found = inherited
    elif match is not None:  # selector, non-override
        found = (match[0], match[1])
    else:
        found = None
    previous = _marked_attr(el, "previous-category")
    if previous is not None:
        found = (found[0] if found is not None else None, previous)
    return found


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
        previous: Optional[str] = None
        if isinstance(spec, str):
            category: Optional[str] = spec
            override = False
        else:
            category = spec.get("category")  # type: ignore[assignment]
            moved = spec.get("previous_category", spec.get("previousCategory"))
            previous = moved if isinstance(moved, str) else None
            override = bool(
                spec.get("overrideParentElementCategory", spec.get("override", False))
            )
        try:
            matched = cast("list[_Element]", CSSSelector(selector)(doc))
        except Exception:
            continue
        for element in matched:
            result[element] = (category, previous, override)
    return result


# -- explicit block calls -------------------------------------------------------


def translate_fragment(
    client: "LangsysClient", html: str, category: Optional[str],
    previous_category: Optional[str] = None,
) -> str:
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
    markers = ValueMarkers.read(root)  # VAR-3
    with markers.rendering():
        return _translate_fragment_unit(client, html, root, markers, attrs, locale,
                                        (category, previous_category))


def _translate_fragment_unit(
    client: "LangsysClient",
    html: str,
    root: _Element,
    markers: ValueMarkers,
    attrs: list[str],
    locale: str,
    named: _Cat,
) -> str:
    category, previous = named
    given: Optional[_Cat] = named if category is not None or previous is not None else None
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
        _process_host(client, elements[0], attrs, locale, (None, None), given, {})
        markers.finish(root, locale)
        return inner_html(root)
    if has_hosts:
        _process_nested_hosts(client, root, attrs, locale, (None, None), given, {})

    tokens, text_nodes = unit_tokens(root, attrs)
    if not tokens or not markers.registers(root, tokens):
        markers.finish(root, locale)
        return inner_html(root) if has_hosts else html
    if is_phrase_unit(tokens, text_nodes):
        translated, _, prior = client._translate(tokens[0], category, None, locale, None, previous)
        if not has_hosts:
            rendered = apply_block_translations(html, {tokens[0]: translated}, attrs,
                                                locale=locale)
            # MOVE-5 - a one-phrase fragment filled from its previous category names it.
            return rendered if prior is None else stamp_host(
                rendered, [("data-ls-previous-category", prior)])
        apply_element(root, {tokens[0]: translated}, attrs)
        markers.finish(root, locale)
        return inner_html(root)
    markers.finish(root, locale)  # the hosts' renders kept, everything else as it was
    return client._render_block(inner_html(root) if has_hosts else html, category, tokens,
                                previous)
