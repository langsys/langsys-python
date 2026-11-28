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

MIG-9's one-time import reads each target locale's files with `LegacyTranslations`: the same
formats and keys as the source files, each key's value converted the same way into the translation
of that key's source phrase. A value that is missing, empty, fuzzy, or does not convert is not a
translation. A gettext plural's ``msgstr[n]`` are indexed by the file's ``Plural-Forms``, so each
CLDR category of the locale takes the form its whole-number counts evaluate to under that
expression (`gettext_plural_categories`); an entry imports only when every category has such a
count, every count of one category takes one form, and every form is taken, and is otherwise left
for machine translation with a warning naming the file, the msgid and why.
"""

from __future__ import annotations

import gettext
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from babel import Locale, UnknownLocaleError

from ._log import logger
from .exceptions import ConfigurationError
from .text import strip_c0

__all__ = [
    "SUPPORTED_FORMATS",
    "LegacyFile",
    "LegacyKeys",
    "LegacyTranslations",
    "TargetValue",
    "convert_literal",
    "convert_value",
    "gettext_plural",
    "gettext_plural_categories",
]

#: MIG-7 - the formats this core's ecosystem uses. Others are refused at load, by name.
SUPPORTED_FORMATS = ("gettext", "plain")

_DEFAULT_FORMATS = {
    ".json": "plain", ".po": "gettext", ".mo": "gettext",
    ".php": "laravel", ".yml": "rails-i18n", ".yaml": "rails-i18n",
}

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
    """One configured source file, its declared format, and an optional per-file namespace (MIG-7):
    a file that holds one group's keys, as `locales/en/cart.json` holds `cart`'s, answers
    `cart.items.title` for its `items.title`."""

    path: Union[str, Path]
    format: Optional[str] = None
    namespace: Optional[str] = None


@dataclass
class _Entry:
    phrase: str
    category: Optional[str]
    source: str
    recognised: bool = True


def _namespace(key: str) -> Optional[str]:
    """MIG-5 - a dotted key's leading segment, when the key is dotted: `checkout.submit` is
    `checkout`, while a sentence that ends in a full stop (`Welcome back.`) has none."""
    head, dot, rest = key.partition(".")
    if not dot or not head or not rest or any(c.isspace() for c in head):
        return None
    return head


def _warn(message: str, *args: Any) -> None:
    logger.warning("langsys: " + message, *args)


def _unconvertible(text: str) -> Optional[re.Match[str]]:
    return (
        _PY_FORMATTED.search(text)
        or _COLON_CASED.search(text)
        or _PY_POSITIONAL.search(_PY_NAMED.sub("", text.replace("%%", "")))
    )


def convert_value(text: str, *, where: str = "") -> str:
    """MIG-4 - a file value's placeholders, converted the same way whatever its format."""
    unconvertible = _unconvertible(text)
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


def call_phrase(
    text: str,
    entry_point: str = "gettext",
    *,
    plural: Optional[str] = None,
    category: Optional[str] = None,
    passed: Optional[Iterable[str]] = (),
    legacy: Optional["LegacyKeys"] = None,
) -> tuple[str, Optional[str], Optional[tuple[Optional[str], str]]]:
    """The phrase and category a translate-function call looks up, and the legacy entry it hit.

    One conversion for the running call (`translate_legacy()`, `translate()`) and for sync (FRM-2),
    so sync registers exactly the phrase the runtime looks up. With legacy files the text is a key
    first (MIG-2); otherwise it is literal source text, converted under its entry point's syntax for
    the names the call fills. `passed=None` is a call whose values the source does not spell out:
    every placeholder the text holds counts as filled.
    """
    if legacy is not None:
        phrase, resolved, hit = legacy.resolve(text, category)
        if hit:
            return phrase, resolved, legacy.entry_key(text, category)
    if passed is None:
        names = _BRACED if entry_point == "blocktranslate" else _PY_NAMED
        passed = [m.group(1) for part in (text, plural or "") for m in names.finditer(part)]
    if entry_point == "ngettext":
        if plural is None:
            raise ValueError("an ngettext call needs its plural form")
        return gettext_plural(text, plural, literal=True, passed=passed), category, None
    return convert_literal(text, entry_point, passed), category, None


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


