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

from ..interpolate import percent_placeholders_to_braces
from .attributes import DEFAULT_TRANSLATABLE_ATTRIBUTES, classify_block_attribute

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
    "is_marked_host",
    "extract_phrases",
    "apply_block_translations",
    "stamp_content_block",
    "apply_element",
    "inner_html",
    "text_content",
    "normalize_whitespace",
    "normalize_phrase",
]

#: TOK-2 (8.0.1) - the collapse set is JavaScript's whitespace class, ENUMERATED by
#: codepoint. "Whitespace" is not a portable word: CPython's own class collapses U+0085 and
#: misses U+FEFF, which is two of the divergences this list corrects.
#:
#: Written as integers deliberately. Escapes for these characters have been decoded into
#: invisible literals in transit more than once in this repository's history, and a set
#: nobody can read is a set nobody can review.
_JS_WHITESPACE = (
    0x0009, 0x000A, 0x000B, 0x000C, 0x000D, 0x0020, 0x00A0, 0x1680,
    *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF,
)

#: HELD (strip ruling) - NOT a statement of the rule. CPython's class also collapses the C0
#: separators U+001C-U+001F and JavaScript's does not, but a ruling on stripping C0 controls
#: is pending, so today's behaviour is kept exactly. Adopting JavaScript's set wholesale
#: would silently decide that ruling. Remove this tuple only when the ruling lands.
_HELD_C0_SEPARATORS = (0x001C, 0x001D, 0x001E, 0x001F)

_COLLAPSE = frozenset(_JS_WHITESPACE + _HELD_C0_SEPARATORS)
_COLLAPSE_CHARS = "".join(chr(codepoint) for codepoint in sorted(_COLLAPSE))
_WS = re.compile("[" + re.escape(_COLLAPSE_CHARS) + "]+")

#: The first start tag in a fragment, with its self-closing slash captured separately so
#: an attribute can be inserted before it without disturbing the rest of the string.
#:
#: Quote-aware on purpose. A `>` inside an attribute value is legal and ordinary —
#: `title="a>b"`, a `data-*` attribute holding JSON — and a pattern that stops at the
#: first `>` inserts the stamp into the middle of that value, producing markup that is
#: not merely different but broken.
_OPEN_TAG = re.compile(
    r"""<[A-Za-z][^\s/>]*(?:"[^"]*"|'[^']*'|[^>"'])*?(?P<selfclose>/?)>""",
    re.VERBOSE | re.DOTALL,
)

#: An HTML comment may precede the host element and may itself contain markup, so the
#: first `<tag` in the string is not necessarily the host's.
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

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
#: `math` is excluded as of 8.0.1: MathML is notation, and translating a variable name or an
#: operator corrupts it rather than localising it.
#:
#: `svg` is deliberately NOT excluded. Its `<text>` renders words a reader sees, so 8.0.1
#: makes its text translatable on every path. The rule is behavioural, not structural - see
#: `page.py`, where svg gets its own handling instead of being treated as a block element,
#: which is the retracted mechanism that dropped a parent block's own text.
SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "math"})

#: MARK-2 — a host already carrying one of these has an identity, and walking into it
#: registers its text a second time under a new id. Both spellings, because a page
#: mixing them is the ordinary case: a PHP-rendered page hosting a JS-rendered
#: component is what a customer's site looks like.
#: A phrase host is value-blind: this SDK has no "declare a phrase" affordance, so the
#: attribute's presence is always an identity. A BLOCK host is not — see
#: `classify_block_attribute`, which both walkers share so the two cannot drift.
PHRASE_HOST_ATTRS = ("data-ls-phrase", "data-langsys-phrase")
BLOCK_HOST_ATTRS = ("data-ls-contentblock", "data-langsys-contentblock")


def normalize_whitespace(text: Optional[str]) -> str:
    """TOK-2 - collapse runs of the enumerated set to one space, and trim the SAME set.

    Trimming is a second site, not a detail of the first. `str.strip()` uses Python's own idea
    of whitespace, which removes U+0085 and keeps U+FEFF, so a fix to the collapse alone passes
    the internal case and still disagrees at the edges.
    """
    return _WS.sub(" ", text).strip(_COLLAPSE_CHARS) if text else ""


def normalize_phrase(text: Optional[str]) -> str:
    """The canonical phrase key: whitespace per TOK-2, then `%name%` to `{name}` per TOK-5.

    Used on BOTH sides of every register/lookup pair (CONF-1). A normalisation applied when a
    phrase is captured and not when it is looked up registers one string and looks up another,
    which misses forever and re-registers every render.
    """
    return percent_placeholders_to_braces(normalize_whitespace(text))


def _parse_fragment(html: str) -> _Element:
    return cast("_Element", lxml_html.fragment_fromstring(html, create_parent="div"))


