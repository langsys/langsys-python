"""Parameter interpolation with locale-aware CLDR formatting and an ICU subset.

Behaviour mirrors the other Langsys SDKs so ``{name}`` phrases render identically
across languages:

* ``{name}`` slots are substituted from ``params``; an unknown key or ``None`` value
  is **left visible** (``{name}``) rather than blanked, so missing data is obvious.
* numbers and dates are CLDR-formatted for the target locale (pass a string to opt out
  of grouping — for ids and codes); ``bool`` renders ``"true"``/``"false"``.
* ICU MessageFormat (``{n, plural, …}`` / ``select`` / ``selectordinal`` /
  ``{n, number|date|time}``) is handled by a small pure-Python parser backed by Babel's
  CLDR plural rules — no libicu system dependency. Anything malformed degrades to simple
  interpolation instead of raising.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Optional, Union

from babel import Locale, numbers
from babel import dates as babel_dates

Params = dict[str, Any]

# Same detection as the JS/PHP SDKs: an argument whose second token is a known ICU
# keyword. The trailing ``[,}]`` also matches style-less ``{n, number}``.
_ICU_PATTERN = re.compile(
    r"\{[^{}]+,\s*(plural|select|selectordinal|number|date|time)\s*[,}]"
)
_SIMPLE_SLOT = re.compile(r"\{([^{},]+)\}")

_DATE_STYLES = {"short", "medium", "long", "full"}


def is_icu(template: str) -> bool:
    """True when ``template`` uses ICU MessageFormat syntax (plural/select/number/…)."""
    return bool(_ICU_PATTERN.search(template))


def interpolate(template: str, params: Params, locale: str = "en") -> str:
    """Render ``template`` against ``params`` in ``locale``."""
    if is_icu(template):
        try:
            nodes, _ = _parse(template, 0)
            return _render(nodes, params, locale, plural_value=None, offset=0)
        except Exception:
            # Malformed ICU (or an unexpected node) must never blow up a page.
            return _simple(template, params, locale)
    return _simple(template, params, locale)


# -- simple {name} interpolation ---------------------------------------------


def _simple(template: str, params: Params, locale: str) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in params or params[key] is None:
            return match.group(0)
        return _format_value(params[key], locale)

    return _SIMPLE_SLOT.sub(repl, template)


def _format_value(value: Any, locale: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime.datetime, datetime.date)):
        return _format_date(value, locale)
    if isinstance(value, (int, float)):
        return _format_number(value, locale)
    return str(value)


def _babel_locale(locale: str) -> Locale:
    try:
        return Locale.parse((locale or "en").replace("-", "_"))
    except Exception:
        return Locale("en")


def _format_number(value: Union[int, float], locale: str) -> str:
    try:
        return str(numbers.format_decimal(value, locale=_babel_locale(locale)))
    except Exception:
        return str(value)


def _format_date(value: Any, locale: str, style: str = "medium") -> str:
    style = style if style in _DATE_STYLES else "medium"
    try:
        if isinstance(value, datetime.datetime):
            return babel_dates.format_datetime(value, format=style, locale=_babel_locale(locale))
        return babel_dates.format_date(value, format=style, locale=_babel_locale(locale))
    except Exception:
        return str(value.isoformat()) if hasattr(value, "isoformat") else str(value)


def _format_time(value: Any, locale: str, style: str = "medium") -> str:
    style = style if style in _DATE_STYLES else "medium"
    try:
        return babel_dates.format_time(value, format=style, locale=_babel_locale(locale))
    except Exception:
        return str(value)


# -- ICU-subset parser --------------------------------------------------------
#
# The grammar handled here (a practical subset of ICU MessageFormat):
#   message   := (text | argument)*
#   argument  := '{' name '}'
#              | '{' name ',' ('number'|'date'|'time') (',' style)? '}'
#              | '{' name ',' ('plural'|'selectordinal') ',' offset? (selector '{' message '}')+ '}'
#              | '{' name ',' 'select' ',' (selector '{' message '}')+ '}'
# Within a plural/selectordinal submessage, '#' renders (value - offset) as a number.

_LiteralNode = str


class _Arg:
    __slots__ = ("name", "kind", "style", "options", "offset")

    def __init__(
        self,
        name: str,
        kind: Optional[str],
        style: Optional[str],
        options: Optional[dict[str, list[Any]]],
        offset: int,
    ) -> None:
        self.name = name
        self.kind = kind  # None | number | date | time | plural | selectordinal | select
        self.style = style
        self.options = options  # selector -> nodes
        self.offset = offset


_Node = Union[_LiteralNode, _Arg]


def _parse(text: str, i: int) -> tuple[list[_Node], int]:
    """Parse a message body starting at ``i``; stop at end or an unmatched ``}``."""
    nodes: list[_Node] = []
    buf: list[str] = []
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "}":
            break  # end of an enclosing submessage
        if ch == "'":  # ICU apostrophe escaping: '{' , '' -> '
            i = _consume_quoted(text, i, buf)
            continue
        if ch == "{":
            if buf:
                nodes.append("".join(buf))
                buf = []
            arg, i = _parse_argument(text, i)
            nodes.append(arg)
            continue
        buf.append(ch)
        i += 1
    if buf:
        nodes.append("".join(buf))
    return nodes, i


def _consume_quoted(text: str, i: int, buf: list[str]) -> int:
    # text[i] == "'"
    if i + 1 < len(text) and text[i + 1] == "'":
        buf.append("'")
        return i + 2
    j = i + 1
    while j < len(text) and text[j] != "'":
        buf.append(text[j])
        j += 1
    return j + 1 if j < len(text) else j


def _parse_argument(text: str, i: int) -> tuple[_Arg, int]:
    # text[i] == '{'
    i += 1
    name, i = _read_until(text, i, ",}")
    name = name.strip()
    if i >= len(text):
        raise ValueError("unterminated argument")
    if text[i] == "}":
        return _Arg(name, None, None, None, 0), i + 1

    # skip ','
    i += 1
    kind, i = _read_until(text, i, ",}")
    kind = kind.strip()

    if kind in ("number", "date", "time"):
        style: Optional[str] = None
        if i < len(text) and text[i] == ",":
            style_text, i = _read_until(text, i + 1, "}")
            style = style_text.strip() or None
        _expect(text, i, "}")
        return _Arg(name, kind, style, None, 0), i + 1

    if kind in ("plural", "selectordinal", "select"):
        _expect(text, i, ",")
        options, offset, i = _parse_options(text, i + 1)
        _expect(text, i, "}")
        return _Arg(name, kind, None, options, offset), i + 1

    raise ValueError(f"unsupported argument type: {kind!r}")


def _parse_options(text: str, i: int) -> tuple[dict[str, list[_Node]], int, int]:
    options: dict[str, list[_Node]] = {}
    offset = 0
    n = len(text)
    while i < n:
        i = _skip_ws(text, i)
        if i >= n or text[i] == "}":
            break
        # A selector is a single whitespace-/brace-delimited token (e.g. "one", "=0",
        # "offset:1"), so offset and the following selector don't get merged.
        start = i
        while i < n and not text[i].isspace() and text[i] != "{":
            i += 1
        selector = text[start:i]
        if selector.startswith("offset:"):
            offset = int(selector[len("offset:") :])
            continue
        i = _skip_ws(text, i)
        _expect(text, i, "{")
        nodes, i = _parse(text, i + 1)
        _expect(text, i, "}")
        i += 1
        if selector:
            options[selector] = nodes
    return options, offset, i


def _skip_ws(text: str, i: int) -> int:
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    return i


def _read_until(text: str, i: int, stops: str) -> tuple[str, int]:
    start = i
    n = len(text)
    while i < n and text[i] not in stops:
        i += 1
    return text[start:i], i


def _expect(text: str, i: int, ch: str) -> None:
    if i >= len(text) or text[i] != ch:
        raise ValueError(f"expected {ch!r} at position {i}")


# -- ICU render ---------------------------------------------------------------


def _render(
    nodes: list[_Node], params: Params, locale: str, plural_value: Optional[float], offset: int
) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            out.append(_apply_hash(node, plural_value, offset, locale))
        else:
            out.append(_render_arg(node, params, locale))
    return "".join(out)


def _apply_hash(text: str, plural_value: Optional[float], offset: int, locale: str) -> str:
    if plural_value is None or "#" not in text:
        return text
    return text.replace("#", _format_number(plural_value - offset, locale))


def _render_arg(arg: _Arg, params: Params, locale: str) -> str:
    if arg.name not in params or params[arg.name] is None:
        return "{" + arg.name + "}"
    value = params[arg.name]

    if arg.kind is None:
        return _format_value(value, locale)
    if arg.kind == "number":
        return _format_number(value, locale)
    if arg.kind == "date":
        return _format_date(value, locale, arg.style or "medium")
    if arg.kind == "time":
        return _format_time(value, locale, arg.style or "medium")

    options = arg.options or {}
    if arg.kind == "select":
        branch = options.get(str(value)) or options.get("other") or []
        return _render(branch, params, locale, plural_value=None, offset=0)

    # plural / selectordinal
    number = float(value)
    exact = options.get("=" + _int_key(number))
    if exact is not None:
        return _render(exact, params, locale, plural_value=number, offset=arg.offset)
    category = _plural_category(number - arg.offset, locale, ordinal=arg.kind == "selectordinal")
    branch = options.get(category) or options.get("other") or []
    return _render(branch, params, locale, plural_value=number, offset=arg.offset)


def _int_key(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


def _plural_category(number: float, locale: str, *, ordinal: bool) -> str:
    try:
        loc = _babel_locale(locale)
        rule = loc.ordinal_form if ordinal else loc.plural_form
        return str(rule(number))
    except Exception:
        return "other"
