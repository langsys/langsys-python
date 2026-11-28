"""The core's share of a framework binding's sync command (spec FRM-2).

A server SDK implementing FRM registers what passes through the framework's translate function only
through its sync command, never while serving a request. The command itself (a Django management
command with `--watch` and `--strict`), finding the app's files and reading its templates are the
binding's. The core gives it:

- `scan_source()`: every call to the configured translate functions in a Python source file, read
  with `ast`, with its literal text, or reported with its file and line when its text is built at
  runtime. A key built at runtime inside a literal group (`_(f"validation.{key}")`) names its group.
- `Planner`: decides each phrase against the catalog, read fresh: already in it, in the
  framework's files (registered with their translations, MIG-9), or new. A phrase is converted by
  `migrate.call_phrase()`, the function `translate_legacy()` uses, so sync registers exactly what
  the running call looks up. `Planner.offline()` plans with no catalog and no key, for a CI gate.
- `LangsysClient.plan_sync()` and `apply_sync()`: the planner against the API, and the
  registration of what the catalog lacks.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from .locale import normalize_locale
from .messages import DEFAULT_LABEL_PLACEHOLDERS, TemplateList, TemplateProblem
from .migrate import LegacyFile, LegacyKeys, LegacyTranslations, call_phrase
from .text import strip_c0
from .types import UNCATEGORIZED
from .values import ValueSets

__all__ = ["DEFAULT_FUNCTIONS", "Hit", "PlanItem", "Planner", "SyncPlan", "scan_files",
           "scan_source"]

#: Each kind of translate function: the entry point it converts under (MIG-2) and its arguments.
LAYOUTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "gettext": ("gettext", ("message",)),
    "pgettext": ("gettext", ("context", "message")),
    "ngettext": ("ngettext", ("singular", "plural", "number")),
    "npgettext": ("ngettext", ("context", "singular", "plural", "number")),
    "t": ("t", ("phrase",)),
}

#: The gettext family, by the names Python and Django give it. A binding passes its own.
DEFAULT_FUNCTIONS: dict[str, str] = {
    "_": "gettext", "gettext": "gettext", "gettext_lazy": "gettext", "gettext_noop": "gettext",
    "pgettext": "pgettext", "pgettext_lazy": "pgettext",
    "ngettext": "ngettext", "ngettext_lazy": "ngettext",
    "npgettext": "npgettext", "npgettext_lazy": "npgettext",
}

#: A literal prefix that is a key group: dotted segments ending at the dot the runtime key follows.
_GROUP = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.\Z")

#: A framework placeholder MIG-2 converts to `{name}`: `%(name)s` and `{{ name }}`.
_LABEL_FORMS = re.compile(r"%\(([A-Za-z_]\w*)\)[sd]|\{\{\s*([A-Za-z_]\w*)\s*\}\}")

Files = Sequence[Union[str, Path, LegacyFile]]


@dataclass(frozen=True)
class Hit:
    """One call to a translate function in the source.

    `text` is the literal the call translates, or None when it is built at runtime (`reason` says
    how, and `group` names the literal key group a runtime key sits in, if any). `passed` is the
    names the call fills: a tuple, or None when the source does not spell them out, which counts
    every placeholder its text holds. `rich` marks a line printed raw (FRM-8).
    """

    path: str
    line: int
    function: str
    entry_point: str
    text: Optional[str]
    plural: Optional[str] = None
    category: Optional[str] = None
    passed: Optional[tuple[str, ...]] = ()
    group: Optional[str] = None
    reason: str = ""
    rich: bool = False

    @property
    def origin(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass
class PlanItem:
    """A phrase the plan decided: its category (None for none), the translations it carries from
    the framework's files, and every call or line it came from."""

    phrase: str
    category: Optional[str]
    translations: dict[str, str] = field(default_factory=dict)
    origins: list[str] = field(default_factory=list)

    def registration(self) -> dict[str, Any]:
        item: dict[str, Any] = {"phrase": self.phrase, "category": self.category}
        if self.translations:
            item["translations"] = dict(self.translations)
        return item


