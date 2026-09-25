"""Legacy-key migration (spec MIG): an i18n-keyed app keeps its source-language file, and `t()`
resolves an argument as a KEY in it first.

A hit takes the key's source value as the phrase - source text is the key, the Langsys way - and a
miss treats the argument as literal source text, registered as usual. The catalog only ever holds
source text; a key never reaches Langsys, so a later codemod can inline each value and delete the
file with no change to any id. The mode is off unless files are configured, so an app that has
finished migrating pays nothing.

Formats this core reads, the ones its ecosystem's entry points use:

* ``plain`` - a JSON file of (possibly nested) keys; the default for ``.json``. Nested keys
  resolve by dotted path, and the first segment of a dotted key is its namespace.
* ``gettext`` - a ``.po`` file, Django's and any gettext app's. A ``msgid`` is already source
  text, so a hit is the identity case: it contributes its ``msgctxt`` as the category, and an
  entry with ``msgid_plural`` becomes ``{count, plural, =1 {...} other {...}}``. A ``.mo`` is the
  compiled artifact of a ``.po`` and is refused with a hint to point at its ``.po``.

Any other configured format is refused when the client is built, naming the format and the file.

Placeholders in a file's values convert whatever the format: ``{{name}}``, ``{name}``, ``:name``,
``%{name}``, ``%(name)s`` and ``%(name)d`` become ``{name}``, and ``%%`` is a literal ``%``. A form
``{name}`` cannot express - ``%(name).2f``, ``%<name>.2f``, a positional ``%s``, Laravel's
``:Name`` casing - registers verbatim with a warning, and so does a ``|`` in a ``plain`` file:
never silently mangled.

A literal miss converts under the syntax of the entry point that received it, since that
framework acts on the text: Langsys ``t()`` converts nothing; a gettext-family call converts only
the placeholders it passes. `convert_literal` is that converter, exposed for framework bindings.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from ._log import logger
from .exceptions import ConfigurationError

__all__ = [
    "SUPPORTED_FORMATS",
    "LegacyFile",
    "LegacyKeys",
    "convert_literal",
    "convert_value",
    "gettext_plural",
]

#: MIG-7 - the formats this core's ecosystem uses. Others are refused at load, by name.
SUPPORTED_FORMATS = ("gettext", "plain")

_BRACED = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_RAILS = re.compile(r"%\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PY_NAMED = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)([sd])")
_PY_FORMATTED = re.compile(
    r"%\([A-Za-z_][A-Za-z0-9_]*\)[#0\- +]*\d*\.\d+[a-z]|%<[A-Za-z_]\w*>[^\s]*"
)
_PY_POSITIONAL = re.compile(r"%[#0\-+]?\d*(?:\.\d+)?[sdif]")
_COLON = re.compile(r"(?<![\w:]):([a-z_][a-z0-9_]*)\b")
_COLON_CASED = re.compile(r"(?<![\w:]):([A-Z][A-Za-z0-9_]*)\b")


@dataclass(frozen=True)
class LegacyFile:
    """One configured source file and its declared format (MIG-7)."""

    path: Union[str, Path]
    format: Optional[str] = None


@dataclass
class _Entry:
    phrase: str
    category: Optional[str]
    source: str


def _warn(message: str, *args: Any) -> None:
    logger.warning("langsys: " + message, *args)


def convert_value(text: str, *, where: str = "") -> str:
    """MIG-4 - a file value's placeholders, converted the same way whatever its format."""
    unconvertible = (
        _PY_FORMATTED.search(text)
        or _COLON_CASED.search(text)
        or _PY_POSITIONAL.search(_PY_NAMED.sub("", text.replace("%%", "")))
    )
    if unconvertible:
        _warn("%s: %r holds %r, which {name} cannot express; registered verbatim.",
              where or "legacy value", text, unconvertible.group(0))
        return text
    converted = _BRACED.sub(r"{\1}", text)
    converted = _RAILS.sub(r"{\1}", converted)
    converted = _PY_NAMED.sub(r"{\1}", converted)
    converted = _COLON.sub(r"{\1}", converted)
    return converted.replace("%%", "%")


