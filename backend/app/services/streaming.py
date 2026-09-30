"""Server-sent events for long agent runs.

The agent runs in a worker thread with a progress listener; this generator forwards each message as
`event: progress`, then one `event: done` (the saved session) or `event: error` ({status, detail, retry_after}).
Comment lines keep proxies from closing an idle connection. If the browser disconnects, the run still
finishes and is saved, so reloading the page shows the result.
"""
import contextvars
import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.agents.progress import listening
from app.errors import describe

log = logging.getLogger("app.stream")
KEEPALIVE_SECONDS = 15


def wants_stream(request: Request) -> bool:
    return "text/event-stream" in request.headers.get("accept", "")


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def stream_run(work: Callable[[], Any]) -> StreamingResponse:
    """Run `work()` (returns JSON-able data) and stream its progress, then its result or error."""
    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def target() -> None:
        try:
            with listening(lambda message: events.put(("progress", {"message": message}))):
                result = work()
            events.put(("done", result))
        except Exception as e:  # noqa: BLE001 (every failure becomes an error event)
            known = describe(e)
            if known is None:
                log.exception("Streaming run failed")
                status, body = 500, {"detail": "Something went wrong. Please try again."}
            else:
                status, body, _ = known
            events.put(("error", {"status": status, **body}))

    worker = threading.Thread(target=contextvars.copy_context().run, args=(target,), daemon=True)

    def body() -> Iterator[str]:
        worker.start()
        yield ": started\n\n"  # send headers now, so the browser shows progress immediately
        while True:
            try:
                event, data = events.get(timeout=KEEPALIVE_SECONDS)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield sse(event, data)
            if event != "progress":
                return

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