@dataclass
class SyncPlan:
    """What a sync would do, and what it reports."""

    offline: bool = False
    in_catalog: list[PlanItem] = field(default_factory=list)
    with_translations: list[PlanItem] = field(default_factory=list)
    new: list[PlanItem] = field(default_factory=list)
    #: Calls whose text is built at runtime outside a covered group: never registered.
    reported: list[Hit] = field(default_factory=list)
    #: Runtime keys inside a group whose lines register anyway.
    covered: list[Hit] = field(default_factory=list)
    #: Uncategorised sentences the message listing lists under the message category (MSG-7).
    via_message_listing: list[PlanItem] = field(default_factory=list)
    #: Lines holding a label placeholder: they register through the validation listing, per field.
    via_validation: list[PlanItem] = field(default_factory=list)
    #: The message listing's own problems (MSG-7), which `--strict` fails on too.
    problems: list[TemplateProblem] = field(default_factory=list)

    def fails_strict(self) -> bool:
        """`--strict`'s exit: any reported call or listing problem."""
        return bool(self.reported or self.problems)

    def registrations(self) -> list[dict[str, Any]]:
        """What applying the plan registers: every phrase the catalog lacks."""
        return [item.registration() for item in self.with_translations + self.new]


# -- the scanner ---------------------------------------------------------------------------------


def scan_files(paths: Iterable[Union[str, Path]], **options: Any) -> list[Hit]:
    """`scan_source()` over the files a binding found. Finding them is the binding's."""
    hits: list[Hit] = []
    for path in paths:
        hits.extend(scan_source(Path(path).read_text(encoding="utf-8"), str(path), **options))
    return hits


def scan_source(
    source: str,
    path: str = "<string>",
    *,
    functions: Optional[Mapping[str, str]] = None,
    rich_functions: Optional[Mapping[str, str]] = None,
) -> list[Hit]:
    """Every call in `source` to a configured translate function, in source order.

    `functions` maps a function's name to its kind (a key of `LAYOUTS`); a call counts whether it
    names the function or reaches it as an attribute (`translation.gettext(...)`). `rich_functions`
    names the functions whose result the binding prints raw, read the same way and marked rich.
    """
    tree = ast.parse(source, filename=path)
    plain = dict(DEFAULT_FUNCTIONS if functions is None else functions)
    rich = dict(rich_functions or {})
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    found: list[tuple[int, int, Hit]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node.func)
        if name is None or (name not in plain and name not in rich):
            continue
        is_rich = name in rich
        kind = rich[name] if is_rich else plain[name]
        hit = _read_call(node, name, kind, is_rich, parents.get(node), path)
        found.append((node.lineno, node.col_offset, hit))
    return [hit for _, _, hit in sorted(found, key=lambda f: (f[0], f[1]))]


def _callee(func: ast.expr) -> Optional[str]:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _read_call(
    call: ast.Call, name: str, kind: str, rich: bool, parent: Optional[ast.AST], path: str
) -> Hit:
    entry_point, layout = LAYOUTS[kind]

    def reported(reason: str, group: Optional[str] = None) -> Hit:
        return Hit(path, call.lineno, name, entry_point, None, reason=reason, group=group,
                   rich=rich)

    if any(isinstance(a, ast.Starred) for a in call.args) or any(
            k.arg is None for k in call.keywords):
        return reported("its arguments are unpacked at runtime")
    bound: dict[str, ast.expr] = dict(zip(layout, call.args))
    bound.update({k.arg: k.value for k in call.keywords if k.arg in layout})
    text_name = "phrase" if kind == "t" else ("singular" if "singular" in layout else "message")
    node = bound.get(text_name)
    if node is None:
        return reported("it is given no text")
    text = _literal(node)
    if text is None:
        group = _group(node)
        how = "its key is built at runtime" if group else (
            "its text is built at runtime" if _is_built(node) else "its argument is not a literal")
        return reported(how, group)
    plural: Optional[str] = None
    if "plural" in layout:
        plural_node = bound.get("plural")
        plural = _literal(plural_node) if plural_node is not None else None
        if plural is None:
            return reported("its plural form is not a literal")
    category: Optional[str] = None
    category_node = bound.get("context") if "context" in layout else _keyword(call, "category")
    if category_node is not None and not _is_none(category_node):
        category = _literal(category_node)
        if category is None:
            return reported("its category is not a literal")
    if kind == "t":
        passed = _mapping_keys(_keyword(call, "params"))
    else:
        passed = _percent_values(call, parent)
    return Hit(path, call.lineno, name, entry_point, text, plural=plural, category=category,
               passed=passed, rich=rich)


