"""Live progress messages from inside an agent run ("Writing the draft", "Fix round 1: 3 problems").

Agents call `say(...)`; it does nothing unless a listener is active for the current context, so the agents
stay plain functions and the JSON endpoints are unchanged. The streaming endpoints set a listener with
`listening(...)` and forward each message to the browser as a server-sent event.
"""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_listener: ContextVar[Callable[[str], None] | None] = ContextVar("progress_listener", default=None)


def say(message: str) -> None:
    """Report one step to whoever is listening (no-op otherwise). Never lets a listener break the agent."""
    listener = _listener.get()
    if listener is None:
        return
    try:
        listener(message)
    except Exception:  # noqa: BLE001 (progress is best-effort)
        pass


@contextmanager
def listening(listener: Callable[[str], None]) -> Iterator[None]:
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)
