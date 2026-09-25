"""Server messages (spec MSG family): the entries a server sends for validation errors and system
messages, the templates they are written from, and how they are registered and rendered.

An entry is `{field?, code, message, template, params?}`, and those key names are fixed across the
fleet. `template` is the source sentence, looked up and translated as a whole; `params` fill its
`{name}` markers; `message` is the template already filled; `code` is the slug an app branches on;
`field` is a dotted path for a field failure. The body around the entries is the app's own, so they
are found wherever they sit.

The template rules are what make a message translatable at all. Everything translatable - the
field's label, an option's label - is written into the sentence, so `The password is required.` and
`The name is required.` are two phrases the translator inflects separately. A `{name}` marker holds
only a value that is not translatable: a number, a date, the user's raw input.

The pieces a framework binding supplies - the validator's failed rules, its label facility, the
redirect that carries entries to the next page - live in the binding. This module gives it the
entry shape, the fill, the template list and its checks, and the listing command.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import re
import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from ._log import logger

if TYPE_CHECKING:
    from .client import LangsysClient

__all__ = [
    "DEFAULT_MESSAGE_CATEGORY",
    "LABEL_MARKERS",
    "MESSAGE_CODES",
    "TemplateProblem",
    "TemplateRefused",
    "TemplateList",
    "fill_template",
    "resolve_server_messages",
    "server_message",
    "size_code",
    "template_markers",
    "to_server_message",
]

#: MSG-6 - templates are registered and looked up under one category, identical on the server
#: that registers them and on every client that renders them.
DEFAULT_MESSAGE_CATEGORY = "Errors"

#: MSG-2 - the shared validation vocabulary, in the spec's order. `invalid` is the code for a
#: failure that arrived with text and no rule. A code is for logic and never chooses text.
MESSAGE_CODES = (
    "required", "invalid_type", "invalid_format", "invalid_option", "invalid_date", "not_found",
    "already_taken", "mismatch", "too_short", "too_long", "too_small", "too_large", "too_few",
    "too_many", "not_allowed", "already_member", "not_member", "already_owner", "expired",
    "not_available", "invalid",
)

#: MSG-11 - marker names that carry a label by construction. A label is translatable, so it is
#: written into the sentence; a template naming one of these is refused when it is added.
LABEL_MARKERS = frozenset({"attribute", "field", "label", "other", "values"})

#: MSG-3 - a marker is a lowercase snake_case name in braces. `{Name}`, `{ min }` and `{1x}` are
#: not markers and are never filled. The server fills `message` with this grammar, so a client
#: that disagreed about what a marker is would fill a different sentence.
_MARKER = re.compile(r"\{([a-z][a-z0-9_]*)\}")

#: Placeholders another framework would have filled - left in a template, they reach the catalog
#: as literal text and the value they stood for is never translated. Laravel `:attribute`, the
#: `{{ field }}` of Vue, Jinja and Django templates, and Python's `%(name)s`, `%s` and `{0}`.
_FRAMEWORK_PLACEHOLDERS = (
    ("a Laravel placeholder", re.compile(r"(?<![\w:]):[a-z_][a-zA-Z0-9_]*")),
    ("a double-brace placeholder", re.compile(r"\{\{\s*[^{}]*?\s*\}\}")),
    # No space flag: `5% discount` is prose, not `% d`.
    ("a %-format placeholder", re.compile(r"%(?:\([^)]*\))?[#0\-]*\d*(?:\.\d+)?[sdifgexXr]")),
    ("a positional or str.format placeholder", re.compile(r"\{\d*(?:![rsa])?(?::[^{}]*)?\}")),
)

#: Error bodies are shallow; the bound stops a cyclic or pathological one.
_MAX_DEPTH = 16

Entry = dict[str, Any]


# -- markers and fill (MSG-3, MSG-4) ------------------------------------------------------------


def template_markers(template: str) -> list[str]:
    """The marker names in a template, once each, in the order they first appear."""
    if not isinstance(template, str) or not template:
        return []
    return list(dict.fromkeys(m.group(1) for m in _MARKER.finditer(template)))


def _render_param(value: Any) -> Optional[str]:
    """How a param prints into the sentence, as the reference prints it; None keeps the marker."""
    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value)


def fill_template(template: str, params: Optional[Mapping[str, Any]] = None) -> str:
    """MSG-4 - fill a template's markers. A marker with no param, a null param or a structure stays
    as its literal marker, so a missing value is visible rather than blank."""
    values = params or {}

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        rendered = _render_param(values[name]) if name in values else None
        return match.group(0) if rendered is None else rendered

    return _MARKER.sub(fill, str(template))


def server_message(
    code: str,
    template: str,
    params: Optional[Mapping[str, Any]] = None,
    field: Optional[str] = None,
) -> Entry:
    """MSG-1/MSG-4 - one entry, in the wire's key order, with `message` filled from the template.

    `params` is present only when the template has markers. Numbers stay numbers. This is the pure
    constructor; `LangsysClient.server_message` adds the MSG-8 and MSG-11 behaviour that needs a
    catalog.
    """
    markers = template_markers(template)
    values = dict(params or {})
    entry: Entry = {}
    if field:
        entry["field"] = field
    entry["code"] = code
    entry["message"] = fill_template(template, values)
    entry["template"] = template
    if markers:
        entry["params"] = {k: v for k, v in values.items() if k in markers}
    return entry


def size_code(value: Any, too: str) -> str:
    """MSG-2 - a size rule's code follows the field's type: text `too_short`/`too_long`, numbers
    `too_small`/`too_large`, lists `too_few`/`too_many`. `too` is `"small"` or `"large"`."""
    if too not in ("small", "large"):
        raise ValueError("too must be 'small' or 'large'")
    if isinstance(value, str):
        return "too_short" if too == "small" else "too_long"
    if isinstance(value, (list, tuple, set, frozenset, Mapping)):
        return "too_few" if too == "small" else "too_many"
    return "too_small" if too == "small" else "too_large"


# -- resolution (MSG-1) -------------------------------------------------------------------------


def to_server_message(value: Any) -> Optional[Entry]:
    """An entry from its wire form, or None. `code`, `message` and `template` must be strings:
    without `template` there is nothing to look up, and rendering `message` as a key is the one
    thing a client must never do."""
    if not isinstance(value, Mapping):
        return None
    code, message, template = value.get("code"), value.get("message"), value.get("template")
    if not (isinstance(code, str) and isinstance(message, str) and isinstance(template, str)):
        return None
    entry: Entry = {}
    if isinstance(value.get("field"), str) and value["field"]:
        entry["field"] = value["field"]
    entry.update(code=code, message=message, template=template)
    if isinstance(value.get("params"), Mapping):
        entry["params"] = value["params"]
    return entry


def resolve_server_messages(
    body: Any,
    *,
    key: Optional[str] = None,
    resolver: Optional[Callable[[Any], Any]] = None,
) -> list[Entry]:
    """Every entry a response carries, wherever it sits (MSG-1).

    By default the whole body is searched, so the langsys envelope, a JSON:API `errors[]` or a house
    style all resolve with nothing configured. An entry's own `params` are never searched. `key`
    narrows the search to one dotted path; `resolver` replaces it, mapping an app's native failures
    to entries. Accepts the decoded body or its JSON text; anything unreadable resolves to no
    entries rather than raising, since this runs on an error path already.
    """
    if isinstance(body, (str, bytes)):
        try:
            body = json.loads(body)
        except ValueError:
            return []
    if resolver is not None:
        mapped = resolver(body)
        items = mapped if isinstance(mapped, list) else [] if mapped is None else [mapped]
        return [e for e in (to_server_message(i) for i in items) if e is not None]
    if key:
        body = _dig(body, key)
    found: list[Entry] = []
    _walk(body, found, 0, set())
    return found


def _walk(node: Any, found: list[Entry], depth: int, seen: set[int]) -> None:
    if depth > _MAX_DEPTH or not isinstance(node, (Mapping, list)) or id(node) in seen:
        return
    seen.add(id(node))
    entry = to_server_message(node) if isinstance(node, Mapping) else None
    if entry is not None:
        found.append(entry)
    children = node.items() if isinstance(node, Mapping) else enumerate(node)
    for name, child in children:
        if entry is not None and name == "params":
            continue
        _walk(child, found, depth + 1, seen)


def _dig(body: Any, path: str) -> Any:
    node = body
    for segment in path.split("."):
        if isinstance(node, Mapping) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            node = node[int(segment)]
        else:
            return None
    return node


# -- the template list and its checks (MSG-3, MSG-7, MSG-11) ------------------------------------


class TemplateRefused(ValueError):
    """A template that breaks MSG-11's observable half, refused when it is added."""