def _keyword(call: ast.Call, name: str) -> Optional[ast.expr]:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _literal(node: ast.expr) -> Optional[str]:
    """The string a node always evaluates to, or None: a literal, adjacent literals, literals
    joined with `+`, and an f-string with nothing to format."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts = [_literal(v) for v in node.values]
        return None if any(p is None for p in parts) else "".join(p or "" for p in parts)
    return None


def _is_built(node: ast.expr) -> bool:
    return isinstance(node, (ast.JoinedStr, ast.BinOp))


def _group(node: ast.expr) -> Optional[str]:
    """The literal key group a runtime key is built in, or None: `f"validation.{key}"` and
    `"validation." + key` are in `validation`; `f"Hello {name}"` is a sentence, not a key."""
    if isinstance(node, ast.JoinedStr):
        prefix = ""
        for value in node.values:
            text = _literal(value) if isinstance(value, ast.Constant) else None
            if text is None:
                break
            prefix += text
        else:
            return None
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        operands: list[ast.expr] = []

        def flatten(n: ast.expr) -> None:
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                flatten(n.left)
                flatten(n.right)
            else:
                operands.append(n)

        flatten(node)
        prefix = ""
        for operand in operands:
            text = _literal(operand)
            if text is None:
                break
            prefix += text
    else:
        return None
    return prefix[:-1] if _GROUP.match(prefix) else None


def _mapping_keys(node: Optional[ast.expr]) -> Optional[tuple[str, ...]]:
    """The names a params mapping fills: a dict literal's keys, none for no mapping, and None
    (every placeholder) for one the source does not spell out."""
    if node is None or _is_none(node):
        return ()
    if isinstance(node, ast.Dict):
        keys = [_literal(k) if k is not None else None for k in node.keys]
        if any(k is None for k in keys):
            return None
        return tuple(k for k in keys if k is not None)
    return None


def _percent_values(call: ast.Call, parent: Optional[ast.AST]) -> Optional[tuple[str, ...]]:
    """The names `%` fills on a gettext call's result, which a binding's lazy text looks up with:
    a mapping's keys, none for a tuple or a single value, none for a result used as it is."""
    if not (isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Mod)
            and parent.left is call):
        return ()
    values = parent.right
    if isinstance(values, (ast.Tuple, ast.Constant, ast.JoinedStr)):
        return ()
    return _mapping_keys(values)


# -- the planner ---------------------------------------------------------------------------------