def _format_of(spec: LegacyFile) -> tuple[Path, str]:
    """MIG-7 - a configured file's path and format, refusing a format this core does not read."""
    path = Path(spec.path)
    suffix = path.suffix.lower()
    # MIG-7's defaults by file type: a PHP array is Laravel's, a YAML file Rails', a `.po`
    # gettext, and undeclared JSON plain. Naming the format lets a refusal say which it is.
    fmt = spec.format or _DEFAULT_FORMATS.get(suffix)
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
    return path, str(fmt)


class LegacyKeys:
    """The configured source files, loaded once: key -> (phrase, category), first file wins."""

    def __init__(self, files: Sequence[Union[str, Path, LegacyFile]]) -> None:
        self.entries: dict[tuple[Optional[str], str], _Entry] = {}
        #: Each key's first entry under any context, for a call that names no category.
        self._by_key: dict[str, _Entry] = {}
        self._first_key: dict[str, tuple[Optional[str], str]] = {}
        self.duplicates: list[str] = []
        self.unrecognised: list[str] = []
        for item in files:
            spec = item if isinstance(item, LegacyFile) else LegacyFile(item)
            self._load(spec)

    # MIG-7
    def _load(self, spec: LegacyFile) -> None:
        path, fmt = _format_of(spec)
        text = path.read_text(encoding="utf-8")
        if fmt == "plain":
            flat: dict[str, str] = {}
            _flatten(json.loads(text), "", flat, str(path))
            for key, value in flat.items():
                if spec.namespace:
                    key = f"{spec.namespace}.{key}"
                where = f"{path} key {key!r}"
                if "|" in value:
                    _warn("%s: a '|' in a plain file is text, not a plural; registered "
                          "verbatim.", where)
                    self.unrecognised.append(where)
                    phrase, recognised = value, False
                else:
                    phrase = convert_value(value, where=where)
                    recognised = phrase != value or not _unconvertible(value)
                category = spec.namespace or _namespace(key)
                self._add((None, key), _Entry(phrase, category, str(spec.path), recognised), key)
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
                self._add((context, msgid), _Entry(phrase, context, str(spec.path)), msgid)

    def _add(self, key: tuple[Optional[str], str], entry: _Entry, name: str) -> None:
        if key in self.entries:
            first = self.entries[key].source
            self.duplicates.append(f"{name!r} in {entry.source} (first defined in {first})")
            return
        self.entries[key] = entry
        self._by_key.setdefault(key[1], entry)
        self._first_key.setdefault(key[1], key)

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

    def entry_key(
        self, arg: str, category: Optional[str] = None
    ) -> Optional[tuple[Optional[str], str]]:
        """The `(context, key)` that `resolve()` answers `arg` from, or None for a miss."""
        if (category, arg) in self.entries:
            return (category, arg)
        if category is not None:
            return (None, arg) if (None, arg) in self.entries else None
        return self._first_key.get(arg)

    def lookup(self, arg: str, category: Optional[str] = None) -> Optional[dict[str, Any]]:
        """A hit as the shared vectors describe it - phrase, category, the answering file, and
        whether the value converted - or None for the MIG-2 miss."""
        phrase, resolved, hit = self.resolve(arg, category)
        if not hit:
            return None
        entry = (
            self.entries.get((category, arg)) or self.entries.get((None, arg)) or self._by_key[arg]
        )
        return {"phrase": phrase, "category": resolved, "file": entry.source,
                "recognised": entry.recognised}

    def problems(self) -> list[str]:
        """What the listing reports: keys defined twice, and values registered verbatim."""
        return [f"duplicate key {d}" for d in self.duplicates] + [
            f"registered verbatim: {u}" for u in self.unrecognised
        ]