def convert_literal(
    text: str, entry_point: str = "t", passed: Iterable[str] = ()
) -> str:
    """MIG-2 - a literal miss, converted under the syntax of the entry point that received it.

    ``"t"`` (Langsys) converts nothing: its argument is already Langsys syntax. ``"gettext"``
    (``gettext``/``_()``/``pgettext``) converts only the ``%(name)s`` placeholders the call
    passes; ``"blocktranslate"`` converts only the ``{{ name }}`` placeholders it passes. What the
    call did not pass is printed as written by that framework, so it stays literal here too.
    """
    names = set(passed)
    if entry_point == "t":
        return text

    def passed_only(match: re.Match[str]) -> str:
        return "{" + match.group(1) + "}" if match.group(1) in names else match.group(0)

    if entry_point == "gettext":
        return _PY_NAMED.sub(passed_only, text)
    if entry_point == "blocktranslate":
        return _BRACED.sub(passed_only, text)
    raise ValueError(f"unknown entry point {entry_point!r}; expected t, gettext or blocktranslate")


def gettext_plural(
    singular: str, plural: str, *, literal: bool = False, passed: Iterable[str] = ()
) -> str:
    """MIG-4's ``gettext`` row: ``=1 {msgid} other {msgid_plural}``, because untranslated gettext
    returns ``msgid`` only when n is exactly 1. The count renders as ``#`` inside the branches;
    the argument is named ``count``. `literal` converts as an ``ngettext`` call would.
    """
    def branch(text: str) -> str:
        text = re.sub(r"%\(count\)[sd]", "#", text)
        return convert_literal(text, "gettext", passed) if literal else convert_value(text)

    return "{count, plural, =1 {" + branch(singular) + "} other {" + branch(plural) + "}}"


# -- files -----------------------------------------------------------------------------------------


def _flatten(node: Any, prefix: str, out: dict[str, str], where: str) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            _flatten(value, f"{prefix}.{key}" if prefix else str(key), out, where)
    elif isinstance(node, str):
        out[prefix] = node


_PO_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_PO_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r"}


def _po_unquote(parts: list[str]) -> str:
    joined = "".join(parts)
    return re.sub(r"\\(.)", lambda m: _PO_ESCAPES.get(m.group(1), m.group(1)), joined)