@dataclass(frozen=True)
class TemplateProblem:
    """A message the listing cannot register ahead of time, named so someone can fix it.

    `source` is the file or class it came from, `field` the field it validates, `fix` what to do.
    """

    message: str
    source: str = ""
    field: str = ""
    fix: str = ""

    def __str__(self) -> str:
        where = [self.source] if self.source else []
        if self.field:
            where.append(f"field {self.field!r}")
        line = f"{' '.join(where)}: {self.message}" if where else self.message
        return f"{line} - {self.fix}" if self.fix else line


def check_template(template: str) -> None:
    """Refuse a template MSG-11 can see is wrong: a label-carrying marker name, or a placeholder
    another framework should have filled. Raises `TemplateRefused` naming which and why."""
    if not isinstance(template, str) or not template.strip():
        raise TemplateRefused("a template is a whole source sentence; this one is empty")
    labelled = [m for m in template_markers(template) if m in LABEL_MARKERS]
    if labelled:
        raise TemplateRefused(
            f"{template!r}: the marker {{{labelled[0]}}} carries a label, which is translatable - "
            "write the label into the sentence, one template per field (MSG-3)"
        )
    without_markers = _MARKER.sub("", template)
    for kind, pattern in _FRAMEWORK_PLACEHOLDERS:
        leftover = pattern.search(without_markers)
        if leftover:
            raise TemplateRefused(
                f"{template!r} still holds {kind}, {leftover.group(0)!r}, that should have been "
                "written into the sentence or turned into a {name} marker"
            )