class Planner:
    """FRM-2's rules, given the framework's files and the declared value sets.

    `legacy_files` are the source language's files (every line of them registers); `targets` maps
    each target locale to its files, whose lines are carried as translations (MIG-9).
    `covered_groups` names key groups registered another way, `listing` is the message listing
    (MSG-7), and `label_placeholders` the framework's label tokens.
    """

    def __init__(
        self,
        *,
        legacy_files: Optional[Files] = None,
        legacy: Optional[LegacyKeys] = None,
        targets: Optional[Mapping[str, Files]] = None,
        value_sets: Union[ValueSets, Iterable[Any], None] = None,
        covered_groups: Iterable[str] = (),
        listing: Optional[TemplateList] = None,
        label_placeholders: Sequence[str] = DEFAULT_LABEL_PLACEHOLDERS,
    ) -> None:
        self.legacy = legacy if legacy is not None else (
            LegacyKeys(legacy_files) if legacy_files else None)
        self.readers = [LegacyTranslations(normalize_locale(locale), files)
                        for locale, files in (targets or {}).items()]
        self.value_sets = value_sets if isinstance(value_sets, ValueSets) else ValueSets(
            value_sets or ())
        self.covered_groups = tuple(covered_groups)
        self.listing = listing
        self.label_placeholders = tuple(label_placeholders)

    @classmethod
    def offline(cls, hits: Iterable[Hit], **options: Any) -> SyncPlan:
        """A plan with no catalog and no key, for a CI gate: every phrase is new, and what a
        strict run fails on is reported as it would be."""
        return cls(**options).plan(hits, None)

    def plan(self, hits: Iterable[Hit], catalog: Optional[Mapping[str, Any]]) -> SyncPlan:
        """Decide every call and every source-language line against `catalog` (None: offline)."""
        plan = SyncPlan(offline=catalog is None)
        if self.listing is not None:
            plan.problems.extend(self.listing.problems)
        listed = set(self.listing.templates) if self.listing is not None else set()
        items: dict[tuple[str, str], PlanItem] = {}
        keys: dict[tuple[str, str], list[tuple[Optional[str], str]]] = {}
        aside: dict[tuple[str, str, str], PlanItem] = {}

        def put_aside(bucket: str, phrase: str, category: Optional[str], origin: str) -> None:
            item = aside.setdefault((bucket, category or UNCATEGORIZED, phrase),
                                    PlanItem(phrase, category))
            item.origins.append(origin)
            if len(item.origins) == 1:
                getattr(plan, bucket).append(item)

        def add(phrase: str, category: Optional[str], origin: str, raw: str,
                key: Optional[tuple[Optional[str], str]]) -> None:
            phrase = strip_c0(phrase)
            if not phrase.strip():
                return
            if self._holds_label(raw, phrase):
                put_aside("via_validation", phrase, category, origin)
                return
            sentences = self.value_sets.sentences(phrase) or [phrase]
            for sentence in sentences:
                if category is None and sentence in listed:
                    put_aside("via_message_listing", sentence, category, origin)
                    continue
                slot = (category or UNCATEGORIZED, sentence)
                item = items.setdefault(slot, PlanItem(sentence, category))
                item.origins.append(origin)
                if key is not None and sentence == phrase:
                    keys.setdefault(slot, []).append(key)

        for hit in hits:
            if hit.text is None:
                if hit.group is not None and self._covers(hit.group):
                    plan.covered.append(hit)
                else:
                    plan.reported.append(hit)
                continue
            phrase, category, key = call_phrase(
                hit.text, hit.entry_point, plural=hit.plural, category=hit.category,
                passed=hit.passed, legacy=self.legacy)
            add(phrase, category, hit.origin, hit.text + (hit.plural or ""), key)
        if self.legacy is not None:
            for key, entry in self.legacy.entries.items():
                add(entry.phrase, entry.category, f"{entry.source} key {key[1]!r}", entry.phrase,
                    key)

        for slot, item in items.items():
            for reader in self.readers:
                for key in keys.get(slot, []):
                    value = reader.get(key)
                    if value.translation is not None:
                        item.translations.setdefault(reader.locale, value.translation)
                        break
            held = catalog.get(slot[0]) if catalog is not None else None
            if isinstance(held, Mapping) and item.phrase in held:
                plan.in_catalog.append(item)
            elif item.translations:
                plan.with_translations.append(item)
            else:
                plan.new.append(item)
        return plan

    def _covers(self, group: str) -> bool:
        """A runtime key's group is covered when the source-language files hold its lines, which
        register anyway, or the binding names it (or a group it sits in) as registered another
        way."""
        for named in self.covered_groups:
            if group == named or group.startswith(named + "."):
                return True
        if self.legacy is None:
            return False
        prefix = group + "."
        return any(key[1].startswith(prefix) for key in self.legacy.entries)

    def _holds_label(self, raw: str, phrase: str) -> bool:
        """A line still holding the framework's label placeholder, as written or converted."""
        for placeholder in self.label_placeholders:
            if placeholder in raw or placeholder in phrase:
                return True
            converted = _converted(placeholder)
            if converted is not None and converted in phrase:
                return True
        return False


def _converted(placeholder: str) -> Optional[str]:
    """A label placeholder as MIG-2 converts it: `%(field_label)s` and `{{ field }}` are
    `{field_label}` and `{field}`."""
    match = _LABEL_FORMS.fullmatch(placeholder)
    if match is None:
        return None
    return "{" + (match.group(1) or match.group(2)) + "}"