def _parse_po(text: str) -> list[dict[str, Any]]:
    """The entries of a `.po` file: msgctxt, msgid, msgid_plural, fuzzy. msgstr is not read here."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    field_name: Optional[str] = None

    def finish() -> None:
        if "msgid" in current:
            entries.append(
                {k: (_po_unquote(v) if isinstance(v, list) else v) for k, v in current.items()}
            )

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            finish()
            current, field_name = {}, None
            continue
        if line.startswith("#,") and "fuzzy" in line:
            current["fuzzy"] = True
            continue
        if line.startswith("#"):
            continue
        keyword = re.match(r"(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(.*)", line)
        if keyword:
            if keyword.group(1) == "msgctxt" and "msgid" in current:
                finish()
                current = {}
            field_name = keyword.group(1)
            current[field_name] = _PO_STRING.findall(keyword.group(2))
        elif field_name and line.startswith('"'):
            current[field_name].extend(_PO_STRING.findall(line))
    finish()
    return entries


class LegacyKeys:
    """The configured source files, loaded once: key -> (phrase, category), first file wins."""

    def __init__(self, files: Sequence[Union[str, Path, LegacyFile]]) -> None:
        self.entries: dict[tuple[Optional[str], str], _Entry] = {}
        #: Each key's first entry under any context, for a call that names no category.
        self._by_key: dict[str, _Entry] = {}
        self.duplicates: list[str] = []
        self.unrecognised: list[str] = []
        for item in files:
            spec = item if isinstance(item, LegacyFile) else LegacyFile(item)
            self._load(spec)

    # MIG-7
    def _load(self, spec: LegacyFile) -> None:
        path = Path(spec.path)
        suffix = path.suffix.lower()
        fmt = spec.format or {".json": "plain", ".po": "gettext", ".mo": "gettext"}.get(suffix)
        if suffix == ".mo":
            raise ConfigurationError(
                f"Langsys: {path} is a compiled gettext catalog. Configure its source instead: "
                f"{path.with_suffix('.po')}"
            )
        if fmt not in SUPPORTED_FORMATS:
            raise ConfigurationError(
                f"Langsys: legacy file {path} is format {fmt or suffix or 'unknown'!r}, which this "
                f"SDK does not read; supported: {', '.join(SUPPORTED_FORMATS)}"
            )
        text = path.read_text(encoding="utf-8")
        if fmt == "plain":
            flat: dict[str, str] = {}
            _flatten(json.loads(text), "", flat, str(path))
            for key, value in flat.items():
                where = f"{path} key {key!r}"
                if "|" in value:
                    _warn("%s: a '|' in a plain file is text, not a plural; registered "
                          "verbatim.", where)
                    self.unrecognised.append(where)
                    phrase = value
                else:
                    phrase = convert_value(value, where=where)
                namespace = key.split(".", 1)[0] if "." in key else None
                self._add((None, key), _Entry(phrase, namespace, str(path)), key)
        else:
            for entry in _parse_po(text):
                msgid = entry["msgid"]
                if not msgid:
                    continue  # the header
                context = entry.get("msgctxt") or None
                where = f"{path} msgid {msgid!r}"
                if entry.get("msgid_plural") is not None:
                    phrase = gettext_plural(msgid, entry["msgid_plural"])
                else:
                    phrase = convert_value(msgid, where=where)
                self._add((context, msgid), _Entry(phrase, context, str(path)), msgid)

    def _add(self, key: tuple[Optional[str], str], entry: _Entry, name: str) -> None:
        if key in self.entries:
            first = self.entries[key].source
            self.duplicates.append(f"{name!r} in {entry.source} (first defined in {first})")
            return
        self.entries[key] = entry
        self._by_key.setdefault(key[1], entry)

    def resolve(self, arg: str, category: Optional[str]) -> tuple[str, Optional[str], bool]:
        """MIG-2/3/5 - (phrase, category, hit). A hit's phrase is the source value, never the key;
        its category is the key's namespace or msgctxt unless the call passed one."""
        entry = self.entries.get((category, arg))
        if entry is None:
            entry = self.entries.get((None, arg)) if category is not None else self._by_key.get(arg)
        if entry is None:
            logger.debug(
                "langsys: %r is not a key in the legacy files; registering it as source text.", arg
            )
            return arg, category, False
        return entry.phrase, category if category is not None else entry.category, True

    def problems(self) -> list[str]:
        """What the listing reports: keys defined twice, and values registered verbatim."""
        return [f"duplicate key {d}" for d in self.duplicates] + [
            f"registered verbatim: {u}" for u in self.unrecognised
        ]


def main(argv: Optional[list[str]] = None) -> int:
    """`python -m langsys.migrate FILE[:FORMAT] ...` - load the legacy files as the client would,
    and name every key defined twice and every value that registers verbatim. Non-zero when any."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: python -m langsys.migrate FILE[:FORMAT] ...", file=sys.stderr)
        return 2
    files = []
    for arg in args:
        path, _, fmt = arg.partition(":") if not Path(arg).exists() else (arg, "", "")
        files.append(LegacyFile(path, fmt or None))
    try:
        keys = LegacyKeys(files)
    except ConfigurationError as exc:
        print(f"PROBLEM {exc}")
        return 1
    for problem in keys.problems():
        print(f"PROBLEM {problem}")
    print(f"{len(keys.entries)} key(s), {len(keys.problems())} problem(s)")
    return 1 if keys.problems() else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
