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

import contextlib
import datetime
import logging
import re
from typing import Any, Optional, Union

from babel import Locale, numbers
from babel import dates as babel_dates

from ._log import logger

Params = dict[str, Any]

#: ICU-4 dedup: one notice per ``(template, locale)`` for the process lifetime. The
#: same phrase renders thousands of times and the developer needs to learn once.
_NOTICED: set[tuple[str, str]] = set()


#: ICU-6 dedup: one warning per ``(template, locale)`` that the formatter could not render.
_FAILED: set[tuple[str, str]] = set()


def reset_recovery_notices() -> None:
    """Forget which ``(template, locale)`` pairs have been noticed or warned. Test seam."""
    _NOTICED.clear()
    _FAILED.clear()


class _FormatFailure(ValueError):
    """A construct the formatter cannot render - no branch applies, an unsupported type."""

# Same detection as the JS/PHP SDKs: an argument whose second token is a known ICU
# keyword. The trailing ``[,}]`` also matches style-less ``{n, number}``.
_ICU_PATTERN = re.compile(
    r"\{[^{}]+,\s*(plural|select|selectordinal|number|date|time)\s*[,}]"
)
_SIMPLE_SLOT = re.compile(r"\{([^{},]+)\}")
_ANY_CONSTRUCT = re.compile(r"\{\s*[A-Za-z_]\w*\s*,\s*[A-Za-z]")

#: TOK-5 — `%name%` is accepted as an escape for `{name}`.
#:
#: `{` is not inert in a template compiler: several of the frameworks we ship bindings
#: for treat it as an expression delimiter, so an author who cannot get `{name}` past
#: their own build needs a form that survives it — and having provided one, the fleet
#: has to read it back.
#:
#: The name is required to look like an identifier. A looser pattern would read the `%`
#: in `100% of 50%` as a delimiter and eat the text between them, which is a far more
#: common shape in real copy than the escape itself.
_PERCENT_SLOT = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def percent_placeholders_to_braces(text: str) -> str:
    """TOK-5 - rewrite every identifier-shaped `%name%` to `{name}`, for CAPTURE.

    Distinct from the interpolator's rewrite, which is gated on the name being a supplied
    argument. At capture there are no arguments, only markup being turned into a phrase, and
    8.0.1 requires `%name%` seen in captured markup to normalise to `{name}` BEFORE the token
    is derived - so markup authored with the escape and markup authored with braces register
    one phrase under one id. Same identifier guard, from the same pattern, so `100% of 50%`
    stays prose on both sides.
    """
    if "%" not in text:
        return text
    return _PERCENT_SLOT.sub(lambda match: "{" + match.group(1) + "}", text)


_DATE_STYLES = {"short", "medium", "long", "full"}


def is_icu(template: str) -> bool:
    """True when ``template`` uses ICU MessageFormat syntax (plural/select/number/…)."""
    return bool(_ICU_PATTERN.search(template))


def interpolate(template: str, params: Params, locale: str = "en") -> str:
    """Render ``template`` against ``params`` in ``locale``.

    A ``select``/``plural`` whose argument was not supplied renders its ``other``
    branch (ICU-1) rather than the raw source. Only the missing nodes are rewritten:
    everything else keeps full CLDR selection through the same renderer (ICU-5).
    """
    # TOK-5 — the escape is resolved here, on the template, so everything downstream
    # sees one placeholder form and no substituted value is ever re-scanned.
    template = _rewrite_percent_slots(template, params)
    # Any `{name, kind ...}` construct goes to the ICU renderer, including a kind it does not
    # support: that fails there and recovers through ICU-6, where the simple path would print it.
    if is_icu(template) or _ANY_CONSTRUCT.search(template):
        recovered: list[str] = []
        try:
            nodes, _ = _parse(template, 0)
            out = _render(
                nodes, params, locale, plural_value=None, offset=0, recovered=recovered
            )
        except Exception as exc:
            # ICU-6 - a phrase the formatter cannot render goes through our own branch
            # selection, and warns: an empty string or raw ICU syntax on the page is a
            # silent hole only a developer can read.
            _warn_format_failure(template, locale, exc)
            return _lenient(template, params, locale, None)
        if recovered:
            _notice_recovery(template, locale, recovered)
        return out
    return _simple(template, params, locale)