# -- MIG-9: a target locale's files ----------------------------------------------------------------

#: The whole numbers each CLDR category's counts are drawn from: every count to 1100, which holds
#: every category's small and repeating cases, and the powers of ten that reach the categories
#: CLDR keeps for large round numbers (French and Spanish `many` start at a million).
_WHOLE_COUNTS = tuple(range(1101)) + tuple(10**k for k in range(4, 8)) + (2 * 10**6, 3 * 10**6)
_CLDR_ORDER = ("zero", "one", "two", "few", "many", "other")
_PLURAL_FORMS = re.compile(r"nplurals\s*=\s*(\d+)\s*;\s*plural\s*=\s*([^;]+)")


def _plural_rule(locale: str) -> Any:
    """Babel's CLDR plural rule for `locale`, falling back to its language; None when unknown."""
    tag = locale.replace("-", "_")
    for candidate in (tag, tag.split("_", 1)[0]):
        try:
            return Locale.parse(candidate).plural_form
        except (ValueError, UnknownLocaleError):
            continue
    return None


def gettext_plural_categories(
    header: Optional[str], locale: str
) -> tuple[Optional[dict[str, int]], str]:
    """MIG-9 - each CLDR plural category of `locale`, mapped to the ``msgstr`` index its
    whole-number counts take under a `.po` file's ``Plural-Forms`` header, or None and why not.

    A gettext count is a whole number, so a category CLDR reaches only with fractions (Russian
    `other`, Czech `many`) has no form of its own, and the entry cannot be imported without
    guessing one. Two categories may take one form (French `many` and `other` both take
    ``msgstr[1]``); every form must be taken by some category.
    """
    match = _PLURAL_FORMS.search(header or "")
    if match is None:
        return None, "the file declares no Plural-Forms"
    nplurals, expression = int(match.group(1)), match.group(2).strip()
    try:
        plural = gettext.c2py(expression)
    except (ValueError, SyntaxError) as exc:
        return None, f"its Plural-Forms expression {expression!r} does not parse ({exc})"
    rule = _plural_rule(locale)
    if rule is None:
        return None, f"CLDR holds no plural rules for {locale}"
    forms: dict[str, set[int]] = {category: set() for category in set(rule.tags) | {"other"}}
    for count in _WHOLE_COUNTS:
        forms[rule(count)].add(int(plural(count)))
    mapping: dict[str, int] = {}
    for category in _CLDR_ORDER:
        if category not in forms:
            continue
        taken = forms[category]
        if not taken:
            return None, (f"CLDR's {category!r} has no whole-number count in {locale}, so no "
                          f"gettext form is its")
        if len(taken) > 1:
            return None, (f"counts CLDR calls {category!r} take gettext forms "
                          f"{sorted(taken)}")
        (index,) = taken
        if index >= nplurals:
            return None, f"its Plural-Forms gives form {index} although nplurals is {nplurals}"
        mapping[category] = index
    unused = sorted(set(range(nplurals)) - set(mapping.values()))
    if unused:
        return None, f"no CLDR category of {locale} takes msgstr[{unused[0]}]"
    return mapping, ""


#: MIG-9 - why a target file's value is not a translation of its key.
SKIP_MISSING, SKIP_EMPTY, SKIP_FUZZY = "missing", "empty", "fuzzy"
SKIP_NOT_CONVERTED, SKIP_PLURAL_FORMS = "not-converted", "plural-forms"


@dataclass(frozen=True)
class TargetValue:
    """A target file's value for one key: its translation, or the reason it is not one."""

    translation: Optional[str]
    reason: Optional[str] = None
    detail: str = ""


