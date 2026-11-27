"""Value markers (VAR-3): how a value printed from a variable is read back as a placeholder.

An emitter marks a printed value with a comment pair, `<!--ls:NAME-->VALUE<!--/ls-->`, or, where it
cannot write comments, with `<span data-ls-param="NAME">VALUE</span>`. Reading a tree folds each run
of text and markers under one parent into one text node holding the run's text with `{NAME}` in
place of each value, so every tokenizer and renderer downstream sees one ordinary text node:
`Hello {name}, welcome back` is one token, and one phrase for every user (VAR-1). The value is kept
as the param of its name.

A marker is read only when it is well formed: text alone between the pair, a closing comment, and
a name in VAR-2's grammar `[a-z][a-z0-9_]*` that is not a markup token (`m<N>o`, `m<N>c`). A pair
with an element inside, or a span holding an element, is ordinary markup, and ordinary comments
split text exactly as they always have, so no id derived from unmarked markup changes. A well-formed
marker whose name is outside the grammar is a value the reader can tell came from a variable but
cannot name (VAR-7): the unit holding it registers nothing and renders as it was, with one debug
notice per process.

Rendering puts the values back (`finish`). A folded node a renderer rewrote is interpolated with
its run's values, a name used only as a plain `{NAME}` through a sentinel that becomes its marker
again and a name an ICU construct uses as its raw value, so plural and select choose on it; a node
nothing rewrote is restored as it was. The output carries the markers it was given, so a later
reader, such as a browser SDK on the page, reads them again. A sentinel carries this reader's
random nonce, so text that merely looks like one stays text.
"""

from __future__ import annotations

import contextlib
import copy
import re
import secrets
import threading
from collections.abc import Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from lxml import etree
from lxml.etree import _Element

from .._log import logger

__all__ = ["ValueMarkers", "active", "may_hold"]

NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
RESERVED = re.compile(r"m\d+[oc]\Z")
PARAM_ATTRIBUTE = "data-ls-param"
#: Subtrees whose text is not prose, or that hold no comments (RCDATA).
_SKIPPED = frozenset({"script", "style", "template", "noscript", "math", "plaintext", "title",
                      "textarea", "head"})
_OPEN, _CLOSE = "", ""

_NOTICED = [False]
_NOTICE_GUARD = threading.Lock()
_ACTIVE: ContextVar[Optional[ValueMarkers]] = ContextVar("langsys_value_markers", default=None)


def may_hold(html: Optional[str]) -> bool:
    """Whether a string could hold a marker at all: unmarked content stays on its own path."""
    return bool(html) and ("<!--ls:" in str(html) or PARAM_ATTRIBUTE in str(html))


def active() -> ValueMarkers:
    """The markers of the tree being rendered in this context, or an empty reader."""
    return _ACTIVE.get() or ValueMarkers()


@dataclass
class _Marker:
    name: str
    value: str
    #: None for the comment form; the attribute form's element, emptied, to rebuild it from.
    element: Optional[_Element] = None


@dataclass
class _Run:
    parent: _Element
    #: The node whose tail holds the run, or None for the parent's own text.
    anchor: Optional[_Element]
    text: str
    #: The run as it was read: its text pieces with the markers between them.
    pieces: list[Union[str, _Marker]]
    values: dict[str, str] = field(default_factory=dict)


def _is_comment(node: _Element) -> bool:
    return node.tag is etree.Comment  # type: ignore[comparison-overlap]


def _tag(node: _Element) -> str:
    return node.tag.lower() if isinstance(node.tag, str) else ""


