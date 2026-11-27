"""Declared value sets (spec FRM-7): the finite translatable values an app shows in its sentences.

A status or a category is finite and known, so the app declares it, and a sentence that names one is
one phrase per value, registered whole with the value written in (`The order is Shipped.`),
because the translator needs the whole sentence to make it agree. A declaration names the
placeholder it fills; a placeholder is never linked to a set by matching names, so an undeclared
placeholder stays an ordinary one, and a value outside every set declared for its placeholder (a
user's name) is ordinary data.

Two kinds of declaration:

- **The contract**: anything with `translatable_values()` returning `{placeholder: display words}`,
  a class whose method is a class or static method, or an instance.
- **An `Enum` declared for a placeholder** with `@translates_as("status")`: its members are the
  values, each written in as its display word, `label()` (or a `label` attribute) where the enum
  defines one and its value otherwise. A slug value is then the word users read, so define a label.

Membership is read when it is asked, never cached, so a value added to a set since the last sync
counts at once. Finding the declarations by scanning an app's classes is a framework binding's; the
core reads the ones it is given.
"""

from __future__ import annotations

import enum
import itertools
import re
from collections.abc import Iterable, Mapping
from typing import Any, Callable, Optional, Protocol, Sequence, TypeVar, runtime_checkable

from ._log import logger

__all__ = ["TranslatableValues", "ValueSets", "translates_as"]

#: The attribute `translates_as` sets on an Enum class: the placeholder it fills.
PLACEHOLDER_ATTRIBUTE = "__langsys_placeholder__"

#: A flat placeholder, the only kind a value is written into; an ICU argument is not one.
_FLAT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

E = TypeVar("E", bound=type)


@runtime_checkable
class TranslatableValues(Protocol):
    """FRM-7's contract: the display words of each placeholder a declaration fills."""

    def translatable_values(self) -> Mapping[str, Iterable[str]]: ...


def translates_as(placeholder: str) -> Callable[[E], E]:
    """Declare an `Enum`'s members as the values of `placeholder` (FRM-7)."""

    def declare(cls: E) -> E:
        if not (isinstance(cls, type) and issubclass(cls, enum.Enum)):
            raise TypeError("translates_as() declares an Enum class")
        setattr(cls, PLACEHOLDER_ATTRIBUTE, placeholder)
        return cls

    return declare


def _display_word(member: enum.Enum) -> str:
    label = getattr(member, "label", None)
    if callable(label):
        label = label()
    if isinstance(label, str) and label:
        return label
    return str(member.value)


class ValueSets:
    """The declarations a client was given, read each time a value is asked about."""

    def __init__(self, declarations: Iterable[Any] = ()) -> None:
        self._declarations = list(declarations)

    def add(self, declaration: Any) -> None:
        self._declarations.append(declaration)

    def __bool__(self) -> bool:
        return bool(self._declarations)

    def _members(self, placeholder: str) -> list[tuple[Any, str]]:
        """(value, display word) for every value declared for `placeholder`, read now."""
        found: list[tuple[Any, str]] = []
        for declaration in self._declarations:
            if _is_enum(declaration):
                if getattr(declaration, PLACEHOLDER_ATTRIBUTE, None) == placeholder:
                    found.extend((member, _display_word(member)) for member in declaration)
                continue
            words = _provided(declaration).get(placeholder)
            if words is not None and not isinstance(words, str):
                found.extend((word, str(word)) for word in words)
        return found

    def words(self, placeholder: str) -> list[str]:
        """Every display word declared for `placeholder`, once each, in declaration order."""
        return list(dict.fromkeys(word for _, word in self._members(placeholder)))

    def placeholders(self) -> list[str]:
        """Every placeholder some declaration fills."""
        names: list[str] = []
        for declaration in self._declarations:
            if _is_enum(declaration):
                name = getattr(declaration, PLACEHOLDER_ATTRIBUTE, None)
                if isinstance(name, str):
                    names.append(name)
                continue
            names.extend(str(n) for n in _provided(declaration))
        return list(dict.fromkeys(names))

    def word_for(self, placeholder: str, value: Any) -> Optional[str]:
        """The display word `value` is written in as, when it belongs to a set declared for
        `placeholder`: a member of a declared enum, its value, or a declared display word. None for
        anything else, which stays an ordinary placeholder. Never raises on a value of another
        type."""
        for member, word in self._members(placeholder):
            if value is member or _same(value, member) or _same(value, word):
                return word
            if isinstance(member, enum.Enum) and _same(value, member.value):
                return word
        return None

    def write_in(
        self, template: str, params: Mapping[str, Any]
    ) -> Optional[tuple[str, dict[str, Any], list[str]]]:
        """The sentence `template` becomes when each declared value among `params` is written into
        its flat placeholder as its display word: (sentence, the params left, the placeholders
        written in). None when no param is a declared value of its placeholder."""
        words: dict[str, str] = {}
        for name in _placeholders(template):
            if name in params:
                word = self.word_for(name, params[name])
                if word is not None:
                    words[name] = word
        if not words:
            return None
        rest = {k: v for k, v in params.items() if k not in words}
        return _write(template, words), rest, list(words)

    def sentences(
        self, template: str, names: Optional[Sequence[str]] = None
    ) -> Optional[list[str]]:
        """Every written-in sentence of `template`, one per combination of the declared values of
        `names` (by default every declared placeholder it holds), the others left as they are.
        None when it holds no declared placeholder."""
        declared = [n for n in (names if names is not None else _placeholders(template))
                    if self.words(n)]
        if not declared:
            return None
        return [_write(template, dict(zip(declared, combo)))
                for combo in itertools.product(*(self.words(n) for n in declared))]


def _placeholders(template: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _FLAT.finditer(template)))


def _is_enum(declaration: Any) -> bool:
    return isinstance(declaration, type) and issubclass(declaration, enum.Enum)


def _provided(declaration: Any) -> Mapping[str, Any]:
    """What a contract declaration returns now; a declaration that fails declares nothing and
    says so, since a value set is read inside a render."""
    provide = getattr(declaration, "translatable_values", None)
    if not callable(provide):
        return {}
    try:
        found = provide()
    except Exception as exc:  # noqa: BLE001 - a render never throws for a declaration
        logger.warning("langsys: %r.translatable_values() raised %r; it declares nothing.",
                       declaration, exc)
        return {}
    return found if isinstance(found, Mapping) else {}


def _same(value: Any, candidate: Any) -> bool:
    """Equality between values of one type (any two strings counting as one), so `True` is never
    `1` and a string is never an int-backed member's value. Never raises."""
    if type(value) is not type(candidate) and not (
            isinstance(value, str) and isinstance(candidate, str)):
        return False
    try:
        return bool(value == candidate)
    except Exception:  # noqa: BLE001 - a value of any type is asked about
        return False


def _write(template: str, words: Mapping[str, str]) -> str:
    return _FLAT.sub(lambda m: words.get(m.group(1), m.group(0)), template)