def is_marked_host(el: _Element) -> bool:
    """True when this element already carries a Langsys identity (MARK-2).

    A declaration flag or an opt-out is not an identity, so a subtree carrying one is
    walked as ordinary content rather than excised.
    """
    if any(el.get(attr) is not None for attr in PHRASE_HOST_ATTRS):
        return True
    return any(
        classify_block_attribute(el.get(attr)) == "identity" for attr in BLOCK_HOST_ATTRS
    )


def _skip(el: _Element) -> bool:
    if _tag(el) in SKIP_TAGS:
        return True
    return el.get("translate") == "no" or bool(el.get("data-notrans"))


def _tag(el: _Element) -> str:
    return el.tag.lower() if isinstance(el.tag, str) else ""


def _button_value(el: _Element) -> Optional[str]:
    tag = _tag(el)
    if tag == "button" and el.get("value"):
        return normalize_phrase(el.get("value"))
    if (
        tag == "input"
        and el.get("value")
        and (el.get("type") or "").lower() in ("submit", "button")
    ):
        return normalize_phrase(el.get("value"))
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
            normalized = normalize_phrase(value)
            if normalized:
                out.append(normalized)
    button = _button_value(el)
    if button:
        out.append(button)
    if el.text:
        text = normalize_phrase(el.text)
        if text:
            out.append(text)
    for child in el:
        # MARK-2 — EXCISION, not merely "do not re-register". The host's text must not
        # reach the parent's phrase list at all: a content block's id derives from its
        # phrases in order, so harvesting an already-identified host's text would also
        # shift the id of the block containing it.
        if isinstance(child.tag, str) and is_marked_host(child):
            if child.tail:
                tail = normalize_phrase(child.tail)
                if tail:
                    out.append(tail)
            continue
        _walk_extract(child, attrs, out)
        if child.tail:
            tail = normalize_phrase(child.tail)
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

    # Injected into the ORIGINAL string rather than re-serialised from the parse tree.
    # Round-tripping through lxml is not lossless for markup we were only asked to
    # stamp: `&nbsp;` comes back as a raw U+00A0, `<br/>` as `<br>`, `&eacute;` as `é`,
    # and unquoted or single-quoted attribute values get rewritten. On the miss path
    # the caller is handed back its own markup, so those changes would be ours to
    # explain and none of them were asked for.
    match = _search_open_tag(html)
    if match is None:  # pragma: no cover - a single element always has an open tag
        return html
    attribute = f' data-ls-contentblock="{_attr_escape(custom_id)}"'
    cut = match.end() - len(match.group("selfclose")) - 1
    return html[:cut] + attribute + html[cut:]


def _search_open_tag(html: str) -> Optional["re.Match[str]"]:
    """The host element's start tag, skipping any comments that precede it."""
    position = 0
    while True:
        comment = _COMMENT.match(html, position) or _COMMENT.search(html, position)
        match = _OPEN_TAG.search(html, position)
        if match is None:
            return None
        if comment is None or match.start() < comment.start():
            return match
        position = comment.end()


def _attr_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


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
        if value:
            # CONF-1 - look the value up under the key it was REGISTERED under. Extraction
            # goes through normalize_phrase, so a raw lookup misses for any value carrying a
            # whitespace run, an NBSP or a %name% escape, and the attribute never translates.
            key = normalize_phrase(value)
            translated = translations.get(key)
            if translated and translated != key:
                el.set(attr, translated)
    button_attr = _button_value_raw(el)
    if button_attr is not None:
        key = normalize_phrase(button_attr)
        translated = translations.get(key)
        if translated and translated != key:
            el.set("value", translated)
    el.text = _translate_text(el.text, translations)
    for child in el:
        # MARK-2 — mirror the extraction excision. DECISION, recorded rather than left
        # implicit: a host we refuse to tokenize must also be one we refuse to rewrite.
        # Its text is not in our phrase list, so any translation we applied would be one
        # keyed to a *sibling's* phrase that happened to read the same — and the SDK
        # that owns the host re-renders it anyway, so the write is both wrong and
        # temporary. Only bites when the texts coincide, which is precisely when it is
        # hardest to notice.
        if isinstance(child.tag, str) and is_marked_host(child):
            child.tail = _translate_text(child.tail, translations)
            continue
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
    normalized = normalize_phrase(text)
    if not normalized or normalized not in translations:
        return text
    translated = translations[normalized]
    if not translated or translated == normalized:
        return text
    lead = " " if ord(text[0]) in _COLLAPSE else ""
    trail = " " if ord(text[-1]) in _COLLAPSE else ""
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
    return normalize_phrase("".join(str(t) for t in el.itertext()))