class ValueMarkers:
    """The markers of one tree, folded into placeholders by `read`, put back by `finish`."""

    def __init__(self) -> None:
        self._nonce = secrets.token_hex(16)
        self._runs: list[_Run] = []
        self._unnamed: list[_Element] = []
        self._issued: list[_Marker] = []
        self._sentinel = re.compile(_OPEN + self._nonce + r":(\d+)" + _CLOSE)

    # -- reading ------------------------------------------------------------------------------

    @classmethod
    def read(cls, root: _Element) -> ValueMarkers:
        """Fold every well-formed marker run below `root` into a placeholder text node."""
        markers = cls()
        markers._fold_within(root)
        return markers

    def _fold_within(self, node: _Element) -> None:
        if not isinstance(node.tag, str) or _tag(node) in _SKIPPED:
            return
        self._fold_children(node)
        for child in node:
            self._fold_within(child)

    def _marker_at(self, children: Sequence[_Element], i: int) -> Optional[tuple[_Marker, int]]:
        node = children[i]
        if _is_comment(node):
            text = node.text or ""
            if not text.startswith("ls:"):
                return None
            closing = children[i + 1] if i + 1 < len(children) else None
            if closing is None or not _is_comment(closing) or (closing.text or "").strip() != "/ls":
                return None  # unclosed, or an element inside: ordinary markup
            name = text[3:]
            if not NAME.match(name) or RESERVED.match(name):
                self._unnamed.append(node)
                return None
            return _Marker(name, node.tail or ""), 2
        if isinstance(node.tag, str) and node.get(PARAM_ATTRIBUTE) is not None:
            if len(node):
                return None  # it holds an element (or a comment): ordinary markup
            name = node.get(PARAM_ATTRIBUTE) or ""
            if not NAME.match(name) or RESERVED.match(name):
                self._unnamed.append(node)
                return None
            shell = copy.copy(node)
            shell.text, shell.tail = None, None
            return _Marker(name, node.text or "", shell), 1
        return None

    def _fold_children(self, parent: _Element) -> None:
        i = 0
        while i < len(parent):
            children = list(parent)
            if self._marker_at(children, i) is None:
                i += 1
                continue
            anchor = children[i - 1] if i > 0 else None
            before = (anchor.tail if anchor is not None else parent.text) or ""
            run = _Run(parent, anchor, before, [before])
            j = i
            while j < len(children):
                found = self._marker_at(children, j)
                if found is None:
                    break
                marker, consumed = found
                after = children[j + consumed - 1].tail or ""
                run.pieces += [marker, after]
                run.text += "{" + marker.name + "}" + after
                run.values.setdefault(marker.name, marker.value)
                j += consumed
            for node in children[i:j]:
                parent.remove(node)  # its tail goes with it, and is in the run
            self._set(parent, anchor, run.text)
            self._runs.append(run)
            i += 1

    @staticmethod
    def _get(parent: _Element, anchor: Optional[_Element]) -> str:
        return (anchor.tail if anchor is not None else parent.text) or ""

    @staticmethod
    def _set(parent: _Element, anchor: Optional[_Element], text: Optional[str]) -> None:
        if anchor is not None:
            anchor.tail = text or None
        else:
            parent.text = text or None

    # -- what a unit holds --------------------------------------------------------------------

    @staticmethod
    def _within(node: _Element, el: _Element, skip: Optional[Callable[[_Element], bool]]) -> bool:
        current: Optional[_Element] = node
        while current is not None:
            if current is el:
                return True
            if skip is not None and skip(current):
                return False
            current = current.getparent()
        return False

    def values_within(
        self, el: _Element, skip: Optional[Callable[[_Element], bool]] = None
    ) -> dict[str, str]:
        """The values of the runs inside `el`, outside any subtree `skip` keeps opaque."""
        values: dict[str, str] = {}
        for run in self._runs:
            if self._within(run.parent, el, skip):
                for name, value in run.values.items():
                    values.setdefault(name, value)
        return values

    def registers(self, el: _Element, tokens: Sequence[str]) -> bool:
        """Whether the unit `el`, with these tokens, may register. A unit holding a value the
        reader cannot name registers nothing (VAR-7), nor does one whose tokens are made only of
        its markers' placeholders, since it has no text of its own."""
        if any(self._within(node, el, None) for node in self._unnamed):
            self._notice()
            return False
        names = self.values_within(el)
        if not names:
            return True
        placeholders = re.compile("|".join(re.escape("{" + n + "}") for n in names))
        return any(placeholders.sub("", token).strip() for token in tokens)

    @staticmethod
    def _notice() -> None:
        with _NOTICE_GUARD:
            if _NOTICED[0]:
                return
            _NOTICED[0] = True
        logger.debug(
            "langsys: a value marker names its value outside [a-z][a-z0-9_]* (or as a markup "
            "token), so the unit holding it registers nothing and renders as it was (VAR-7)."
        )

    # -- rendering ----------------------------------------------------------------------------

    def params(self, values: dict[str, str], text: str) -> dict[str, str]:
        """Params that put `values` back into `text`: a name an ICU construct uses gets its raw
        value, so plural and select choose on it; any other gets a sentinel for its marker."""
        out: dict[str, str] = {}
        for name, value in values.items():
            if re.search(r"\{\s*" + re.escape(name) + r"\s*,", text):
                out[name] = value
            else:
                out[name] = self._issue(_Marker(name, value, self._form_of(name, value)))
        return out

    def _form_of(self, name: str, value: str) -> Optional[_Element]:
        for run in self._runs:
            for piece in run.pieces:
                if isinstance(piece, _Marker) and piece.name == name:
                    return piece.element
        return None

    def _issue(self, marker: _Marker) -> str:
        self._issued.append(marker)
        return f"{_OPEN}{self._nonce}:{len(self._issued) - 1}{_CLOSE}"

    @contextlib.contextmanager
    def rendering(self) -> Iterator[ValueMarkers]:
        """Make this reader the one the page walk's renderers consult."""
        token = _ACTIVE.set(self)
        try:
            yield self
        finally:
            _ACTIVE.reset(token)

    def finish(self, root: _Element, locale: str) -> None:
        """Put every value back: interpolate each folded node a renderer rewrote, expand every
        sentinel into its marker, and restore each folded node nothing rewrote."""
        if not self._runs and not self._issued:
            return
        from ..interpolate import interpolate

        alive = set(root.iter())
        alive.add(root)
        untouched: list[_Run] = []
        for run in self._runs:
            if run.parent not in alive or (run.anchor is not None and run.anchor not in alive):
                continue
            current = self._get(run.parent, run.anchor)
            if current == run.text:
                untouched.append(run)
            elif not self._sentinel.search(current):  # a renderer that put values in already
                self._set(run.parent, run.anchor,
                          interpolate(current, self.params(run.values, current), locale))
        self._expand(root)
        for run in untouched:
            self._restore(run)

    def _expand(self, root: _Element) -> None:
        holders: list[tuple[_Element, Optional[_Element]]] = []
        for node in root.iter():
            if isinstance(node.tag, str) and node.text and _OPEN in node.text:
                holders.append((node, None))
            if node is not root and node.tail and _OPEN in node.tail:
                parent = node.getparent()
                if parent is not None:
                    holders.append((parent, node))
        for parent, anchor in holders:
            pieces: list[Union[str, _Marker]] = []
            text = self._get(parent, anchor)
            offset = 0
            for match in self._sentinel.finditer(text):
                pieces.append(text[offset:match.start()])
                pieces.append(self._issued[int(match.group(1))])
                offset = match.end()
            pieces.append(text[offset:])
            self._place(parent, anchor, pieces)

    def _restore(self, run: _Run) -> None:
        self._place(run.parent, run.anchor, run.pieces)

    def _place(
        self, parent: _Element, anchor: Optional[_Element], pieces: list[Union[str, _Marker]]
    ) -> None:
        """Write `pieces` where (parent, anchor) holds its text: the first piece as that text,
        then each marker's nodes, each followed by the piece after it."""
        first = pieces[0]
        self._set(parent, anchor, first if isinstance(first, str) else "")
        index = 0 if anchor is None else list(parent).index(anchor) + 1
        k = 1
        while k < len(pieces):
            marker = pieces[k]
            after = pieces[k + 1] if k + 1 < len(pieces) else ""
            assert isinstance(marker, _Marker) and isinstance(after, str)
            for node in self._nodes(marker, after):
                parent.insert(index, node)
                index += 1
            k += 2

    @staticmethod
    def _nodes(marker: _Marker, after: str) -> list[_Element]:
        if marker.element is not None:
            element = copy.copy(marker.element)
            element.text, element.tail = marker.value or None, after or None
            return [element]
        opening = etree.Comment("ls:" + marker.name)
        opening.tail = marker.value or None
        closing = etree.Comment("/ls")
        closing.tail = after or None
        return [opening, closing]
