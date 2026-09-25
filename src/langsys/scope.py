"""Request scopes: misses recorded while serving a request wait for that request's response.

SRV-3 - misses discovered during a server render are collected after the response has been
flushed, never on the request path. The client's queue is process-wide and its debounce is a
timer, so neither alone can know where a request's response stands. A binding therefore marks
the request:

    scope = langsys.begin_request_scope()      # at request start
    ...                                         # render; misses are tagged with the scope
    langsys.end_request_scope(scope)            # once the response is out

or `with langsys.request_scope(): ...`. A miss recorded while a scope is open is sent by no flush -
the debounce timer, an explicit `flush_pending()`, or another request's - until a scope that
recorded it has ended. Misses recorded outside any scope (a worker, a management command, a
script) keep the debounce unchanged. The shutdown flush releases everything, so a scope that is
never ended holds its misses until the process exits.

The open scope is found through a `ContextVar`, so each thread and each asyncio task sees its own
request, and a client built lazily part-way through a request joins it: misses are tagged with the
ambient scope when they are recorded, not with anything the client holds.
"""

from __future__ import annotations

import contextlib
import itertools
import threading
import weakref
from collections.abc import Iterator
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .client import LangsysClient

__all__ = ["RequestScope", "begin_request_scope", "end_request_scope", "request_scope"]

_ids = itertools.count(1)
_CURRENT: ContextVar[Optional["RequestScope"]] = ContextVar("langsys_request_scope", default=None)


class RequestScope:
    """One request's scope. Opaque to callers: pass it back to `end_request_scope`."""

    __slots__ = ("id", "_ended", "_token", "_clients", "_lock", "__weakref__")

    def __init__(self) -> None:
        self.id = next(_ids)
        self._ended = False
        self._token: Optional[Token[Optional[RequestScope]]] = None
        self._clients: weakref.WeakSet[LangsysClient] = weakref.WeakSet()
        self._lock = threading.Lock()

    @property
    def ended(self) -> bool:
        return self._ended

    def _joined_by(self, client: LangsysClient) -> None:
        with self._lock:
            self._clients.add(client)

    def __repr__(self) -> str:
        return f"<RequestScope {self.id} {'ended' if self._ended else 'open'}>"


def current_scope() -> Optional[RequestScope]:
    """The scope open in this thread or task, if any."""
    scope = _CURRENT.get()
    return None if scope is None or scope.ended else scope


def begin_request_scope() -> RequestScope:
    """Open a scope for the request being served in this thread or task."""
    scope = RequestScope()
    scope._token = _CURRENT.set(scope)
    return scope


def end_request_scope(scope: RequestScope) -> None:
    """Close `scope` once the response is out, releasing its misses to the next flush.

    Takes the handle explicitly, so it may be called from a different task than the one that
    began the scope - ASGI middleware often sends the response from one. Idempotent.
    """
    with scope._lock:
        if scope._ended:
            return
        scope._ended = True
        clients = list(scope._clients)
    token, scope._token = scope._token, None
    if token is not None:
        with contextlib.suppress(ValueError):  # ended from another context
            _CURRENT.reset(token)
    for client in clients:
        client._scope_released()


@contextlib.contextmanager
def request_scope() -> Iterator[RequestScope]:
    """`with request_scope():` - begin on entry, end on exit, exceptions included."""
    scope = begin_request_scope()
    try:
        yield scope
    finally:
        end_request_scope(scope)