Declared = Union[str, Entry, TemplateProblem]


@dataclass
class TemplateList:
    """The templates an app can emit (MSG-7): every one checked when it is added (MSG-11)."""

    templates: dict[str, str] = field(default_factory=dict)  # template -> where it came from
    problems: list[TemplateProblem] = field(default_factory=list)

    def add(self, template: str, source: str = "") -> None:
        """Add one template, or raise `TemplateRefused`."""
        check_template(template)
        self.templates.setdefault(template, source)

    def extend(self, declarations: Iterable[Declared], source: str = "") -> None:
        """Add everything a provider declares, collecting refusals and reported problems instead
        of stopping at the first, so one run names every message that needs fixing."""
        for item in declarations:
            if isinstance(item, TemplateProblem):
                self.problems.append(item)
                continue
            template = item.get("template") if isinstance(item, Mapping) else item
            origin = (item.get("source") if isinstance(item, Mapping) else None) or source
            try:
                self.add(str(template), str(origin))
            except TemplateRefused as refused:
                self.problems.append(TemplateProblem(
                    str(refused), source=str(origin),
                    field=str(item.get("field", "")) if isinstance(item, Mapping) else "",
                ))

    def __iter__(self) -> Iterator[str]:
        return iter(self.templates)

    def __len__(self) -> int:
        return len(self.templates)


def _load_provider(spec: str) -> Callable[[], Iterable[Declared]]:
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise SystemExit(f"--provider must be 'package.module:callable', not {spec!r}")
    provider = getattr(importlib.import_module(module_name), attr)
    return provider if callable(provider) else (lambda: provider)


def run_listing(
    providers: Iterable[Callable[[], Iterable[Declared]]],
    *,
    client: Optional[LangsysClient] = None,
    register: bool = False,
    category: str = DEFAULT_MESSAGE_CATEGORY,
    out: Any = None,
) -> int:
    """MSG-7 - list every template the providers declare; with `register`, register the ones the
    catalog lacks under `category`. Returns the exit code: non-zero when any message could not be
    listed, one actionable line each, so the command can gate CI."""
    stream = out or sys.stdout
    listing = TemplateList()
    for provider in providers:
        listing.extend(provider())
    for template, origin in listing.templates.items():
        print(f"{template}\t{origin}" if origin else template, file=stream)
    registered = 0
    if register and client is not None and listing.templates:
        registered = client.register_templates(listing, category=category)
    for problem in listing.problems:
        print(f"PROBLEM {problem}", file=stream)
    print(
        f"{len(listing)} template(s), {len(listing.problems)} problem(s)"
        + (f", {registered} newly registered under {category!r}" if register else ""),
        file=stream,
    )
    return 1 if listing.problems else 0


def main(argv: Optional[list[str]] = None) -> int:
    """`python -m langsys.messages --provider app.errors:templates [--register]`."""
    parser = argparse.ArgumentParser(prog="python -m langsys.messages", description=main.__doc__)
    parser.add_argument("--provider", action="append", required=True,
                        help="package.module:callable returning the declared templates; repeatable")
    parser.add_argument("--register", action="store_true",
                        help="register templates the catalog lacks (needs LANGSYS_* credentials)")
    parser.add_argument("--category", default=DEFAULT_MESSAGE_CATEGORY)
    args = parser.parse_args(argv)
    client = None
    if args.register:
        from .client import LangsysClient

        client = LangsysClient(auto_flush=False, debounce=0)
    return run_listing(
        [_load_provider(p) for p in args.provider],
        client=client, register=args.register, category=args.category,
    )


def warn_translatable_marker_value(template: str, marker: str, value: str) -> None:
    logger.warning(
        "langsys: the marker {%s} in %r was filled with %r, which is itself a phrase in the "
        "catalog. A translatable value in a marker is never translated; write it into the "
        "sentence as its own template (MSG-11).",
        marker, template, value,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
