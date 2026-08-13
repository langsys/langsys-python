"""Reactive primitives — the binding point framework wrappers hook into.

The base SDK is framework-agnostic, but a Django / Flask / FastAPI wrapper needs a
clean, synchronous way to (a) supply the current user locale and (b) react when the
loaded catalog or locale changes. That's exactly what the TS base SDK exposes as
"signals" for Angular/Vue to wrap; :class:`Signal` and :class:`LocaleSource` are the
Python analogs, deliberately tiny and dependency-free.
"""

from __future__ import annotations

import contextlib
from typing import Callable, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")
Unsubscribe = Callable[[], None]


@runtime_checkable
class LocaleSource(Protocol):
    """The minimum contract the client reads the user locale from.

    A wrapper can hand its own object (a request-scoped store, a settings signal)
    as long as it implements this. The SDK only ever *reads* and *subscribes* — it
    never writes the source. :class:`Signal` satisfies this Protocol.
    """

    def get(self) -> str:
        """Return the current locale (e.g. ``"en-US"``)."""
        ...

    def subscribe(self, callback: Callable[[str], None]) -> Unsubscribe:
        """Register ``callback``; it is called immediately with the current value and
        again on every change. Returns a function that unsubscribes."""
        ...


class Signal(Generic[T]):
    """A minimal synchronous observable.

    Semantics match the other Langsys SDKs' signal contract:

    * ``subscribe`` fires the callback **immediately** with the current value.
    * ``set`` is a no-op when the new value is identical (``is``) to the current one,
      otherwise it notifies every subscriber synchronously.
    """

    def __init__(self, initial: T) -> None:
        self._value = initial
        self._subscribers: list[Callable[[T], None]] = []

    def get(self) -> T:
        return self._value

    def set(self, value: T) -> None:
        if value is self._value or value == self._value:
            return
        self._value = value
        for callback in list(self._subscribers):
            callback(value)

    def update(self, fn: Callable[[T], T]) -> None:
        self.set(fn(self._value))

    def subscribe(self, callback: Callable[[T], None]) -> Unsubscribe:
        self._subscribers.append(callback)
        callback(self._value)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(callback)

        return unsubscribe