def _translation(text: str) -> TargetValue:
    """A single value as a translation, converted as the source's values are."""
    if not strip_c0(text).strip():
        return TargetValue(None, SKIP_EMPTY)
    found = _unconvertible(text)
    if found:
        return TargetValue(None, SKIP_NOT_CONVERTED, _cannot_convert(found))
    return TargetValue(strip_c0(convert_value(text)))


def _cannot_convert(found: re.Match[str]) -> str:
    return f"{found.group(0)!r} cannot be written as {{name}}"


class LegacyTranslations:
    """One target locale's files, read for MIG-9's import: each key, keyed as `LegacyKeys` keys
    the source files, with its value as a translation or the reason it is not one."""

    def __init__(self, locale: str, files: Sequence[Union[str, Path, LegacyFile]]) -> None:
        self.locale = locale
        self.values: dict[tuple[Optional[str], str], TargetValue] = {}
        for item in files:
            spec = item if isinstance(item, LegacyFile) else LegacyFile(item)
            self._load(spec)

    def get(self, key: tuple[Optional[str], str]) -> TargetValue:
        return self.values.get(key, TargetValue(None, SKIP_MISSING))

    def _put(self, key: tuple[Optional[str], str], value: TargetValue) -> None:
        self.values.setdefault(key, value)  # the first file wins, as for the source files

    def _load(self, spec: LegacyFile) -> None:
        path, fmt = _format_of(spec)
        text = path.read_text(encoding="utf-8")
        if fmt == "plain":
            flat: dict[str, str] = {}
            _flatten(json.loads(text), "", flat, str(path))
            for key, value in flat.items():
                if spec.namespace:
                    key = f"{spec.namespace}.{key}"
                if "|" in value:
                    self._put((None, key), TargetValue(
                        None, SKIP_NOT_CONVERTED, "a '|' in a plain file is text, not a plural"))
                else:
                    self._put((None, key), _translation(value))
            return
        entries = _parse_po(text)
        header = next((e.get("msgstr", "") for e in entries if e["msgid"] == ""), None)
        mapped: Optional[tuple[Optional[dict[str, int]], str]] = None  # one per file
        for entry in entries:
            msgid = entry["msgid"]
            if not msgid:
                continue
            entry_key = (entry.get("msgctxt") or None, msgid)
            if entry.get("fuzzy"):
                self._put(entry_key, TargetValue(None, SKIP_FUZZY))
            elif entry.get("msgid_plural") is None:
                self._put(entry_key, _translation(entry.get("msgstr", "")))
            else:
                if mapped is None:
                    mapped = gettext_plural_categories(header, self.locale)
                self._put(entry_key, self._plural(entry, mapped, f"{path} msgid {msgid!r}"))

    def _plural(
        self, entry: dict[str, Any], mapped: tuple[Optional[dict[str, int]], str], where: str
    ) -> TargetValue:
        forms: list[str] = []
        while f"msgstr[{len(forms)}]" in entry:
            forms.append(entry[f"msgstr[{len(forms)}]"])
        if not forms or any(not strip_c0(form).strip() for form in forms):
            return TargetValue(None, SKIP_EMPTY)
        branches: list[str] = []
        for form in forms:
            text = re.sub(r"%\(count\)[sd]", "#", form)
            found = _unconvertible(text)
            if found:
                return TargetValue(None, SKIP_NOT_CONVERTED, _cannot_convert(found))
            branches.append(convert_value(text))
        mapping, why = mapped
        if mapping is not None and len(forms) != len(set(mapping.values())):
            why = (f"the entry has {len(forms)} forms and its Plural-Forms declares "
                   f"{len(set(mapping.values()))}")
            mapping = None
        if mapping is None:
            _warn("%s: its plural forms were not imported for %s, because %s; left for machine "
                  "translation.", where, self.locale, why)
            return TargetValue(None, SKIP_PLURAL_FORMS, why)
        icu = " ".join(f"{category} {{{branches[index]}}}" for category, index in mapping.items())
        return TargetValue(strip_c0("{count, plural, " + icu + "}"))


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
