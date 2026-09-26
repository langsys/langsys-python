"""Server messages (spec MSG family): translation for a framework's own error messages.

Validation errors exist only after a failed submit, so no visitor's page discovers them; the server
registers them. What every Langsys SDK agrees on is exactly what translation needs: the
**template** - the framework's own sentence, unfilled, with the field's label written in where it
references the field and each non-translatable value left as a `{name}` marker - and the
**params** that fill it, registered under one category. `message`, the filled template, is the
fallback a client shows when it cannot look the template up.

Everything around that pair belongs to the app and its framework: the error body the entries
travel in (entries are attached beside it, never replacing it), the key they sit under, the
framework's own identifier for the failure (`code`, passed through, absent where the framework has
none) and its path format for the field. This module adds no vocabulary, no wording and no
envelope of its own.

The framework-shaped halves - turning a validator's failures into entries, reading its labels,
carrying entries across a redirect - live in each framework binding. This module gives them the
entry, the fill, entry resolution, the template list and its check, and the listing command.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import re
import sys
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence, Union

from ._log import logger

if TYPE_CHECKING:
    from .client import LangsysClient

__all__ = [
    "DEFAULT_ATTACH_KEY",
    "DEFAULT_LABEL_PLACEHOLDERS",
    "DEFAULT_MESSAGE_CATEGORY",
    "Pieces",
    "TemplateList",
    "TemplateProblem",
    "TemplateRefused",
    "attach_server_messages",
    "fill_template",
    "resolve_server_messages",
    "run_listing",
    "server_message",
    "template_markers",
    "to_server_message",
]

#: MSG-6 - templates are registered and looked up under one category, identical on the server
#: that registers them and on every client that renders them.
DEFAULT_MESSAGE_CATEGORY = "Errors"

#: MSG-1 - the key entries are attached under, beside the framework's own error body. The same
#: default as the Laravel binding's, so a client resolving by configuration sees one name.
DEFAULT_ATTACH_KEY = "langsys_errors"

#: MSG-11 - the Python frameworks' own label placeholders: a template still holding one should
#: have had the label written in (MSG-3). Django's model validation messages (`%(field_label)s`,
#: `%(field_labels)s` for unique_together, `%(date_field_label)s` for unique_for_date/month/year,
#: `%(model_name)s`), a form message's `%(field)s`, and a template-language `{{ field }}`. A
#: binding names its framework's own; Pydantic's messages carry none.
DEFAULT_LABEL_PLACEHOLDERS = (
    "%(field_label)s", "%(field_labels)s", "%(date_field_label)s", "%(model_name)s",
    "%(field)s", "{{ field }}",
)

#: MSG-3 - a marker is a lowercase snake_case name in braces. `{Name}`, `{ min }` and `{1x}` are
#: not markers and are never filled. The server fills `message` with this grammar, so a client
#: that disagreed about what a marker is would fill a different sentence.
_MARKER = re.compile(r"\{([a-z][a-z0-9_]*)\}")

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
    template: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    field: Any = None,
    code: Any = None,
) -> Entry:
    """MSG-1/MSG-4 - one entry: the template, its params, and `message` filled from them.

    `field` (the framework's own path format - a dotted string, Pydantic's `loc` list) and `code`
    (the framework's own identifier for the failure) are passed through unchanged, and omitted
    when the framework has none. `params` is present only when the template has markers; numbers
    stay numbers. This is the pure constructor; `LangsysClient.server_message` adds the MSG-8 and
    MSG-11 behaviour that needs a catalog.
    """
    markers = template_markers(template)
    values = dict(params or {})
    entry: Entry = {"template": template}
    if markers:
        entry["params"] = {k: v for k, v in values.items() if k in markers}
    entry["message"] = fill_template(template, values)
    if field is not None:
        entry["field"] = field
    if code is not None:
        entry["code"] = code
    return entry


def attach_server_messages(
    body: MutableMapping[str, Any], entries: Sequence[Entry], key: str = DEFAULT_ATTACH_KEY
) -> MutableMapping[str, Any]:
    """MSG-1 - attach entries beside the framework's native error body, which is left otherwise
    exactly as it was. Returns the same body."""
    if key in body:
        raise ValueError(f"the error body already has a {key!r} member; attach under another key")
    body[key] = list(entries)
    return body


# -- resolution (MSG-1) -------------------------------------------------------------------------


#: The piece names an app's entries use where they differ from the SDK's (MSG-1).
Pieces = Mapping[str, str]


def to_server_message(value: Any, pieces: Optional[Pieces] = None) -> Optional[Entry]:
    """An entry from its wire form, in the SDK's piece names, or None when it is not one. An entry
    needs a string `template` to look up or a string `message` to show. `params` is kept when it is
    a map; `field` and `code` pass through as the framework wrote them. `pieces` maps the SDK's
    piece names to the ones the server was configured with."""
    if not isinstance(value, Mapping):
        return None
    names = pieces or {}

    def read(piece: str) -> Any:
        return value.get(names.get(piece, piece))

    template, message, params = read("template"), read("message"), read("params")
    if not isinstance(template, str) and not isinstance(message, str):
        return None
    entry: Entry = {}
    if isinstance(template, str):
        entry["template"] = template
    if isinstance(params, Mapping):
        entry["params"] = params
    if isinstance(message, str):
        entry["message"] = message
    field_, code = read("field"), read("code")
    if field_ not in (None, ""):
        entry["field"] = field_
    if code is not None:
        entry["code"] = code
    return entry


def resolve_server_messages(
    body: Any,
    *,
    key: Optional[str] = None,
    resolver: Optional[Callable[[Any], Any]] = None,
    pieces: Optional[Pieces] = None,
) -> list[Entry]:
    """The entries a response carries, found where the app's configuration says they are (MSG-1).

    The body is the framework's own and is never searched by shape: `key` is the dotted path the
    server attached the entries under (`DEFAULT_ATTACH_KEY` unless configured otherwise), and
    `resolver` maps the body to entries itself. One of them is required. `pieces` renames the
    entries' pieces. At the key sits a list of entries, a single entry, or a field -> entries map.
    Accepts the decoded body or its JSON text and never changes the body; unreadable JSON or a key
    the body does not carry resolves to no entries, since this runs in an error path already.
    """
    if resolver is None and not key:
        raise TypeError(
            "resolve_server_messages needs to know where the entries sit: pass key= with the path "
            f"the server attaches them under ({DEFAULT_ATTACH_KEY!r} by default), or resolver="
        )
    if isinstance(body, (str, bytes)):
        try:
            body = json.loads(body)
        except ValueError:
            return []
    found = resolver(body) if resolver is not None else _dig(body, key or "")
    entries = (to_server_message(item, pieces) for item in _items(found))
    return [entry for entry in entries if entry is not None]


def _items(found: Any) -> list[Any]:
    """A list of entries, a single entry, or a field -> entries map, as items."""
    if isinstance(found, list):
        return found
    if not isinstance(found, Mapping):
        return []
    values = list(found.values())
    if values and all(isinstance(v, list) for v in values):
        return [item for v in values for item in v]
    return [found]


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


# -- the template list and its check (MSG-7, MSG-11) --------------------------------------------


class TemplateRefused(ValueError):
    """A template still holding its framework's label placeholder, refused when it is added."""


@dataclass(frozen=True)
class TemplateProblem:
    """A message the listing cannot register ahead of time, named so someone can fix it.

    `source` is the file or class it came from, `field` the field it validates, `fix` what would
    make it listable. Advice, not an error: MSG-8 registers it the first time it is emitted.
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


@dataclass
class TemplateList:
    """The templates an app can emit (MSG-7), each checked as it is added (MSG-11)."""

    label_placeholders: Sequence[str] = DEFAULT_LABEL_PLACEHOLDERS
    templates: dict[str, str] = field(default_factory=dict)  # template -> where it came from
    problems: list[TemplateProblem] = field(default_factory=list)

    def check(self, template: str) -> None:
        """Refuse a template that still holds one of its framework's label placeholders: the label
        should have been written in (MSG-3)."""
        if not isinstance(template, str) or not template.strip():
            raise TemplateRefused("a template is the framework's sentence; this one is empty")
        for placeholder in self.label_placeholders:
            if placeholder in template:
                raise TemplateRefused(
                    f"{template!r} still holds the label placeholder {placeholder!r}; write the "
                    "field's label into the sentence, one template per field (MSG-3)"
                )

    def add(self, template: str, source: str = "") -> None:
        """Add one template, or raise `TemplateRefused`."""
        self.check(template)
        self.templates.setdefault(template, source)

    def extend(self, declarations: Iterable[Declared], source: str = "") -> None:
        """Add everything a provider declares, collecting refusals and reported problems."""
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


Declared = Union[str, Entry, TemplateProblem]


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
    label_placeholders: Sequence[str] = DEFAULT_LABEL_PLACEHOLDERS,
    strict: bool = False,
    out: Any = None,
) -> int:
    """MSG-7 - list every template the providers declare; with `register`, register the ones the
    catalog lacks under `category`. A message that cannot be listed is reported with an actionable
    line and is not an error - MSG-8 registers it when first emitted - unless `strict` is set."""
    stream = out or sys.stdout
    listing = TemplateList(label_placeholders=label_placeholders)
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
    return 1 if strict and listing.problems else 0


def main(argv: Optional[list[str]] = None) -> int:
    """`python -m langsys.messages --provider app.errors:templates [--register] [--strict]`."""
    parser = argparse.ArgumentParser(prog="python -m langsys.messages", description=main.__doc__)
    parser.add_argument("--provider", action="append", required=True,
                        help="package.module:callable returning the declared templates; repeatable")
    parser.add_argument("--register", action="store_true",
                        help="register templates the catalog lacks (needs LANGSYS_* credentials)")
    parser.add_argument("--category", default=DEFAULT_MESSAGE_CATEGORY)
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero when any message cannot be listed ahead of time")
    args = parser.parse_args(argv)
    client = None
    if args.register:
        from .client import LangsysClient

        client = LangsysClient(auto_flush=False, debounce=0)
    return run_listing(
        [_load_provider(p) for p in args.provider],
        client=client, register=args.register, category=args.category, strict=args.strict,
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