def _notice_recovery(template: str, locale: str, recovered: list[str]) -> None:
    """ICU-4 — say which arguments were defaulted, once per ``(template, locale)``.

    The recovered argument never appears in the source phrase, so there is nothing
    for a developer to grep for and no failing behaviour to notice. Debug level only:
    a notice that ignores the log level warns in production on every render.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        # Not marked as noticed — otherwise enabling debug later would stay silent.
        return
    key = (template, locale)
    if key in _NOTICED:
        return
    _NOTICED.add(key)
    # dict.fromkeys preserves first-seen order while de-duplicating.
    names = ", ".join(dict.fromkeys(recovered))
    logger.debug(
        "langsys: interpolation recovery in locale %s — defaulted to the 'other' branch "
        "for argument(s): %s. This is normal when the target translation needs an "
        "argument the source phrase never had; pass %s in params to select a branch. "
        "Template: %s",
        locale,
        names,
        names,
        template,
    )


# -- simple {name} interpolation ---------------------------------------------


def _simple(template: str, params: Params, locale: str) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in params or params[key] is None:
            return match.group(0)
        return _format_value(params[key], locale)

    return _SIMPLE_SLOT.sub(repl, template)


def _rewrite_percent_slots(template: str, params: Params) -> str:
    """TOK-5 — rewrite the `%name%` escape to `{name}` **in the template**.

    Applied to the template before rendering, never to the rendered output. Rewriting
    the output would re-scan substituted values, so a parameter whose *value* contained
    `%other%` would pull in another parameter — user-supplied data reaching arguments
    it was never given. `{name}` has never had that exposure because substitution
    happens once; the escape has to match it.

    Left exactly as authored when the argument is absent or null, so an unresolved slot
    stays visible in the form the author wrote (ICU-4's observability requirement
    reaching this rule), and when the name is not an argument at all, so prose
    containing percent signs survives untouched.
    """
    if "%" not in template:
        return template

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in params and params[key] is not None:
            return "{" + key + "}"
        return match.group(0)

    return _PERCENT_SLOT.sub(repl, template)


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
    nodes: list[_Node],
    params: Params,
    locale: str,
    plural_value: Optional[float],
    offset: int,
    recovered: list[str],
    hash_literal: Optional[str] = None,
) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            out.append(_apply_hash(node, plural_value, offset, locale, hash_literal))
        else:
            out.append(_render_arg(node, params, locale, recovered))
    return "".join(out)


def _apply_hash(
    text: str,
    plural_value: Optional[float],
    offset: int,
    locale: str,
    hash_literal: Optional[str] = None,
) -> str:
    if "#" not in text:
        return text
    if hash_literal is not None:
        # ICU-3 — inside a recovered plural there is no count. A plausible `0`
        # reads as correct and states something false; `{count}` is visibly a gap.
        return text.replace("#", hash_literal)
    if plural_value is None:
        return text
    return text.replace("#", _format_number(plural_value - offset, locale))


def _recover(arg: _Arg, params: Params, locale: str, recovered: list[str]) -> str:
    """ICU-1 — render the ``other`` branch of a node whose argument is missing.

    Raises when there is no ``other`` branch: that node is malformed, and ICU-1 says
    to leave it to normal error handling rather than invent a fallback. The caller's
    ``except`` degrades the whole template to simple interpolation.
    """
    options = arg.options or {}
    branch = options.get("other")
    if branch is None:
        raise ValueError(f"recovery needs an 'other' branch for {arg.name!r}")
    recovered.append(arg.name)
    return _render(
        branch,
        params,
        locale,
        plural_value=None,
        offset=0,
        recovered=recovered,
        # `#` has no count to render; emit the argument name instead.
        hash_literal="{" + arg.name + "}" if arg.kind != "select" else None,
    )


def _render_arg(arg: _Arg, params: Params, locale: str, recovered: list[str]) -> str:
    if arg.name not in params or params[arg.name] is None:
        # ICU-2 — present-but-null is absent. For a branching node that means
        # recovery (ICU-1); for a plain argument the slot stays visible.
        if arg.kind in ("plural", "selectordinal", "select"):
            return _recover(arg, params, locale, recovered)
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
        branch = options.get(str(value)) or options.get("other")
        if branch is None:
            raise _FormatFailure(f"no branch of {arg.name!r} fits {value!r}, and none is other")
        return _render(branch, params, locale, plural_value=None, offset=0, recovered=recovered)

    # plural / selectordinal — supplied, so it keeps full CLDR selection (ICU-5).
    number = float(value)
    exact = options.get("=" + _int_key(number))
    if exact is not None:
        return _render(
            exact, params, locale, plural_value=number, offset=arg.offset, recovered=recovered
        )
    category = _plural_category(number - arg.offset, locale, ordinal=arg.kind == "selectordinal")
    branch = options.get(category) or options.get("other")
    if branch is None:
        raise _FormatFailure(f"no branch of {arg.name!r} applies to {value!r} and none is other")
    return _render(
        branch, params, locale, plural_value=number, offset=arg.offset, recovered=recovered
    )


def _int_key(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


def _plural_category(number: float, locale: str, *, ordinal: bool) -> str:
    try:
        loc = _babel_locale(locale)
        rule = loc.ordinal_form if ordinal else loc.plural_form
        return str(rule(number))
    except Exception:
        return "other"


# -- ICU-6: rendering what the formatter could not --------------------------------------------


def _warn_format_failure(template: str, locale: str, exc: Exception) -> None:
    """At WARNING whatever the log level, once per ``(template, locale)``. Unlike a missing
    argument (ICU-4, debug), a phrase the formatter cannot render is a defect someone must fix."""
    key = (template, locale)
    if key in _FAILED:
        return
    _FAILED.add(key)
    logger.warning(
        "langsys: the formatter could not render this phrase in locale %s (%s); it was "
        "rendered through branch selection instead. Fix the phrase: %s",
        locale,
        exc,
        template,
    )


def _match_brace(text: str, start: int) -> int:
    """Index of the `}` closing the `{` at `start`, or len(text) when it is never closed."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _lenient(text: str, params: Params, locale: str, hash_value: Optional[str]) -> str:
    """Render `text` tolerating anything malformed: every construct becomes its chosen branch or
    its value, a value not supplied stays the visible `{name}`, and no construct syntax - and no
    empty string where a value exists - reaches the output."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            end = _match_brace(text, i)
            out.append(_lenient_arg(text[i + 1:end], params, locale))
            i = end + 1
        elif ch == "#" and hash_value is not None:
            out.append(hash_value)
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _split_top(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0 and len(parts) < limit - 1:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _lenient_options(text: str) -> tuple[dict[str, str], int]:
    options: dict[str, str] = {}
    offset, i, n = 0, 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        start = i
        while i < n and not text[i].isspace() and text[i] != "{":
            i += 1
        selector = text[start:i]
        if selector.startswith("offset:"):
            with contextlib.suppress(ValueError):
                offset = int(selector[len("offset:"):])
            continue
        while i < n and text[i].isspace():
            i += 1
        if i >= n or text[i] != "{":
            break
        end = _match_brace(text, i)
        if selector:
            options[selector] = text[i + 1:end]
        i = end + 1
    return options, offset


def _lenient_arg(inner: str, params: Params, locale: str) -> str:
    parts = _split_top(inner, 3)
    name = parts[0].strip()
    value = params.get(name)
    kind = parts[1].strip() if len(parts) > 1 else ""
    if kind in ("plural", "selectordinal", "select"):
        options, offset = _lenient_options(parts[2] if len(parts) > 2 else "")
        if value is None:
            branch = options.get("other")
            if branch is None:
                return "{" + name + "}"
            return _lenient(branch, params, locale, "{" + name + "}" if kind != "select" else None)
        if kind == "select":
            branch = options.get(str(value), options.get("other"))
            return str(value) if branch is None else _lenient(branch, params, locale, None)
        try:
            number = float(value)
        except (TypeError, ValueError):
            branch = options.get(str(value), options.get("other"))
            return str(value) if branch is None else _lenient(branch, params, locale, None)
        shown = _format_number(number - offset, locale)
        branch = options.get("=" + _int_key(number))
        if branch is None:
            category = _plural_category(number - offset, locale, ordinal=kind == "selectordinal")
            branch = options.get(category, options.get("other"))
        return shown if branch is None else _lenient(branch, params, locale, shown)
    if value is None:
        return "{" + name + "}" if name else "{" + inner + "}"
    if kind == "number":
        return _format_number(value, locale) if isinstance(value, (int, float)) else str(value)
    return _format_value(value, locale)
